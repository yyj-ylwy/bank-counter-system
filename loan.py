"""贷款业务子系统 UC-201 ~ UC-206（参与者：贷款业务员）。"""
from datetime import timedelta

from flask import Blueprint, request, g

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, oid, now, as_int, norm_id, check_identity,
    find_customer, check_account, write_txn, write_audit,
    get_param, get_param_dec, customer_view, txn_view, parse_date_range, new_contract_no,
)

bp = Blueprint("loan", __name__, url_prefix="/api/loan")
clerk = require_role(C.ROLE_LOAN)


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


def add_months(dt, months):
    """给日期加 months 个月（简化，用于估算到期日）。"""
    y, mth = dt.year, dt.month - 1 + int(months)
    y += mth // 12
    mth = mth % 12 + 1
    day = min(dt.day, 28)
    return dt.replace(year=y, month=mth, day=day)


def accrue_penalty(loan, raw_overdue_rate):
    """逾期罚息增量计提：按『当前剩余本金 × 日罚息率 × 自上次计提以来的天数(按日历日)』累加到未缴罚息。
    返回 (应收未缴罚息 Decimal, 计提截止时间)。纯计算不写库；逾期列表与还款共用同一口径，避免毛/净不一致。"""
    principal = dec(loan["balance"])
    penalty_due = dec(loan.get("penalty_due", 0))
    due = loan.get("due_date")
    asof = loan.get("penalty_asof") or due
    today = now()
    if due and principal > 0 and asof and raw_overdue_rate is not None and today.date() > asof.date():
        start = asof if asof.date() >= due.date() else due
        days = (today.date() - start.date()).days  # 日历日粒度，避免放款时分秒导致 off-by-one
        if days > 0:
            penalty_due += D(principal * dec(raw_overdue_rate) * days)
            asof = today
    return D(penalty_due), (asof or due)


def loan_view(db, ln, with_customer=True):
    v = {
        "id": str(ln["_id"]),
        "contract_no": ln["contract_no"],
        "loan_type": ln.get("loan_type"),
        "amount": float(dec(ln.get("amount"))),
        "balance": float(dec(ln.get("balance"))),
        "penalty_due": float(dec(ln.get("penalty_due", 0))),  # 应收未缴逾期罚息
        "interest_rate": float(dec(ln.get("interest_rate"))) if ln.get("interest_rate") is not None else None,
        "term_months": ln.get("term_months"),
        "status": ln["status"],
        "status_label": C.LOAN_STATUS_LABEL.get(ln["status"], ln["status"]),
        "purpose": ln.get("purpose"),
        "guarantee": ln.get("guarantee"),
        "repay_method": ln.get("repay_method"),
        "reject_reason": ln.get("reject_reason"),
        "supplement_note": ln.get("supplement_note"),
        "due_date": ln["due_date"].strftime("%Y-%m-%d") if ln.get("due_date") else None,
        "repay_log": ln.get("repay_log") or [],
        "collection_log": ln.get("collection_log") or [],   # UC-205 催收记录，供查询/展示
        "created_at": ln["created_at"].strftime("%Y-%m-%d %H:%M:%S") if ln.get("created_at") else None,
    }
    if with_customer:
        cust = db.customer.find_one({"_id": ln["customer_id"]})
        v["customer"] = customer_view(cust)
        acc = db.account.find_one({"_id": ln.get("account_id")}) if ln.get("account_id") else None
        v["account_no"] = acc["account_no"] if acc else None
    return v


# ---------- UC-201 贷款申请办理 ----------
@bp.post("/apply")
@clerk
def apply():
    d = _body()
    db = get_db()
    cust = find_customer(db, ident=(d.get("ident") or d.get("id_no") or d.get("customer_no") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户，请先核对客户信息")

    loan_type = (d.get("loan_type") or "").strip()
    amount = D(d.get("amount") or 0)
    term = as_int(d.get("term_months"))
    account_no = (d.get("account_no") or "").strip()
    if loan_type not in C.LOAN_TYPES:  # E-2 类型非法
        return fail("E-2", "贷款类型非法")
    if amount <= 0 or amount > C.LOAN_AMOUNT_MAX:  # E-2 金额范围
        return fail("E-2", f"申请金额须大于 0 且不超过 {C.LOAN_AMOUNT_MAX:,}")
    if term <= 0 or term > C.LOAN_TERM_MAX:  # E-2 期限范围（防到期日计算溢出）
        return fail("E-2", f"期限须为 1~{C.LOAN_TERM_MAX} 个月")

    # E-1 黑名单 / 严重逾期
    if cust["status"] == C.CUSTOMER_BLACKLIST:
        return fail("E-1", "客户处于黑名单，拒绝办理")
    # 逾期 = 已标记 OVERDUE，或 ACTIVE 但已过到期日且未还清（用今日零点，与还款/罚息的日历日口径一致）
    today0 = now().replace(hour=0, minute=0, second=0, microsecond=0)
    if db.loan.count_documents({"customer_id": cust["_id"], "$or": [
            {"status": C.LOAN_OVERDUE},
            {"status": C.LOAN_ACTIVE, "due_date": {"$lt": today0}, "balance": {"$gt": m(0)}}]}):
        return fail("E-1", "客户存在逾期贷款，拒绝办理")

    if account_no:  # 指定账户必须属于本客户且状态正常，杜绝把贷款打进他人账户
        acc = db.account.find_one({"account_no": account_no, "customer_id": cust["_id"], "status": C.ACCOUNT_NORMAL})
    else:
        acc = db.account.find_one({"customer_id": cust["_id"], "status": C.ACCOUNT_NORMAL})
    if not acc:
        return fail("E-NOACC", "客户没有可用的正常储蓄账户，或指定账户不属于该客户")

    ln = {
        "contract_no": new_contract_no(),
        "customer_id": cust["_id"],
        "account_id": acc["_id"],
        "user_id": g.user["_id"],
        "loan_type": loan_type,
        "amount": m(amount),
        "balance": m(0),
        "interest_rate": None,
        "term_months": term,
        "status": C.LOAN_PENDING,
        "purpose": (d.get("purpose") or "").strip(),
        "guarantee": (d.get("guarantee") or "").strip(),
        "due_date": None,
        "repay_log": [],
        "created_at": now(),
    }
    ln["_id"] = db.loan.insert_one(ln).inserted_id
    write_audit(db, user_id=g.user["_id"], action="LOAN_APPLY", object_type="loan",
                object_id=ln["contract_no"], result=C.RESULT_SUCCESS,
                detail={"amount": str(amount), "type": loan_type})
    return ok({"loan": loan_view(db, ln)}, "贷款申请已提交，状态：待审核")


# ---------- UC-202 审核与审批 ----------
@bp.post("/approve")
@clerk
def approve():
    d = _body()
    contract_no = (d.get("contract_no") or "").strip()
    decision = (d.get("decision") or "").strip().upper()  # APPROVED / REJECTED / SUPPLEMENT
    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款申请")
    # 待审核(PENDING)与待补件(SUPPLEMENT)都允许再次审批，避免补件后合同号被永久卡死
    if ln["status"] not in (C.LOAN_PENDING, C.LOAN_SUPPLEMENT):  # E-1 已被处理
        return fail("E-1", f"该申请已被处理（当前：{C.LOAN_STATUS_LABEL.get(ln['status'])}），请刷新")

    updates = {}
    if decision == "APPROVED":
        # 用 not in (None, "") 判定“是否填写”，避免显式传入的 0 被 `x or default` 静默替换成原申请值而绕过校验
        appr_amt = D(d["approved_amount"]) if d.get("approved_amount") not in (None, "") else D(ln["amount"])
        # 利率不能用 D()（只保留 2 位会把 0.0435 截成 0.04）；用 dec() 保留精度，存库时 D6_rate 再定 4 位
        rate = dec(d["interest_rate"]) if d.get("interest_rate") not in (None, "") \
            else get_param_dec(db, C.P_LOAN_RATE, "0.0435")
        term = as_int(d["term_months"]) if d.get("term_months") not in (None, "") else int(ln["term_months"])
        if appr_amt <= 0 or appr_amt > C.LOAN_AMOUNT_MAX:
            return fail("E-VAL", f"批准金额须大于 0 且不超过 {C.LOAN_AMOUNT_MAX:,}", 400)
        if rate < 0 or rate > 1:  # 年利率为小数，上限 1（100%），防误填成 4.35 这类整数
            return fail("E-VAL", "年利率应为 0~1 之间的小数（如 0.0435 表示 4.35%）", 400)
        if term <= 0 or term > C.LOAN_TERM_MAX:
            return fail("E-VAL", f"期限须为 1~{C.LOAN_TERM_MAX} 个月", 400)
        updates = {"status": C.LOAN_APPROVED, "amount": m(appr_amt),
                   "interest_rate": D6_rate(rate), "term_months": term,
                   "repay_method": (d.get("repay_method") or "等额本息").strip()}
    elif decision == "REJECTED":
        updates = {"status": C.LOAN_REJECTED, "reject_reason": (d.get("reason") or "").strip()}
    elif decision == "SUPPLEMENT":
        updates = {"status": C.LOAN_SUPPLEMENT, "supplement_note": (d.get("reason") or "").strip()}
    else:
        return fail("E-OP", "审批结论非法（APPROVED/REJECTED/SUPPLEMENT）", 400)

    # 条件更新(CAS)：仅当仍为待审核/待补件才写，防并发/双击造成双重审批、矛盾终态
    res = db.loan.update_one({"_id": ln["_id"], "status": {"$in": [C.LOAN_PENDING, C.LOAN_SUPPLEMENT]}},
                             {"$set": updates})
    if res.matched_count == 0:
        return fail("E-1", "该申请已被处理（请刷新后重试）")
    write_audit(db, user_id=g.user["_id"], action="LOAN_APPROVE", object_type="loan",
                object_id=contract_no, result=C.RESULT_SUCCESS, detail={"decision": decision})
    ln.update(updates)
    return ok({"loan": loan_view(db, ln)}, f"审批完成：{C.LOAN_STATUS_LABEL.get(updates['status'])}")


def D6_rate(rate):
    """利率存 4 位小数（DECIMAL(6,4)）。"""
    from bson.decimal128 import Decimal128
    from decimal import Decimal, ROUND_HALF_UP
    return Decimal128(Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


# ---------- UC-203 放款处理 ----------
@bp.post("/disburse")
@clerk
def disburse():
    d = _body()
    contract_no = (d.get("contract_no") or "").strip()
    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款")
    if ln["status"] == C.LOAN_ACTIVE:  # E-2 重复放款
        return fail("E-2", "该贷款已放款，不可重复操作")
    if ln["status"] != C.LOAN_APPROVED:
        return fail("E-STATE", f"贷款状态为「{C.LOAN_STATUS_LABEL.get(ln['status'])}」，不可放款")

    def txn(s):
        loan = db.loan.find_one({"_id": ln["_id"]}, session=s)  # 事务内重读，避免并发重复放款
        if loan["status"] == C.LOAN_ACTIVE:
            return ("E-2", "该贷款已放款，不可重复操作")
        if loan["status"] != C.LOAN_APPROVED:
            return ("E-STATE", f"贷款状态为「{C.LOAN_STATUS_LABEL.get(loan['status'])}」，不可放款")
        acc, err = check_account(db, _acc_no(db, loan), session=s)  # E-1 收款账户无效
        if err:
            return ("E-1", f"收款账户不可用：{err[1]}")
        amount = dec(loan["amount"])
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) + amount)}}, session=s)
        due = add_months(now(), loan["term_months"]).replace(hour=0, minute=0, second=0, microsecond=0)  # 日历到期日
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"status": C.LOAN_ACTIVE, "balance": m(amount),
                                     "due_date": due, "disbursed_at": now(),
                                     "penalty_due": m(0), "penalty_asof": due}}, session=s)
        write_txn(db, business_type=C.TXN_LOAN_DISBURSE, amount=amount, user_id=g.user["_id"],
                  customer_id=loan["customer_id"], account_id=acc["_id"], related_id=loan["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_LOAN_DISBURSE, object_type="loan",
                    object_id=contract_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount)}, session=s)
        return None

    err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"loan": loan_view(db, db.loan.find_one({"_id": ln["_id"]}))}, "放款成功")


def _acc_no(db, ln):
    acc = db.account.find_one({"_id": ln["account_id"]})
    return acc["account_no"] if acc else ""


# ---------- UC-204 还款登记 ----------
@bp.post("/repay")
@clerk
def repay():
    d = _body()
    contract_no = (d.get("contract_no") or "").strip()
    amount = D(d.get("amount") or 0)
    account_no = (d.get("account_no") or "").strip()
    ident = (d.get("ident") or d.get("id_no") or "").strip()  # 邮箱或证件号，任一即可核验
    if amount <= 0:
        return fail("E-AMT", "还款金额必须大于零", 400)

    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款")
    repay_no = account_no or _acc_no(db, ln)
    # 逾期罚息日利率（可能未维护）：仅在贷款确实逾期时才要求存在，避免正常还款被参数缺失阻断
    raw_overdue_rate = get_param(db, C.P_LOAN_OVERDUE_RATE)

    def txn(s):
        loan = db.loan.find_one({"_id": ln["_id"]}, session=s)  # 事务内重读，读改写一致
        if loan["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):  # E-2 已结清
            return None, ("E-2", f"贷款当前为「{C.LOAN_STATUS_LABEL.get(loan['status'])}」，无需还款")
        principal = dec(loan["balance"])
        due = loan.get("due_date")
        if due and now().date() > due.date() and principal > 0 and raw_overdue_rate is None:
            return None, ("E-3", "缺少逾期罚息参数，请管理员先维护 LOAN_OVERDUE_RATE")
        # 增量计提未缴罚息（累加自上次计提以来的天数），罚息单列不并入本金
        penalty, new_asof = accrue_penalty(loan, raw_overdue_rate)
        total_due = principal + penalty
        if amount > total_due:  # E-3 超额（本金 + 应收罚息）
            return None, ("E-3", f"还款金额超过应还合计 {total_due}（本金 {principal} + 罚息 {penalty}）")
        acc, err = check_account(db, repay_no, need_amount=amount, session=s)  # E-1 余额不足
        if err:
            return None, err
        cust, ierr = check_identity(db, acc["customer_id"], ident, session=s)  # 核验扣款账户持有人身份（证件号或邮箱）
        if ierr:
            return None, ierr
        if acc["customer_id"] != loan["customer_id"]:  # 扣款账户须属该贷款客户，杜绝用他人余额还他人贷款
            return None, ("E-OWNER", "还款扣款账户不属于该贷款客户")
        # 罚息优先冲抵，剩余冲抵本金；本金与应收罚息全部还清才结清
        pay_penalty = penalty if amount >= penalty else amount
        pay_principal = amount - pay_penalty
        new_loan_bal = principal - pay_principal
        new_penalty_due = penalty - pay_penalty
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) - amount)}}, session=s)
        settled = new_loan_bal <= 0 and new_penalty_due <= 0
        new_status = C.LOAN_PAID_OFF if settled else loan["status"]
        # 金额一律存 Decimal128（与全系统金额不变式一致），仅在对外序列化时才转 float
        log_entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"), "amount": m(amount),
                     "principal_part": m(pay_principal), "penalty_part": m(pay_penalty),
                     "account_no": repay_no, "balance_after": m(new_loan_bal)}
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"balance": m(new_loan_bal), "status": new_status,
                                     "penalty_due": m(new_penalty_due), "penalty_asof": new_asof},
                            "$push": {"repay_log": log_entry}}, session=s)
        write_txn(db, business_type=C.TXN_LOAN_REPAY, amount=amount, user_id=g.user["_id"],
                  customer_id=loan["customer_id"], account_id=acc["_id"], related_id=loan["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_LOAN_REPAY, object_type="loan",
                    object_id=contract_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount), "principal": str(pay_principal),
                            "penalty": str(pay_penalty), "balance_after": str(new_loan_bal)}, session=s)
        return new_status, None

    status, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    msg = "还款成功，贷款已结清" if status == C.LOAN_PAID_OFF else "还款成功"
    return ok({"loan": loan_view(db, db.loan.find_one({"_id": ln["_id"]}))}, msg)


# ---------- UC-205 逾期管理 ----------
@bp.get("/overdue")
@clerk
def overdue_list():
    db = get_db()
    days = max(0, int(request.args.get("days") or 0))  # 负数当作不过滤，避免过滤条件被静默忽略
    customer_no = (request.args.get("customer_no") or "").strip()
    contract_no = (request.args.get("contract_no") or "").strip()

    q = {"status": {"$in": [C.LOAN_ACTIVE, C.LOAN_OVERDUE]}, "balance": {"$gt": m(0)}}
    if contract_no:
        q["contract_no"] = contract_no
    if customer_no:
        cust = db.customer.find_one({"customer_no": customer_no})
        q["customer_id"] = cust["_id"] if cust else None

    # 注意：不能用 get_param_dec(..., None)，因为 dec(None) 会把缺失值变成 Decimal(0)，
    # 导致“参数缺失应报错”分支永远走不到、罚息被静默算成 0。先判存在性，再转 Decimal。
    raw_rate = get_param(db, C.P_LOAN_OVERDUE_RATE)
    if raw_rate is None:  # E-3 罚息参数缺失
        return fail("E-3", "缺少逾期罚息参数，请管理员先维护 LOAN_OVERDUE_RATE")
    overdue_rate = dec(raw_rate)

    today = now()
    rows = []
    for ln in db.loan.find(q):
        due = ln.get("due_date")
        if not due or today.date() <= due.date():  # 日历日粒度，与还款口径一致
            continue
        overdue_days = (today.date() - due.date()).days
        if days and overdue_days < days:
            continue
        penalty, _ = accrue_penalty(ln, raw_rate)  # 与还款同一净额口径（扣已缴，避免列表多报）
        v = loan_view(db, ln)
        v["overdue_days"] = overdue_days
        v["penalty"] = float(penalty)
        rows.append(v)

    if not rows:  # E-1 无逾期
        return ok({"loans": [], "hint": "无逾期贷款"})
    return ok({"loans": rows})


@bp.post("/overdue")
@clerk
def overdue_record():
    """登记催收结果，并按规则把到期未还的贷款置为 OVERDUE。"""
    d = _body()
    contract_no = (d.get("contract_no") or "").strip()
    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款")
    if ln["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):  # 仅存续/逾期贷款可催收登记
        return fail("E-2", f"贷款当前为「{C.LOAN_STATUS_LABEL.get(ln['status'])}」，无需逾期催收")

    entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"),
             "method": (d.get("method") or "").strip(),
             "feedback": (d.get("feedback") or "").strip(),
             "note": (d.get("note") or "").strip(),
             "operator": g.user["name"]}
    update = {"$push": {"collection_log": entry}}  # 至少 push 催收记录，避免空 $set 报错
    due = ln.get("due_date")
    if due and now().date() > due.date() and dec(ln["balance"]) > 0:  # 日历日粒度
        update["$set"] = {"status": C.LOAN_OVERDUE}
    db.loan.update_one({"_id": ln["_id"]}, update)
    write_audit(db, user_id=g.user["_id"], action="LOAN_OVERDUE", object_type="loan",
                object_id=contract_no, result=C.RESULT_SUCCESS, detail=entry)
    return ok({"loan": loan_view(db, db.loan.find_one({"_id": ln["_id"]}))}, "催收记录已保存")


# ---------- UC-206 贷款查询统计 ----------
@bp.get("/query")
@clerk
def query():
    db = get_db()
    q = {}
    if request.args.get("customer_no"):
        cust = db.customer.find_one({"customer_no": request.args["customer_no"].strip()})
        q["customer_id"] = cust["_id"] if cust else None
    if request.args.get("contract_no"):
        q["contract_no"] = request.args["contract_no"].strip()
    if request.args.get("status"):
        q["status"] = request.args["status"].strip().upper()
    if request.args.get("loan_type"):
        q["loan_type"] = request.args["loan_type"].strip()
    rng = parse_date_range(request.args.get("start"), request.args.get("end"))
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["created_at"] = rng

    total = db.loan.count_documents(q)
    loans = list(db.loan.find(q).sort("created_at", -1).limit(500))
    # 统计基于全量(count/聚合)，而非被截断的 500 条，避免命中 >500 时统计失真
    agg = list(db.loan.aggregate([{"$match": q}, {"$group": {
        "_id": None, "amt": {"$sum": "$amount"}, "bal": {"$sum": "$balance"}}}]))
    stats = {"count": total,
             "total_amount": float(dec(agg[0]["amt"])) if agg else 0.0,
             "total_balance": float(dec(agg[0]["bal"])) if agg else 0.0,
             "overdue_count": db.loan.count_documents({**q, "status": C.LOAN_OVERDUE}),
             "paid_count": db.loan.count_documents({**q, "status": C.LOAN_PAID_OFF})}
    hint = "无匹配数据" if not loans else (f"匹配 {total} 笔，列表仅显示最近 500 笔（统计为全量）" if total > 500 else None)
    write_audit(db, user_id=g.user["_id"], action="LOAN_QUERY", object_type="loan",
                object_id="-", result=C.RESULT_SUCCESS)
    return ok({"loans": [loan_view(db, l) for l in loans], "stats": stats, "hint": hint})
