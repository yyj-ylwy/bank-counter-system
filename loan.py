"""贷款业务子系统 UC-201 ~ UC-206（参与者：贷款业务员）。

路由层：解析/校验请求 → 调 loan_domain（计划表/罚息/分配/状态）与 bankcore（账户记账）
→ 事务内持久化 → 序列化响应。业务规则的计算全部在领域层，本文件不做利息/罚息数学。
"""
from flask import Blueprint, request, g

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, now, as_int,
    find_customer, resolve_loan, resolve_account_no, write_audit,
    get_param, get_param_dec, customer_view, txn_view, parse_date_range, new_contract_no,
)
from bankcore import AccountGuard, SavingsAccount, IdempotencyGuard, TxnRecorder, CREDIT, DEBIT
from loan_domain import (
    STRATEGIES, LoanBook, PenaltyCalculator, RepaymentAllocator,
    build_schedule, sched_to_db, sched_from_db, sched_view, INST_PAID,
    derive_status, first_overdue, remaining_amounts, settle_quote,
    add_months, rate4,  # add_months 再导出：test_logic.py 从本模块导入
)

bp = Blueprint("loan", __name__, url_prefix="/api/loan")
clerk = require_role(C.ROLE_LOAN)


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


def loan_view(db, ln, with_customer=True, with_schedule=True):
    v = {
        "id": str(ln["_id"]),
        "contract_no": ln["contract_no"],
        "loan_type": ln.get("loan_type"),
        "amount": float(dec(ln.get("amount"))),
        "balance": float(dec(ln.get("balance"))),
        "penalty_due": float(dec(ln.get("penalty_due", 0))),  # 应收未缴逾期罚息
        "interest_rate": float(dec(ln.get("interest_rate"))) if ln.get("interest_rate") is not None else None,
        "term_months": ln.get("term_months"),
        "repay_method": ln.get("repay_method"),
        "status": ln["status"],
        "status_label": C.LOAN_STATUS_LABEL.get(ln["status"], ln["status"]),
        "purpose": ln.get("purpose"),
        "guarantee": ln.get("guarantee"),
        "reject_reason": ln.get("reject_reason"),
        "supplement_note": ln.get("supplement_note"),
        "due_date": ln["due_date"].strftime("%Y-%m-%d") if ln.get("due_date") else None,
        "approved_by_no": ln.get("approved_by_no"),      # 审贷分离留痕：审批人工号
        "disbursed_by_no": ln.get("disbursed_by_no"),    # 放款人工号
        "repay_log": ln.get("repay_log") or [],
        "collection_log": ln.get("collection_log") or [],   # UC-205 催收记录，供查询/展示
        "created_at": ln["created_at"].strftime("%Y-%m-%d %H:%M:%S") if ln.get("created_at") else None,
    }
    sched = sched_from_db(ln.get("schedule"))
    if sched:  # 还款计划摘要：剩余利息、下一期、结清试算（按已入账罚息口径）
        _, interest_rem = remaining_amounts(sched)
        v["interest_remaining"] = float(interest_rem)
        v["settle_amount"] = float(settle_quote(ln, dec(ln.get("penalty_due", 0))))
        nxt = next((i for i in sched if i["status"] != INST_PAID), None)
        if nxt:
            v["next_due"] = {"period": nxt["period"],
                             "due_date": nxt["due_date"].strftime("%Y-%m-%d"),
                             "amount": float(D(nxt["principal_due"] - nxt["principal_paid"]
                                               + nxt["interest_due"] - nxt["interest_paid"]
                                               - nxt["waived_interest"]))}
        if with_schedule:
            v["schedule"] = sched_view(sched)
    if with_customer:
        cust = db.customer.find_one({"_id": ln["customer_id"]})
        v["customer"] = customer_view(cust)
        acc = db.account.find_one({"_id": ln.get("account_id")}) if ln.get("account_id") else None
        v["account_no"] = acc["account_no"] if acc else None
    return v


def _has_overdue_loan(db, customer_id):
    """申请准入用的逾期判定：已标 OVERDUE、计划表内有过期未清期、或老贷款整笔到期未清。"""
    for ln in db.loan.find({"customer_id": customer_id,
                            "status": {"$in": [C.LOAN_ACTIVE, C.LOAN_OVERDUE]}}):
        if ln["status"] == C.LOAN_OVERDUE:
            return True
        sched = sched_from_db(ln.get("schedule"))
        if sched:
            if first_overdue(sched):
                return True
        elif (ln.get("due_date") and now().date() > ln["due_date"].date()
              and dec(ln["balance"]) > 0):  # 无计划表的历史贷款沿用整笔口径
            return True
    return False


# ---------- UC-201 贷款申请办理 ----------
@bp.post("/apply")
@clerk
def apply():
    d = _body()
    db = get_db()
    cust = find_customer(db, ident=(d.get("ident") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户，请先核对客户信息")

    loan_type = (d.get("loan_type") or "").strip()
    amount = D(d.get("amount") or 0)
    term = as_int(d.get("term_months"))
    if loan_type not in C.LOAN_TYPES:  # E-2 类型非法
        return fail("E-2", "贷款类型非法")
    if amount <= 0 or amount > C.LOAN_AMOUNT_MAX:  # E-2 金额范围
        return fail("E-2", f"申请金额须大于 0 且不超过 {C.LOAN_AMOUNT_MAX:,}")
    if term <= 0 or term > C.LOAN_TERM_MAX:  # E-2 期限范围（防到期日计算溢出）
        return fail("E-2", f"期限须为 1~{C.LOAN_TERM_MAX} 个月")

    # E-1 黑名单 / 逾期（按期认定，与还款/罚息同口径）
    if cust["status"] == C.CUSTOMER_BLACKLIST:
        return fail("E-1", "客户处于黑名单，拒绝办理")
    if _has_overdue_loan(db, cust["_id"]):
        return fail("E-1", "客户存在逾期贷款，拒绝办理")

    acc = db.account.find_one({"customer_id": cust["_id"], "status": C.ACCOUNT_NORMAL})
    if not acc:
        return fail("E-NOACC", "客户没有可用的正常储蓄账户")

    ln = {
        "contract_no": new_contract_no(),
        "customer_id": cust["_id"],
        "account_id": acc["_id"],
        "user_id": g.user["_id"],  # 申请经办人（审贷分离的比对基准）
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
    serr = LoanBook.sod_check(ln, g.user["_id"], "approve")  # 审贷分离：经办人不得自审
    if serr:
        return fail(serr[0], serr[1])

    updates = {}
    if decision == "APPROVED":
        # 用 not in (None, "") 判定“是否填写”，避免显式传入的 0 被 `x or default` 静默替换成原申请值而绕过校验
        appr_amt = D(d["approved_amount"]) if d.get("approved_amount") not in (None, "") else D(ln["amount"])
        # 利率不能用 D()（只保留 2 位会把 0.0435 截成 0.04）；用 dec() 保留精度，存库时 rate4 再定 4 位
        rate = dec(d["interest_rate"]) if d.get("interest_rate") not in (None, "") \
            else get_param_dec(db, C.P_LOAN_RATE, "0.0435")
        term = as_int(d["term_months"]) if d.get("term_months") not in (None, "") else int(ln["term_months"])
        method = (d.get("repay_method") or "等额本息").strip()
        if appr_amt <= 0 or appr_amt > C.LOAN_AMOUNT_MAX:
            return fail("E-VAL", f"批准金额须大于 0 且不超过 {C.LOAN_AMOUNT_MAX:,}", 400)
        if rate < 0 or rate > 1:  # 年利率为小数，上限 1（100%），防误填成 4.35 这类整数
            return fail("E-VAL", "年利率应为 0~1 之间的小数（如 0.0435 表示 4.35%）", 400)
        if term <= 0 or term > C.LOAN_TERM_MAX:
            return fail("E-VAL", f"期限须为 1~{C.LOAN_TERM_MAX} 个月", 400)
        if method not in STRATEGIES:  # 还款方式决定放款时生成的计划表策略
            return fail("E-VAL", "还款方式非法（等额本息/等额本金/一次性还本付息）", 400)
        updates = {"status": C.LOAN_APPROVED, "amount": m(appr_amt),
                   "interest_rate": rate4(rate), "term_months": term, "repay_method": method,
                   "approved_by": g.user["_id"], "approved_by_no": g.user.get("employee_no"),
                   "approved_at": now()}
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
        serr = LoanBook.sod_check(loan, g.user["_id"], "disburse")  # 审贷分离：审批人不得自放
        if serr:
            return serr
        # 收款为入金方向：冻结/销户拦截，挂失账户放行（只进不出）
        acc, err = AccountGuard.check(db, _acc_no(db, loan), direction=CREDIT, session=s)
        if err:
            return ("E-1", f"收款账户不可用：{err[1]}")
        amount = dec(loan["amount"])
        SavingsAccount(db, acc, s).credit(amount)
        # 生成还款计划：起算日取放款日零点，各期到期日为日历日，比较无时分秒歧义
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        schedule = build_schedule(loan["repay_method"], amount,
                                  dec(loan["interest_rate"]), loan["term_months"], start)
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"status": C.LOAN_ACTIVE, "balance": m(amount),
                                     "schedule": sched_to_db(schedule),
                                     "due_date": schedule[-1]["due_date"],  # 整笔到期日=末期到期日
                                     "disbursed_at": now(), "disbursed_by": g.user["_id"],
                                     "disbursed_by_no": g.user.get("employee_no"),
                                     "penalty_due": m(0), "penalty_asof": None}}, session=s)
        TxnRecorder.record(db, business_type=C.TXN_LOAN_DISBURSE, amount=amount,
                           user_id=g.user["_id"], customer_id=loan["customer_id"],
                           account_id=acc["_id"], related_id=loan["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_LOAN_DISBURSE, object_type="loan",
                    object_id=contract_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount), "repay_method": loan["repay_method"]}, session=s)
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
    ident = (d.get("ident") or "").strip()
    mode = (d.get("mode") or "NORMAL").strip().upper()  # NORMAL 按期/提前还款；SETTLE 一次性提前结清
    amount = D(d.get("amount") or 0)
    request_id = (d.get("request_id") or "").strip()  # 幂等键（选填）
    if mode not in ("NORMAL", "SETTLE"):
        return fail("E-OP", "还款方式非法（NORMAL/SETTLE）", 400)
    if mode == "NORMAL" and amount <= 0:
        return fail("E-AMT", "还款金额必须大于零", 400)

    db = get_db()
    ln, cust, rerr = resolve_loan(db, ident, statuses=[C.LOAN_ACTIVE, C.LOAN_OVERDUE])
    if rerr:
        return fail(rerr[0], rerr[1])
    contract_no = ln["contract_no"]
    repay_no, _c, aerr = resolve_account_no(db, ident)  # 凭任意身份标识定位还款储蓄账户
    if aerr:
        return fail(aerr[0], aerr[1])
    # 逾期罚息日利率（可能未维护）：仅在贷款确实逾期时才要求存在，避免正常还款被参数缺失阻断
    raw_overdue_rate = get_param(db, C.P_LOAN_OVERDUE_RATE)

    def txn(s):
        dup = IdempotencyGuard.find_existing(db, request_id, C.TXN_LOAN_REPAY, s)  # 幂等：重放不重复扣款
        if dup:
            return ("DUP", dup), None
        loan = db.loan.find_one({"_id": ln["_id"]}, session=s)  # 事务内重读，读改写一致
        if loan["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):  # E-2 已结清
            return None, ("E-2", f"贷款当前为「{C.LOAN_STATUS_LABEL.get(loan['status'])}」，无需还款")
        LoanBook(db, s).ensure_schedule(loan)  # 历史贷款懒升级出计划表
        if raw_overdue_rate is None and first_overdue(sched_from_db(loan["schedule"])):
            return None, ("E-3", "缺少逾期罚息参数，请管理员先维护 LOAN_OVERDUE_RATE")
        # 增量计提罚息（按期、日历日、水位不重不漏），罚息单列不并入本金
        penalty, new_asof = PenaltyCalculator.accrue(loan, raw_overdue_rate)
        if mode == "SETTLE":  # 结清试算即应扣金额：罚息 + 到期未还利息 + 全部剩余本金
            pay_amount, alloc = RepaymentAllocator.allocate_settle(loan["schedule"], penalty)
            if pay_amount <= 0:
                return None, ("E-2", "该贷款已无应还款项")
        else:  # 瀑布分配：罚息 → 按期序 利息 → 本金；超额报 E-3
            pay_amount = amount
            alloc, aerr2 = RepaymentAllocator.allocate(loan["schedule"], penalty, amount)
            if aerr2:
                return None, aerr2
        acc, err = AccountGuard.check(db, repay_no, direction=DEBIT,
                                      need_amount=pay_amount, session=s)  # E-1/E-BAL 等
        if err:
            return None, err
        if acc["customer_id"] != loan["customer_id"]:  # 扣款账户须属该贷款客户（凭身份定位已保证，此为并发兜底）
            return None, ("E-OWNER", "还款扣款账户不属于该贷款客户")
        derr = SavingsAccount(db, acc, s).debit(pay_amount)  # 条件原子扣款
        if derr:
            return None, derr
        new_status = derive_status(alloc["schedule"], alloc["penalty_remaining"])
        # 金额一律存 Decimal128（与全系统金额不变式一致），仅在对外序列化时才转 float
        log_entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
                     "amount": m(pay_amount), "principal_part": m(alloc["principal_part"]),
                     "interest_part": m(alloc["interest_part"]), "penalty_part": m(alloc["penalty_part"]),
                     "waived_interest": m(alloc["waived_interest"]),
                     "account_no": repay_no, "balance_after": m(alloc["principal_remaining"])}
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"schedule": sched_to_db(alloc["schedule"]),
                                     "balance": m(alloc["principal_remaining"]),
                                     "penalty_due": m(alloc["penalty_remaining"]),
                                     "penalty_asof": new_asof, "status": new_status},
                            "$push": {"repay_log": log_entry}}, session=s)
        t = TxnRecorder.record(db, business_type=C.TXN_LOAN_REPAY, amount=pay_amount,
                               user_id=g.user["_id"], customer_id=loan["customer_id"],
                               account_id=acc["_id"], related_id=loan["_id"],
                               request_id=request_id, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_LOAN_REPAY, object_type="loan",
                    object_id=contract_no, result=C.RESULT_SUCCESS,
                    detail={"mode": mode, "amount": str(pay_amount),
                            "principal": str(alloc["principal_part"]),
                            "interest": str(alloc["interest_part"]),
                            "penalty": str(alloc["penalty_part"]),
                            "waived": str(alloc["waived_interest"]),
                            "balance_after": str(alloc["principal_remaining"])}, session=s)
        return (new_status, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    tag, payload = res
    view = loan_view(db, db.loan.find_one({"_id": ln["_id"]}))
    if tag == "DUP":  # 幂等命中：返回原流水，不再次扣款
        return ok({"loan": view, "txn": txn_view(payload), "duplicate": True},
                  "重复请求，返回原交易结果")
    msg = "还款成功，贷款已结清" if tag == C.LOAN_PAID_OFF else "还款成功"
    return ok({"loan": view, "txn": txn_view(payload)}, msg)


# ---------- UC-205 逾期管理 ----------
@bp.get("/overdue")
@clerk
def overdue_list():
    db = get_db()
    days = max(0, int(request.args.get("days") or 0))  # 负数当作不过滤，避免过滤条件被静默忽略
    ident = (request.args.get("ident") or "").strip()

    q = {"status": {"$in": [C.LOAN_ACTIVE, C.LOAN_OVERDUE]}, "balance": {"$gt": m(0)}}
    if ident:
        # ident 可能是合同号或客户身份标识
        ln = db.loan.find_one({"contract_no": ident})
        if ln:
            q["contract_no"] = ident
        else:
            cust = find_customer(db, ident=ident)
            q["customer_id"] = cust["_id"] if cust else None

    # 注意：不能用 get_param_dec(..., None)，因为 dec(None) 会把缺失值变成 Decimal(0)，
    # 导致“参数缺失应报错”分支永远走不到、罚息被静默算成 0。先判存在性，再转 Decimal。
    raw_rate = get_param(db, C.P_LOAN_OVERDUE_RATE)
    if raw_rate is None:  # E-3 罚息参数缺失
        return fail("E-3", "缺少逾期罚息参数，请管理员先维护 LOAN_OVERDUE_RATE")

    book = LoanBook(db)
    today = now().date()
    rows = []
    for ln in db.loan.find(q):
        book.ensure_schedule(ln)  # 历史贷款懒升级
        sched = sched_from_db(ln.get("schedule"))
        inst = first_overdue(sched)  # 逾期认定 = 存在过期未清的期（与还款同口径）
        if not inst:
            continue
        overdue_days = (today - inst["due_date"].date()).days
        if days and overdue_days < days:
            continue
        penalty, _ = PenaltyCalculator.accrue(ln, raw_rate)  # 净额口径（扣已缴），与还款一致
        if ln["status"] != C.LOAN_OVERDUE:  # 触碰即自动派生逾期状态，不再等催收手工置位
            db.loan.update_one({"_id": ln["_id"], "status": ln["status"]},
                               {"$set": {"status": C.LOAN_OVERDUE}})
            ln["status"] = C.LOAN_OVERDUE
        v = loan_view(db, ln, with_schedule=False)
        v["overdue_days"] = overdue_days
        v["overdue_period"] = inst["period"]
        v["penalty"] = float(penalty)
        rows.append(v)

    if not rows:  # E-1 无逾期
        return ok({"loans": [], "hint": "无逾期贷款"})
    return ok({"loans": rows})


@bp.post("/overdue")
@clerk
def overdue_record():
    """登记催收结果；状态由计划表派生（存在过期未清期 → OVERDUE）。"""
    d = _body()
    contract_no = (d.get("contract_no") or "").strip()
    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款")
    if ln["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):  # 仅存续/逾期贷款可催收登记
        return fail("E-2", f"贷款当前为「{C.LOAN_STATUS_LABEL.get(ln['status'])}」，无需逾期催收")

    LoanBook(db).ensure_schedule(ln)
    entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"),
             "method": (d.get("method") or "").strip(),
             "feedback": (d.get("feedback") or "").strip(),
             "note": (d.get("note") or "").strip(),
             "operator": g.user["name"]}
    update = {"$push": {"collection_log": entry}}  # 至少 push 催收记录，避免空 $set 报错
    if first_overdue(sched_from_db(ln.get("schedule"))):
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
    ident = (request.args.get("ident") or "").strip()
    if ident:
        ln = db.loan.find_one({"contract_no": ident})
        if ln:
            q["contract_no"] = ident
        else:
            cust = find_customer(db, ident=ident)
            q["customer_id"] = cust["_id"] if cust else None
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
    # 列表场景不携带整张计划表（360 期×500 笔响应过大），单笔详情仍含 schedule
    single = len(loans) == 1
    return ok({"loans": [loan_view(db, l, with_schedule=single) for l in loans],
               "stats": stats, "hint": hint})
