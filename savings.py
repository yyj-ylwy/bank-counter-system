"""储蓄业务子系统 UC-101 ~ UC-108（参与者：储蓄业务员）。"""
from flask import Blueprint, request, g
from pymongo.errors import DuplicateKeyError

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, oid, now,
    validate_id_no, find_customer, check_account, write_txn, write_audit,
    get_param_dec, customer_view, account_view, txn_view, parse_date_range,
    new_customer_no, new_account_no, new_debit_card_no,
)

bp = Blueprint("savings", __name__, url_prefix="/api/savings")
clerk = require_role(C.ROLE_SAVINGS)


def _body():
    return request.get_json(force=True, silent=True) or {}


# ---------- UC-101 客户信息登记与开户注册 ----------
@bp.post("/open-account")
@clerk
def open_account():
    d = _body()
    name = (d.get("name") or "").strip()
    id_type = (d.get("id_type") or "身份证").strip()
    id_no = (d.get("id_no") or "").strip()
    phone = (d.get("phone") or "").strip()
    init = D(d.get("initial_balance") or 0)

    if not name or not id_no:
        return fail("E-REQ", "姓名和证件号为必填项", 400)
    valid, reason = validate_id_no(id_type, id_no)
    if not valid:  # E-2 证件号格式非法/过期
        return fail("E-2", reason)
    if init < 0:
        return fail("E-AMT", "初始存款金额不能为负", 400)

    db = get_db()
    if db.customer.find_one({"id_no": id_no}):  # E-1 证件号已存在
        return fail("E-1", "该证件号已关联客户，可转为信息更新")

    def txn(s):
        cust = {
            "customer_no": new_customer_no(s), "name": name, "id_type": id_type,
            "id_no": id_no, "phone": phone, "status": C.CUSTOMER_NORMAL, "created_at": now(),
        }
        cid = db.customer.insert_one(cust, session=s).inserted_id
        cust["_id"] = cid
        acc = {
            "account_no": new_account_no(s), "customer_id": cid, "card_no": new_debit_card_no(s),
            "card_status": C.CARD_NORMAL, "currency": "CNY", "balance": m(init),
            "status": C.ACCOUNT_NORMAL, "created_at": now(),
        }
        aid = db.account.insert_one(acc, session=s).inserted_id
        acc["_id"] = aid
        write_txn(db, business_type=C.TXN_OPEN, amount=init, user_id=g.user["_id"],
                  customer_id=cid, account_id=aid, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_OPEN, object_type="account",
                    object_id=acc["account_no"], result=C.RESULT_SUCCESS,
                    detail={"customer_no": cust["customer_no"], "name": name}, session=s)
        return cust, acc

    try:
        cust, acc = run_in_transaction(txn)
    except DuplicateKeyError:
        return fail("E-1", "该证件号已关联客户，可转为信息更新")
    return ok({"customer": customer_view(cust), "account": account_view(acc, cust)}, "开户成功")


# ---------- UC-102 柜台存款 ----------
@bp.post("/deposit")
@clerk
def deposit():
    d = _body()
    account_no = (d.get("account_no") or "").strip()
    amount = D(d.get("amount") or 0)
    if amount <= 0:  # E-2
        return fail("E-2", "存款金额必须大于零")

    db = get_db()

    def txn(s):
        acc, err = check_account(db, account_no, session=s)  # E-1 状态异常
        if err:
            return None, err
        new_bal = dec(acc["balance"]) + amount
        db.account.update_one({"_id": acc["_id"]}, {"$set": {"balance": m(new_bal)}}, session=s)
        t = write_txn(db, business_type=C.TXN_DEPOSIT, amount=amount, user_id=g.user["_id"],
                      customer_id=acc["customer_id"], account_id=acc["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_DEPOSIT, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount)}, session=s)
        acc["balance"] = m(new_bal)
        return (acc, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    acc, t = res
    return ok({"balance": float(dec(acc["balance"])), "txn": txn_view(t)}, "存款成功")


# ---------- UC-103 柜台取款 ----------
def _today_withdrawn(db, account_id, session=None):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = D(0)
    cur = db.business_transaction.find(
        {"account_id": account_id, "business_type": C.TXN_WITHDRAW,
         "status": C.TXN_STATUS_SUCCESS, "txn_time": {"$gte": start}}, session=session)
    for t in cur:
        total += dec(t["amount"])
    return total


@bp.post("/withdraw")
@clerk
def withdraw():
    d = _body()
    account_no = (d.get("account_no") or "").strip()
    amount = D(d.get("amount") or 0)
    if amount <= 0:
        return fail("E-AMT", "取款金额必须大于零", 400)

    db = get_db()
    limit = get_param_dec(db, C.P_WITHDRAW_DAILY_LIMIT, "50000")

    def txn(s):
        acc, err = check_account(db, account_no, need_amount=amount, session=s)  # E-1余额/E-3状态
        if err:
            return None, err
        if _today_withdrawn(db, acc["_id"], s) + amount > limit:  # E-2 当日限额
            return None, ("E-2", f"超过当日取款限额 {limit}")
        new_bal = dec(acc["balance"]) - amount
        db.account.update_one({"_id": acc["_id"]}, {"$set": {"balance": m(new_bal)}}, session=s)
        t = write_txn(db, business_type=C.TXN_WITHDRAW, amount=amount, user_id=g.user["_id"],
                      customer_id=acc["customer_id"], account_id=acc["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_WITHDRAW, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount)}, session=s)
        acc["balance"] = m(new_bal)
        return (acc, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    acc, t = res
    return ok({"balance": float(dec(acc["balance"])), "txn": txn_view(t)}, "取款成功")


# ---------- UC-104 转账汇款（含 UC-10401 行内 / 10402 跨行 / 10403 同户）----------
@bp.post("/transfer")
@clerk
def transfer():
    d = _body()
    ttype = (d.get("transfer_type") or "INTRA").strip().upper()  # INTRA=行内, INTER=跨行
    from_no = (d.get("from_account_no") or "").strip()
    to_no = (d.get("to_account_no") or "").strip()
    to_bank = (d.get("to_bank") or "").strip()
    amount = D(d.get("amount") or 0)

    if amount <= 0:
        return fail("E-AMT", "转账金额必须大于零", 400)
    if ttype == "INTRA" and from_no == to_no:  # E-3 同一账户
        return fail("E-3", "转出与转入不能为同一账户")

    db = get_db()
    fee_rate = get_param_dec(db, C.P_TRANSFER_FEE_RATE, "0.001")

    def txn(s):
        fee = D(amount * fee_rate) if ttype == "INTER" else D(0)
        src, err = check_account(db, from_no, need_amount=amount + fee, session=s)  # E-2 余额
        if err:
            return None, err

        if ttype == "INTRA":
            dst, derr = check_account(db, to_no, session=s)  # E-1 转入账户异常
            if derr:
                return None, ("E-1", f"转入账户不可用：{derr[1]}")
            # 扣转出、加转入
            db.account.update_one({"_id": src["_id"]},
                                  {"$set": {"balance": m(dec(src["balance"]) - amount)}}, session=s)
            db.account.update_one({"_id": dst["_id"]},
                                  {"$set": {"balance": m(dec(dst["balance"]) + amount)}}, session=s)
            t_out = write_txn(db, business_type=C.TXN_TRANSFER_OUT, amount=amount, user_id=g.user["_id"],
                              customer_id=src["customer_id"], account_id=src["_id"], session=s)
            t_in = write_txn(db, business_type=C.TXN_TRANSFER_IN, amount=amount, user_id=g.user["_id"],
                             customer_id=dst["customer_id"], account_id=dst["_id"],
                             related_id=t_out["_id"], session=s)
            db.business_transaction.update_one({"_id": t_out["_id"]},
                                              {"$set": {"related_id": t_in["_id"]}}, session=s)
            same_owner = src["customer_id"] == dst["customer_id"]
            write_audit(db, user_id=g.user["_id"], action="TRANSFER", object_type="account",
                        object_id=from_no, result=C.RESULT_SUCCESS,
                        detail={"type": "同户" if same_owner else "行内", "to": to_no,
                                "amount": str(amount)}, session=s)
            return {"fee": 0.0, "sub": "同户转账" if same_owner else "行内转账",
                    "balance": float(dec(src["balance"]) - amount), "txn": txn_view(t_out)}, None
        else:  # INTER 跨行：只扣转出方 + 手续费，不更新系统外收款账户
            if not to_no or not to_bank:  # E-1 收款信息不合法
                return None, ("E-1", "跨行转账需填写收款行和收款账号")
            db.account.update_one({"_id": src["_id"]},
                                  {"$set": {"balance": m(dec(src["balance"]) - amount - fee)}}, session=s)
            t_out = write_txn(db, business_type=C.TXN_TRANSFER_OUT, amount=amount, user_id=g.user["_id"],
                              customer_id=src["customer_id"], account_id=src["_id"], session=s)
            if fee > 0:
                write_txn(db, business_type=C.TXN_TRANSFER_FEE, amount=fee, user_id=g.user["_id"],
                          customer_id=src["customer_id"], account_id=src["_id"],
                          related_id=t_out["_id"], session=s)
            write_audit(db, user_id=g.user["_id"], action="TRANSFER", object_type="account",
                        object_id=from_no, result=C.RESULT_SUCCESS,
                        detail={"type": "跨行", "to_bank": to_bank, "to": to_no,
                                "amount": str(amount), "fee": str(fee)}, session=s)
            return {"fee": float(fee), "sub": "跨行转账（登记汇出，不代表真实到账）",
                    "balance": float(dec(src["balance"]) - amount - fee), "txn": txn_view(t_out)}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok(res, "转账成功")


# ---------- UC-105 账户/明细查询 ----------
@bp.get("/query")
@clerk
def query():
    account_no = (request.args.get("account_no") or "").strip()
    customer_no = (request.args.get("customer_no") or "").strip()
    id_no = (request.args.get("id_no") or "").strip()
    start = request.args.get("start")
    end = request.args.get("end")

    db = get_db()
    acc = None
    cust = None
    if account_no:
        acc = db.account.find_one({"account_no": account_no})
        if acc:
            cust = db.customer.find_one({"_id": acc["customer_id"]})
    else:
        cust = find_customer(db, customer_no=customer_no or None, id_no=id_no or None)
        if cust:
            acc = db.account.find_one({"customer_id": cust["_id"]})

    if not cust or not acc:  # E-1 未找到
        return fail("E-1", "未找到账户")
    if id_no and cust["id_no"] != id_no:  # E-2 证件与账户归属不一致
        write_audit(db, user_id=g.user["_id"], action="QUERY_ACCOUNT", object_type="account",
                    object_id=acc["account_no"], result=C.RESULT_FAILURE, detail={"reason": "证件不匹配"})
        return fail("E-2", "证件与账户归属不一致")

    # 时间范围过滤
    q = {"account_id": acc["_id"]}
    rng = parse_date_range(start, end)
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["txn_time"] = rng
    txns = list(db.business_transaction.find(q).sort("txn_time", -1).limit(500))

    write_audit(db, user_id=g.user["_id"], action="QUERY_ACCOUNT", object_type="account",
                object_id=acc["account_no"], result=C.RESULT_SUCCESS)

    view = account_view(acc, cust)
    if acc["status"] == C.ACCOUNT_CLOSED:
        view["note"] = "账户已销户，仅展示历史信息"  # E-4
    return ok({
        "customer": customer_view(cust),
        "account": view,
        "transactions": [txn_view(t) for t in txns],
        "empty_hint": None if txns else "该时段无明细流水",  # E-3
    })


# ---------- UC-106 挂失/解挂/补卡 ----------
@bp.post("/card")
@clerk
def card_op():
    d = _body()
    account_no = (d.get("account_no") or "").strip()
    id_no = (d.get("id_no") or "").strip()
    op = (d.get("op") or "").strip().upper()  # LOSS 挂失 / UNLOSS 解挂 / REISSUE 补卡

    db = get_db()
    acc = db.account.find_one({"account_no": account_no}) or db.account.find_one({"card_no": account_no})
    if not acc:
        return fail("E-NOACC", "未找到账户")
    cust = db.customer.find_one({"_id": acc["customer_id"]})
    if not cust or (id_no and cust["id_no"] != id_no):  # E-1 证件与账户不匹配
        write_audit(db, user_id=g.user["_id"], action=f"CARD_{op or 'OP'}", object_type="account",
                    object_id=account_no, result=C.RESULT_FAILURE, detail={"reason": "证件不匹配"})
        return fail("E-1", "证件与账户归属不一致")
    if acc["status"] == C.ACCOUNT_CLOSED:
        return fail("E-CLOSED", "账户已销户")

    cur = acc.get("card_status")
    if op == "LOSS":
        if cur == C.CARD_LOST:  # E-2
            return fail("E-2", "卡片已处于挂失状态，无需重复操作")
        updates, new_card, msg = {"card_status": C.CARD_LOST}, acc["card_no"], "挂失成功，已限制交易"
    elif op == "UNLOSS":
        if cur != C.CARD_LOST:  # E-2
            return fail("E-2", "卡片未处于挂失状态，无需解挂")
        updates, new_card, msg = {"card_status": C.CARD_NORMAL}, acc["card_no"], "解挂成功"
    elif op == "REISSUE":
        new_card = new_debit_card_no()
        updates = {"card_no": new_card, "card_status": C.CARD_NORMAL}
        msg = f"补卡成功，新卡号 {new_card}（原卡已失效）"
    else:
        return fail("E-OP", "操作类型非法", 400)

    def txn(s):  # 状态变更与审计同事务，保证补卡/挂失一定留痕
        db.account.update_one({"_id": acc["_id"]}, {"$set": updates}, session=s)
        write_audit(db, user_id=g.user["_id"], action=f"CARD_{op}", object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"old_card": acc["card_no"], "new_card": new_card}, session=s)

    run_in_transaction(txn)
    return ok({"account_no": account_no, "card_no": new_card}, msg)


# ---------- UC-107 销户处理 ----------
@bp.post("/close-account")
@clerk
def close_account():
    d = _body()
    account_no = (d.get("account_no") or "").strip()
    id_no = (d.get("id_no") or "").strip()

    db = get_db()
    acc = db.account.find_one({"account_no": account_no})
    if not acc:
        return fail("E-NOACC", "未找到账户")
    cust = db.customer.find_one({"_id": acc["customer_id"]})
    if not cust or (id_no and cust["id_no"] != id_no):  # E-1 身份核验失败
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CLOSE_ACCOUNT, object_type="account",
                    object_id=account_no, result=C.RESULT_FAILURE, detail={"reason": "身份核验失败"})
        return fail("E-1", "身份核验失败，证件与账户不一致")
    if acc["status"] != C.ACCOUNT_NORMAL:  # E-4 冻结/挂失/已销户
        return fail("E-4", f"账户当前为「{C.ACCOUNT_STATUS_LABEL.get(acc['status'])}」，不可销户")
    # E-2 未结清贷款
    if db.loan.count_documents({"customer_id": cust["_id"],
                                "status": {"$in": [C.LOAN_PENDING, C.LOAN_APPROVED,
                                                   C.LOAN_ACTIVE, C.LOAN_OVERDUE]}}):
        return fail("E-2", "该客户存在未结清贷款，请先结清后再销户")
    # E-3 未关闭外汇子户
    if db.fx_account.count_documents({"customer_id": cust["_id"],
                                      "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}):
        return fail("E-3", "该客户存在未关闭的外汇子户，请先关闭")
    if dec(acc["balance"]) > 0:  # E-5 余额未清零
        return fail("E-5", f"账户仍有余额 {dec(acc['balance'])}，请先取款或转账清零")

    def txn(s):
        a = db.account.find_one({"_id": acc["_id"]}, session=s)  # 事务内重读，防并发下带余额销户
        if a["status"] != C.ACCOUNT_NORMAL:
            return ("E-4", f"账户当前为「{C.ACCOUNT_STATUS_LABEL.get(a['status'])}」，不可销户")
        if dec(a["balance"]) > 0:
            return ("E-5", f"账户仍有余额 {dec(a['balance'])}，请先取款或转账清零")
        db.account.update_one({"_id": a["_id"]},
                              {"$set": {"status": C.ACCOUNT_CLOSED, "card_status": C.CARD_INVALID}}, session=s)
        write_txn(db, business_type=C.TXN_CLOSE_ACCOUNT, amount=0, user_id=g.user["_id"],
                  customer_id=cust["_id"], account_id=a["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CLOSE_ACCOUNT, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS, session=s)
        return None

    err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"account_no": account_no}, "销户成功，账户已关闭，卡片已失效")


# ---------- UC-108 客户信息更新 ----------
@bp.post("/update-customer")
@clerk
def update_customer():
    d = _body()
    key = (d.get("customer_no") or d.get("id_no") or d.get("phone") or "").strip()
    id_no = (d.get("id_no") or "").strip()
    db = get_db()

    cust = find_customer(db, customer_no=(d.get("customer_no") or "").strip() or None,
                         id_no=id_no or None, phone=(d.get("phone_query") or "").strip() or None)
    if not cust:  # E-1
        return fail("E-1", "客户不存在，请重新输入查询条件")
    if id_no and cust["id_no"] != id_no:  # E-2 身份核验失败
        return fail("E-2", "身份核验失败")

    updates = {}
    old = {}
    for f in ("phone", "address", "occupation"):
        if f in d and d[f] is not None:
            updates[f] = str(d[f]).strip()
            old[f] = cust.get(f)
    if "phone" in updates and updates["phone"] and not updates["phone"].isdigit():  # E-3 格式
        return fail("E-3", "手机号格式不合法")

    # E-4 关键信息（姓名/证件号）变更需二次确认
    for f in ("name", "new_id_no"):
        if d.get(f):
            if not d.get("confirm"):
                return fail("E-4", "涉及姓名/证件号等关键信息变更，请二次确认（confirm=true）并填写原因")
            target = "name" if f == "name" else "id_no"
            new_val = str(d[f]).strip()
            if target == "id_no":
                valid, reason = validate_id_no(cust.get("id_type", "身份证"), new_val)
                if not valid:
                    return fail("E-3", reason)
                if db.customer.find_one({"id_no": new_val, "_id": {"$ne": cust["_id"]}}):
                    return fail("E-3", "新证件号已被其他客户占用")
            old[target] = cust.get(target)
            updates[target] = new_val

    if not updates:
        return fail("E-REQ", "没有需要更新的字段", 400)

    db.customer.update_one({"_id": cust["_id"]}, {"$set": updates})
    write_audit(db, user_id=g.user["_id"], action="UPDATE_CUSTOMER", object_type="customer",
                object_id=cust["customer_no"], result=C.RESULT_SUCCESS,
                detail={"old": old, "new": updates, "reason": d.get("reason")})
    cust.update(updates)
    return ok({"customer": customer_view(cust)}, "客户信息更新成功")
