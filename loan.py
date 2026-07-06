"""贷款业务子系统 UC-201 ~ UC-206（参与者：贷款业务员）。"""
from datetime import timedelta

from flask import Blueprint, request, g

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, oid, now,
    find_customer, check_account, write_txn, write_audit,
    get_param_dec, customer_view, txn_view, parse_date_range, new_contract_no,
)

bp = Blueprint("loan", __name__, url_prefix="/api/loan")
clerk = require_role(C.ROLE_LOAN)


def _body():
    return request.get_json(force=True, silent=True) or {}


def add_months(dt, months):
    """给日期加 months 个月（简化，用于估算到期日）。"""
    y, mth = dt.year, dt.month - 1 + int(months)
    y += mth // 12
    mth = mth % 12 + 1
    day = min(dt.day, 28)
    return dt.replace(year=y, month=mth, day=day)


def loan_view(db, ln, with_customer=True):
    v = {
        "id": str(ln["_id"]),
        "contract_no": ln["contract_no"],
        "loan_type": ln.get("loan_type"),
        "amount": float(dec(ln.get("amount"))),
        "balance": float(dec(ln.get("balance"))),
        "interest_rate": float(dec(ln.get("interest_rate"))) if ln.get("interest_rate") is not None else None,
        "term_months": ln.get("term_months"),
        "status": ln["status"],
        "status_label": C.LOAN_STATUS_LABEL.get(ln["status"], ln["status"]),
        "purpose": ln.get("purpose"),
        "guarantee": ln.get("guarantee"),
        "due_date": ln["due_date"].strftime("%Y-%m-%d") if ln.get("due_date") else None,
        "repay_log": ln.get("repay_log") or [],
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
    cust = find_customer(db, customer_no=(d.get("customer_no") or "").strip() or None,
                         id_no=(d.get("id_no") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户，请先核对客户信息")

    loan_type = (d.get("loan_type") or "").strip()
    amount = D(d.get("amount") or 0)
    term = int(d.get("term_months") or 0)
    account_no = (d.get("account_no") or "").strip()
    if not loan_type or amount <= 0 or term <= 0:  # E-2 必填缺失
        return fail("E-2", "贷款类型、金额、期限为必填且需合法")

    # E-1 黑名单 / 严重逾期
    if cust["status"] == C.CUSTOMER_BLACKLIST:
        return fail("E-1", "客户处于黑名单，拒绝办理")
    # 逾期 = 已标记 OVERDUE，或 ACTIVE 但已过到期日且未还清
    if db.loan.count_documents({"customer_id": cust["_id"], "$or": [
            {"status": C.LOAN_OVERDUE},
            {"status": C.LOAN_ACTIVE, "due_date": {"$lt": now()}, "balance": {"$gt": m(0)}}]}):
        return fail("E-1", "客户存在逾期贷款，拒绝办理")

    acc = db.account.find_one({"account_no": account_no}) if account_no else \
        db.account.find_one({"customer_id": cust["_id"]})
    if not acc:
        return fail("E-NOACC", "客户没有可用的储蓄账户（放款/还款账户）")

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
    if ln["status"] != C.LOAN_PENDING:  # E-1 已被处理
        return fail("E-1", f"该申请已被处理（当前：{C.LOAN_STATUS_LABEL.get(ln['status'])}），请刷新")

    updates = {}
    if decision == "APPROVED":
        appr_amt = D(d.get("approved_amount") or ln["amount"])
        # 利率不能用 D()（只保留 2 位会把 0.0435 截成 0.04）；用 dec() 保留精度，存库时 D6_rate 再定 4 位
        rate = dec(d["interest_rate"]) if d.get("interest_rate") not in (None, "") \
            else get_param_dec(db, C.P_LOAN_RATE, "0.0435")
        term = int(d.get("term_months") or ln["term_months"])
        if appr_amt <= 0 or rate < 0 or term <= 0:
            return fail("E-VAL", "批准金额/利率/期限不合法", 400)
        updates = {"status": C.LOAN_APPROVED, "amount": m(appr_amt),
                   "interest_rate": D6_rate(rate), "term_months": term,
                   "repay_method": (d.get("repay_method") or "等额本息").strip()}
    elif decision == "REJECTED":
        updates = {"status": C.LOAN_REJECTED, "reject_reason": (d.get("reason") or "").strip()}
    elif decision == "SUPPLEMENT":
        updates = {"status": C.LOAN_SUPPLEMENT, "supplement_note": (d.get("reason") or "").strip()}
    else:
        return fail("E-OP", "审批结论非法（APPROVED/REJECTED/SUPPLEMENT）", 400)

    db.loan.update_one({"_id": ln["_id"]}, {"$set": updates})
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
        due = add_months(now(), loan["term_months"])
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"status": C.LOAN_ACTIVE, "balance": m(amount),
                                     "due_date": due, "disbursed_at": now()}}, session=s)
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
    if amount <= 0:
        return fail("E-AMT", "还款金额必须大于零", 400)

    db = get_db()
    ln = db.loan.find_one({"contract_no": contract_no})
    if not ln:
        return fail("E-NOLOAN", "未找到贷款")
    repay_no = account_no or _acc_no(db, ln)

    def txn(s):
        loan = db.loan.find_one({"_id": ln["_id"]}, session=s)  # 事务内重读，读改写一致
        if loan["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):  # E-2 已结清
            return None, ("E-2", f"贷款当前为「{C.LOAN_STATUS_LABEL.get(loan['status'])}」，无需还款")
        if amount > dec(loan["balance"]):  # E-3 超额
            return None, ("E-3", f"还款金额超过应还余额 {dec(loan['balance'])}")
        acc, err = check_account(db, repay_no, need_amount=amount, session=s)  # E-1 余额不足
        if err:
            return None, err
        new_loan_bal = dec(loan["balance"]) - amount
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) - amount)}}, session=s)
        log_entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"), "amount": float(amount),
                     "account_no": repay_no, "balance_after": float(new_loan_bal)}
        new_status = C.LOAN_PAID_OFF if new_loan_bal <= 0 else loan["status"]
        db.loan.update_one({"_id": loan["_id"]},
                           {"$set": {"balance": m(new_loan_bal), "status": new_status},
                            "$push": {"repay_log": log_entry}}, session=s)
        write_txn(db, business_type=C.TXN_LOAN_REPAY, amount=amount, user_id=g.user["_id"],
                  customer_id=loan["customer_id"], account_id=acc["_id"], related_id=loan["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_LOAN_REPAY, object_type="loan",
                    object_id=contract_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount), "balance_after": str(new_loan_bal)}, session=s)
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
    days = int(request.args.get("days") or 0)
    customer_no = (request.args.get("customer_no") or "").strip()
    contract_no = (request.args.get("contract_no") or "").strip()

    q = {"status": {"$in": [C.LOAN_ACTIVE, C.LOAN_OVERDUE]}, "balance": {"$gt": m(0)}}
    if contract_no:
        q["contract_no"] = contract_no
    if customer_no:
        cust = db.customer.find_one({"customer_no": customer_no})
        q["customer_id"] = cust["_id"] if cust else None

    overdue_rate = get_param_dec(db, C.P_LOAN_OVERDUE_RATE, None)
    if overdue_rate is None:  # E-3 罚息参数缺失
        return fail("E-3", "缺少逾期罚息参数，请管理员先维护 LOAN_OVERDUE_RATE")

    today = now()
    rows = []
    for ln in db.loan.find(q):
        due = ln.get("due_date")
        if not due or due >= today:
            continue
        overdue_days = (today - due).days
        if days and overdue_days < days:
            continue
        penalty = D(dec(ln["balance"]) * overdue_rate * overdue_days)
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
    if ln["status"] == C.LOAN_PAID_OFF:  # E-2 已结清
        return fail("E-2", "贷款已结清，无需逾期处理")

    entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"),
             "method": (d.get("method") or "").strip(),
             "feedback": (d.get("feedback") or "").strip(),
             "note": (d.get("note") or "").strip(),
             "operator": g.user["name"]}
    update = {"$push": {"collection_log": entry}}  # 至少 push 催收记录，避免空 $set 报错
    due = ln.get("due_date")
    if due and due < now() and dec(ln["balance"]) > 0:
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

    loans = list(db.loan.find(q).sort("created_at", -1).limit(500))
    stats = {"count": len(loans),
             "total_amount": float(sum((dec(l["amount"]) for l in loans), D(0))),
             "total_balance": float(sum((dec(l["balance"]) for l in loans), D(0))),
             "overdue_count": sum(1 for l in loans if l["status"] == C.LOAN_OVERDUE),
             "paid_count": sum(1 for l in loans if l["status"] == C.LOAN_PAID_OFF)}
    write_audit(db, user_id=g.user["_id"], action="LOAN_QUERY", object_type="loan",
                object_id="-", result=C.RESULT_SUCCESS)
    return ok({"loans": [loan_view(db, l) for l in loans], "stats": stats,
               "hint": None if loans else "无匹配数据"})
