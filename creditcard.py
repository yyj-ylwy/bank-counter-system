"""信用卡业务子系统 UC-401 ~ UC-406（参与者：信用卡业务员）。

额度模型：available_limit = 可用额度；已用额度 = credit_limit - available_limit。
预借现金扣减可用额度，还款恢复可用额度。
"""
from datetime import timedelta

from flask import Blueprint, request, g
from pymongo.errors import DuplicateKeyError

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, now,
    find_customer, check_account, write_txn, write_audit,
    get_param_dec, customer_view, txn_view, new_credit_card_no,
)

bp = Blueprint("creditcard", __name__, url_prefix="/api/creditcard")
clerk = require_role(C.ROLE_CREDIT)


def _body():
    return request.get_json(force=True, silent=True) or {}


def cc_view(cc, cust=None):
    return {
        "id": str(cc["_id"]),
        "card_no": cc["card_no"],
        "credit_limit": float(dec(cc.get("credit_limit"))),
        "available_limit": float(dec(cc.get("available_limit"))),
        "used": float(dec(cc.get("credit_limit")) - dec(cc.get("available_limit"))),
        "bill_day": cc.get("bill_day"),
        "repay_day": cc.get("repay_day"),
        "status": cc["status"],
        "status_label": C.CC_STATUS_LABEL.get(cc["status"], cc["status"]),
        "card_type": cc.get("card_type"),
        "customer_name": cust["name"] if cust else None,
        "customer_no": cust["customer_no"] if cust else None,
    }


def bill_view(b):
    return {
        "id": str(b["_id"]),
        "bill_cycle": b["bill_cycle"],
        "total_amount": float(dec(b["total_amount"])),
        "paid_amount": float(dec(b["paid_amount"])),
        "remaining": float(dec(b["total_amount"]) - dec(b["paid_amount"])),
        "min_repay": float(dec(b.get("min_repay", 0))),
        "due_date": b["due_date"].strftime("%Y-%m-%d") if b.get("due_date") else None,
        "status": b["status"],
        "status_label": C.BILL_STATUS_LABEL.get(b["status"], b["status"]),
        "txn_details": b.get("txn_details") or [],
        "repay_log": b.get("repay_log") or [],
    }


def _latest_unpaid_bill(db, cc_id, session=None):
    return db.credit_card_bill.find_one(
        {"credit_card_id": cc_id, "status": {"$in": [C.BILL_UNPAID, C.BILL_PARTIAL]}},
        sort=[("bill_cycle", -1)], session=session)


# ---------- UC-401 信用卡申请办理 ----------
@bp.post("/apply")
@clerk
def apply():
    d = _body()
    db = get_db()
    cust = find_customer(db, customer_no=(d.get("customer_no") or "").strip() or None,
                         id_no=(d.get("id_no") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户")
    if cust["status"] == C.CUSTOMER_BLACKLIST:  # E-1 严重不良记录
        return fail("E-1", "客户存在严重不良信用记录，拒绝办理")

    cc = {
        "card_no": new_credit_card_no(),
        "customer_id": cust["_id"],
        "user_id": g.user["_id"],
        "credit_limit": m(0),
        "available_limit": m(0),
        "bill_day": None,
        "repay_day": None,
        "status": C.CC_PENDING,
        "card_type": (d.get("card_type") or "普卡").strip(),
        "occupation": (d.get("occupation") or "").strip(),
        "monthly_income": str(d.get("monthly_income") or ""),
        "created_at": now(),
    }
    cc["_id"] = db.credit_card.insert_one(cc).inserted_id
    write_audit(db, user_id=g.user["_id"], action="CC_APPLY", object_type="credit_card",
                object_id=cc["card_no"], result=C.RESULT_SUCCESS)
    return ok({"credit_card": cc_view(cc, cust)}, "信用卡申请已提交，状态：待审核")


# ---------- UC-402 审核与额度设定 ----------
@bp.post("/approve")
@clerk
def approve():
    d = _body()
    card_no = (d.get("card_no") or "").strip()
    decision = (d.get("decision") or "").strip().upper()  # APPROVED / REJECTED
    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:
        return fail("E-NOCARD", "未找到信用卡申请")
    if cc["status"] != C.CC_PENDING:
        return fail("E-STATE", f"该申请当前为「{C.CC_STATUS_LABEL.get(cc['status'])}」，不可审核")

    if decision == "APPROVED":
        limit = D(d.get("credit_limit") or 0)
        cap = get_param_dec(db, C.P_CC_LIMIT_MAX, "50000")
        if limit <= 0 or limit > cap:  # E-1 超上限
            return fail("E-1", f"授信额度需在 0~{cap} 之间")
        bill_day = int(d.get("bill_day") or 1)
        repay_day = int(d.get("repay_day") or 20)
        if not (1 <= bill_day <= 28 and 1 <= repay_day <= 28):
            return fail("E-DAY", "账单日/还款日应在 1~28 之间", 400)
        db.credit_card.update_one({"_id": cc["_id"]}, {"$set": {
            "status": C.CC_ACTIVE, "credit_limit": m(limit), "available_limit": m(limit),
            "bill_day": bill_day, "repay_day": repay_day}})
        write_audit(db, user_id=g.user["_id"], action="CC_APPROVE", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS, detail={"limit": str(limit)})
        cc = db.credit_card.find_one({"_id": cc["_id"]})
        return ok({"credit_card": cc_view(cc, db.customer.find_one({"_id": cc["customer_id"]}))},
                  "审批通过，信用卡已激活")
    elif decision == "REJECTED":  # E-2
        db.credit_card.update_one({"_id": cc["_id"]}, {"$set": {
            "status": C.CC_REJECTED, "reject_reason": (d.get("reason") or "").strip()}})
        write_audit(db, user_id=g.user["_id"], action="CC_REJECT", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS)
        return ok(message="已拒绝该信用卡申请")
    return fail("E-OP", "审批结论非法（APPROVED/REJECTED）", 400)


# ---------- UC-403 账单生成 ----------
@bp.post("/bill")
@clerk
def generate_bill():
    d = _body()
    card_no = (d.get("card_no") or "").strip()
    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:
        return fail("E-NOCARD", "未找到信用卡")
    if cc["status"] in (C.CC_PENDING, C.CC_REJECTED):
        return fail("E-STATE", "该卡未激活，无法生成账单")

    cycle = (d.get("bill_cycle") or now().strftime("%Y%m")).strip()
    if db.credit_card_bill.find_one({"credit_card_id": cc["_id"], "bill_cycle": cycle}):
        return fail("E-DUP", f"账期 {cycle} 的账单已存在")
    min_rate = get_param_dec(db, C.P_CC_MIN_REPAY_RATE, "0.10")

    def txn(s):
        # 汇总本卡尚未入账的消费/取现/手续费流水（事务内查+标记，避免账单与入账不一致导致重复计费）
        unbilled = list(db.business_transaction.find({
            "related_id": cc["_id"],
            "business_type": {"$in": [C.TXN_CC_CASH, C.TXN_CC_CASH_FEE]},
            "bill_cycle": {"$exists": False}}, session=s))
        total = sum((dec(t["amount"]) for t in unbilled), D(0))
        details = [{"txn_no": t["txn_no"], "type": C.TXN_TYPE_LABEL.get(t["business_type"]),
                    "amount": float(dec(t["amount"])),
                    "time": t["txn_time"].strftime("%Y-%m-%d %H:%M:%S")} for t in unbilled]
        bill = {
            "credit_card_id": cc["_id"], "bill_cycle": cycle,
            "total_amount": m(total), "paid_amount": m(0), "min_repay": m(D(total * min_rate)),
            "due_date": (now() + timedelta(days=25)),
            "status": C.BILL_PAID if total == 0 else C.BILL_UNPAID,
            "txn_details": details, "repay_log": [], "created_at": now(),
        }
        bill["_id"] = db.credit_card_bill.insert_one(bill, session=s).inserted_id
        if unbilled:
            db.business_transaction.update_many(
                {"_id": {"$in": [t["_id"] for t in unbilled]}}, {"$set": {"bill_cycle": cycle}}, session=s)
        write_audit(db, user_id=g.user["_id"], action="CC_BILL", object_type="credit_card_bill",
                    object_id=cycle, result=C.RESULT_SUCCESS, detail={"total": str(total)}, session=s)
        return bill

    try:
        bill = run_in_transaction(txn)
    except DuplicateKeyError:  # 唯一索引兜底并发重复生成
        return fail("E-DUP", f"账期 {cycle} 的账单已存在")
    msg = "已生成空账单（本期无交易）" if dec(bill["total_amount"]) == 0 else "账单生成成功"  # E-1
    return ok({"bill": bill_view(bill)}, msg)


# ---------- UC-404 还款处理（全额/最低/部分）----------
@bp.post("/repay")
@clerk
def repay():
    d = _body()
    card_no = (d.get("card_no") or "").strip()
    account_no = (d.get("account_no") or "").strip()
    repay_type = (d.get("repay_type") or "PARTIAL").strip().upper()  # FULL/MIN/PARTIAL
    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:
        return fail("E-NOCARD", "未找到信用卡")
    bill = _latest_unpaid_bill(db, cc["_id"])
    if not bill:  # E-3 已结清
        return fail("E-3", "当前没有待还款账单，无需还款")

    remaining = dec(bill["total_amount"]) - dec(bill["paid_amount"])
    min_repay = dec(bill.get("min_repay", 0))
    if repay_type == "FULL":
        amount = remaining
    elif repay_type == "MIN":
        amount = min(min_repay, remaining)
    else:  # PARTIAL
        amount = D(d.get("amount") or 0)
    amount = D(amount)

    if amount <= 0:
        return fail("E-AMT", "还款金额必须大于零", 400)
    if amount > remaining:
        amount = remaining  # 不允许超过应还，自动收敛为全额
    if repay_type == "PARTIAL" and amount < min_repay <= remaining:  # E-2 低于最低
        return fail("E-2", f"还款金额低于最低还款额 {min_repay}")

    def txn(s):
        cc2 = db.credit_card.find_one({"_id": cc["_id"]}, session=s)      # 事务内重读，读改写一致
        bill2 = db.credit_card_bill.find_one({"_id": bill["_id"]}, session=s)
        if not bill2 or bill2["status"] == C.BILL_PAID:  # 并发下已被还清
            return None, ("E-3", "当前没有待还款账单，无需还款")
        pay = min(amount, dec(bill2["total_amount"]) - dec(bill2["paid_amount"]))  # 收敛到应还，防超收
        if pay <= 0:
            return None, ("E-3", "账单已结清，无需还款")
        acc, err = check_account(db, account_no, need_amount=pay, session=s)  # E-1 余额不足
        if err:
            return None, err
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) - pay)}}, session=s)
        # 恢复可用额度（不超过授信额度）
        new_avail = min(dec(cc2["available_limit"]) + pay, dec(cc2["credit_limit"]))
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(new_avail)}}, session=s)
        new_paid = dec(bill2["paid_amount"]) + pay
        new_status = C.BILL_PAID if new_paid >= dec(bill2["total_amount"]) else C.BILL_PARTIAL
        log = {"time": now().strftime("%Y-%m-%d %H:%M:%S"), "amount": float(pay),
               "type": repay_type, "account_no": account_no}
        db.credit_card_bill.update_one({"_id": bill2["_id"]},
                                       {"$set": {"paid_amount": m(new_paid), "status": new_status},
                                        "$push": {"repay_log": log}}, session=s)
        write_txn(db, business_type=C.TXN_CC_REPAY, amount=pay, user_id=g.user["_id"],
                  customer_id=cc2["customer_id"], account_id=acc["_id"], related_id=cc2["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CC_REPAY, object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(pay), "type": repay_type}, session=s)
        return new_status, None

    status, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    bill = db.credit_card_bill.find_one({"_id": bill["_id"]})
    cc = db.credit_card.find_one({"_id": cc["_id"]})
    msg = "还款成功，账单已结清" if status == C.BILL_PAID else "还款成功"
    return ok({"bill": bill_view(bill), "credit_card": cc_view(cc)}, msg)


# ---------- UC-405 预借现金处理 ----------
def _today_cash(db, cc_id, session=None):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = D(0)
    for t in db.business_transaction.find({"related_id": cc_id, "business_type": C.TXN_CC_CASH,
                                           "status": C.TXN_STATUS_SUCCESS,
                                           "txn_time": {"$gte": start}}, session=session):
        total += dec(t["amount"])
    return total


@bp.post("/cash-advance")
@clerk
def cash_advance():
    d = _body()
    card_no = (d.get("card_no") or "").strip()
    id_no = (d.get("id_no") or "").strip()
    amount = D(d.get("amount") or 0)
    payout_account = (d.get("payout_account") or "").strip()  # 空=现金，否则转入该储蓄账户
    if amount <= 0:
        return fail("E-AMT", "取现金额必须大于零", 400)

    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:
        return fail("E-NOCARD", "未找到信用卡")
    cust = db.customer.find_one({"_id": cc["customer_id"]})
    if not id_no:  # UC-405 前置条件：客户身份已核验
        return fail("E-REQ", "请提供证件号以核验客户身份", 400)
    if not cust or cust["id_no"] != id_no:  # E-3 身份核验失败
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CC_CASH, object_type="credit_card",
                    object_id=card_no, result=C.RESULT_FAILURE, detail={"reason": "身份核验失败"})
        return fail("E-3", "身份核验失败，证件与信用卡不一致")
    if cc["status"] != C.CC_ACTIVE:  # E-1 挂失/冻结/异常
        return fail("E-1", f"信用卡状态为「{C.CC_STATUS_LABEL.get(cc['status'])}」，不可取现")

    fee_rate = get_param_dec(db, C.P_CC_CASH_FEE_RATE, "0.01")
    daily_limit = get_param_dec(db, C.P_CC_CASH_DAILY_LIMIT, "20000")
    fee = D(amount * fee_rate)

    def txn(s):
        cc2 = db.credit_card.find_one({"_id": cc["_id"]}, session=s)  # 事务内重读额度，读改写一致
        if cc2["status"] != C.CC_ACTIVE:
            return None, ("E-1", f"信用卡状态为「{C.CC_STATUS_LABEL.get(cc2['status'])}」，不可取现")
        if amount + fee > dec(cc2["available_limit"]):  # E-2 额度不足
            return None, ("E-2", f"可用额度不足，取现+手续费需 {amount + fee}，可用 {dec(cc2['available_limit'])}")
        if _today_cash(db, cc2["_id"], s) + amount > daily_limit:  # E-2 超单日限额
            return None, ("E-2", f"超过预借现金单日限额 {daily_limit}")
        new_avail = dec(cc2["available_limit"]) - amount - fee
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(new_avail)}}, session=s)
        t = write_txn(db, business_type=C.TXN_CC_CASH, amount=amount, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
        if fee > 0:
            write_txn(db, business_type=C.TXN_CC_CASH_FEE, amount=fee, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
        payout_msg = "现金出款"
        if payout_account:  # 转入储蓄账户
            acc, err = check_account(db, payout_account, session=s)
            if err:
                return None, ("E-PAYOUT", f"出款账户不可用：{err[1]}")
            db.account.update_one({"_id": acc["_id"]},
                                  {"$set": {"balance": m(dec(acc["balance"]) + amount)}}, session=s)
            payout_msg = f"转入账户 {payout_account}"
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CC_CASH, object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount), "fee": str(fee), "payout": payout_msg}, session=s)
        return (new_avail, fee, payout_msg, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    new_avail, fee, payout_msg, t = res
    return ok({"available_limit": float(new_avail), "fee": float(fee), "payout": payout_msg,
               "txn": txn_view(t)}, "预借现金成功")


# ---------- UC-406 挂失/补卡与异常处理 ----------
@bp.post("/card")
@clerk
def card_op():
    d = _body()
    card_no = (d.get("card_no") or "").strip()
    id_no = (d.get("id_no") or "").strip()
    op = (d.get("op") or "").strip().upper()  # LOSS/REISSUE/FREEZE/UNFREEZE/EXCEPTION
    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:  # E-1
        return fail("E-1", "信用卡不存在，请重新核对卡号")
    cust = db.customer.find_one({"_id": cc["customer_id"]})
    if not id_no:
        return fail("E-REQ", "请提供证件号以核验客户身份", 400)
    if not cust or cust["id_no"] != id_no:  # E-3 身份核验失败
        write_audit(db, user_id=g.user["_id"], action=f"CC_{op or 'CARD'}", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_FAILURE, detail={"reason": "身份核验失败"})
        return fail("E-3", "身份核验失败")

    cur = cc["status"]
    updates = {}
    new_card = cc["card_no"]
    if op == "LOSS":
        if cur not in (C.CC_ACTIVE, C.CC_FROZEN):
            return fail("E-2", f"当前状态「{C.CC_STATUS_LABEL.get(cur)}」不可挂失")
        updates["status"] = C.CC_LOST
        msg = "信用卡已挂失"
    elif op == "FREEZE":
        if cur != C.CC_ACTIVE:
            return fail("E-2", "仅正常状态可冻结")
        updates["status"] = C.CC_FROZEN
        msg = "信用卡已冻结"
    elif op == "UNFREEZE":
        if cur != C.CC_FROZEN:
            return fail("E-2", "仅冻结状态可解冻")
        updates["status"] = C.CC_ACTIVE
        msg = "信用卡已解冻"
    elif op == "REISSUE":
        if cur not in (C.CC_LOST, C.CC_FROZEN, C.CC_ACTIVE):  # E-2 已销卡不可补
            return fail("E-2", f"当前状态「{C.CC_STATUS_LABEL.get(cur)}」不可补卡")
        new_card = new_credit_card_no()
        # 同一账户换新卡号：额度/账单历史留在同一记录，原卡号入历史并作废
        db.credit_card.update_one({"_id": cc["_id"]}, {
            "$set": {"card_no": new_card, "status": C.CC_ACTIVE},
            "$push": {"former_card_nos": {"card_no": cc["card_no"],
                                          "invalidated_at": now().strftime("%Y-%m-%d %H:%M:%S")}}})
        write_audit(db, user_id=g.user["_id"], action="CC_REISSUE", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS,
                    detail={"old_card": cc["card_no"], "new_card": new_card})
        return ok({"card_no": new_card}, f"补卡成功，新卡号 {new_card}（原卡号已作废）")
    elif op == "EXCEPTION":
        entry = {"time": now().strftime("%Y-%m-%d %H:%M:%S"),
                 "note": (d.get("note") or "").strip(), "operator": g.user["name"]}
        db.credit_card.update_one({"_id": cc["_id"]}, {"$push": {"exception_log": entry}})
        write_audit(db, user_id=g.user["_id"], action="CC_EXCEPTION", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS, detail=entry)
        return ok(message="异常交易登记已保存")
    else:
        return fail("E-OP", "操作类型非法", 400)

    db.credit_card.update_one({"_id": cc["_id"]}, {"$set": updates})
    write_audit(db, user_id=g.user["_id"], action=f"CC_{op}", object_type="credit_card",
                object_id=card_no, result=C.RESULT_SUCCESS,
                detail={"old_card": cc["card_no"], "new_card": new_card})
    return ok({"card_no": new_card}, msg)


# ---------- 供前端查询单卡信息 ----------
@bp.get("/query")
@clerk
def query():
    db = get_db()
    card_no = (request.args.get("card_no") or "").strip()
    customer_no = (request.args.get("customer_no") or "").strip()
    id_no = (request.args.get("id_no") or "").strip()
    cards = []
    if card_no:
        cc = db.credit_card.find_one({"card_no": card_no})
        if cc:
            cards = [cc]
    else:
        cust = find_customer(db, customer_no=customer_no or None, id_no=id_no or None)
        if cust:
            cards = list(db.credit_card.find({"customer_id": cust["_id"]}))
    if not cards:
        return fail("E-1", "未找到信用卡")
    result = []
    for cc in cards:
        cust = db.customer.find_one({"_id": cc["customer_id"]})
        v = cc_view(cc, cust)
        v["bills"] = [bill_view(b) for b in
                      db.credit_card_bill.find({"credit_card_id": cc["_id"]}).sort("bill_cycle", -1)]
        result.append(v)
    return ok({"cards": result})
