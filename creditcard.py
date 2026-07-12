"""信用卡业务子系统 UC-401 ~ UC-406（参与者：信用卡业务员）。

额度模型：available_limit = 可用额度；已用额度 = credit_limit - available_limit。
预借现金扣减可用额度，还款恢复可用额度。
"""
import re
from datetime import timedelta

from flask import Blueprint, request, g
from pymongo.errors import DuplicateKeyError

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, now, as_int,
    find_customer, check_account, write_txn, write_audit, verify_owner, norm_id, check_identity,
    get_param_dec, customer_view, txn_view, new_credit_card_no,
)

bp = Blueprint("creditcard", __name__, url_prefix="/api/creditcard")
clerk = require_role(C.ROLE_CREDIT)


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


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
        "reject_reason": cc.get("reject_reason"),
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


def _oldest_unpaid_bill(db, cc_id, session=None):
    # 最早未清账单优先还款，避免只还最新账单导致旧欠款永远还不上、销户被卡死
    return db.credit_card_bill.find_one(
        {"credit_card_id": cc_id, "status": {"$in": [C.BILL_UNPAID, C.BILL_PARTIAL]}},
        sort=[("bill_cycle", 1)], session=session)


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

    card_type = (d.get("card_type") or "普卡").strip()
    if card_type not in C.CARD_TYPES:  # E-OP 卡种必须合法
        return fail("E-OP", "卡种非法", 400)
    income = (str(d.get("monthly_income") or "")).strip()
    if income:  # 月收入选填，填了须为非负数字
        try:
            if D(income) < 0:
                return fail("E-VAL", "月收入不能为负", 400)
        except Exception:  # noqa: BLE001
            return fail("E-VAL", "月收入应为数字", 400)

    cc = {
        "card_no": new_credit_card_no(),
        "customer_id": cust["_id"],
        "user_id": g.user["_id"],
        "credit_limit": m(0),
        "available_limit": m(0),
        "bill_day": None,
        "repay_day": None,
        "status": C.CC_PENDING,
        "card_type": card_type,
        "occupation": (d.get("occupation") or "").strip()[:C.TEXT_MAX],
        "monthly_income": income,
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
        bill_day = as_int(d.get("bill_day"), 1)
        repay_day = as_int(d.get("repay_day"), 20)
        if not (1 <= bill_day <= 28 and 1 <= repay_day <= 28):
            return fail("E-DAY", "账单日/还款日应在 1~28 之间", 400)
        res = db.credit_card.update_one({"_id": cc["_id"], "status": C.CC_PENDING}, {"$set": {  # CAS 防并发双审
            "status": C.CC_ACTIVE, "credit_limit": m(limit), "available_limit": m(limit),
            "bill_day": bill_day, "repay_day": repay_day}})
        if res.matched_count == 0:
            return fail("E-STATE", "该申请已被处理，请刷新")
        write_audit(db, user_id=g.user["_id"], action="CC_APPROVE", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS, detail={"limit": str(limit)})
        cc = db.credit_card.find_one({"_id": cc["_id"]})
        return ok({"credit_card": cc_view(cc, db.customer.find_one({"_id": cc["customer_id"]}))},
                  "审批通过，信用卡已激活")
    elif decision == "REJECTED":  # E-2
        res = db.credit_card.update_one({"_id": cc["_id"], "status": C.CC_PENDING}, {"$set": {  # CAS 防并发双审
            "status": C.CC_REJECTED, "reject_reason": (d.get("reason") or "").strip()}})
        if res.matched_count == 0:
            return fail("E-STATE", "该申请已被处理，请刷新")
        write_audit(db, user_id=g.user["_id"], action="CC_REJECT", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS)
        cc = db.credit_card.find_one({"_id": cc["_id"]})
        return ok({"credit_card": cc_view(cc, db.customer.find_one({"_id": cc["customer_id"]}))},
                  "已拒绝该信用卡申请")
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
    if not re.fullmatch(r"20\d{4}", cycle) or not (1 <= int(cycle[4:]) <= 12):  # 账期须为 YYYYMM，否则打乱账单排序
        return fail("E-CYCLE", "账期格式应为 YYYYMM（月份 01-12），如 202607", 400)
    if db.credit_card_bill.find_one({"credit_card_id": cc["_id"], "bill_cycle": cycle}):
        return fail("E-DUP", f"账期 {cycle} 的账单已存在")
    min_rate = get_param_dec(db, C.P_CC_MIN_REPAY_RATE, "0.10")

    # 账期次月1号为归集上界：把所有【早于本账期截止】且尚未入账的取现/手续费滚入本期账单，
    # 既排除未来月份债务(不塞进过去账期)，又不会因先出过空账单而让后续取现永远无法入账。
    cy, cm = int(cycle[:4]), int(cycle[4:])
    nm, ny = (cm % 12) + 1, cy + (1 if cm == 12 else 0)
    cycle_end = now().replace(year=ny, month=nm, day=1, hour=0, minute=0, second=0, microsecond=0)

    def txn(s):
        # 汇总截止本账期尚未入账的取现/手续费流水（事务内查+标记，避免账单与入账不一致导致重复计费）
        unbilled = list(db.business_transaction.find({
            "related_id": cc["_id"],
            "business_type": {"$in": [C.TXN_CC_CASH, C.TXN_CC_CASH_FEE]},
            "bill_cycle": {"$exists": False},
            "txn_time": {"$lt": cycle_end}}, session=s))
        total = sum((dec(t["amount"]) for t in unbilled), D(0))
        details = [{"txn_no": t["txn_no"], "type": C.TXN_TYPE_LABEL.get(t["business_type"]),
                    "amount": float(dec(t["amount"])),
                    "time": t["txn_time"].strftime("%Y-%m-%d %H:%M:%S")} for t in unbilled]
        # 到期日 = 账期次月的还款日(repay_day)号，与审批录入的还款日一致，不随出账时间漂移
        rday = min(int(cc.get("repay_day") or 25), 28)
        due = now().replace(year=ny, month=nm, day=rday, hour=0, minute=0, second=0, microsecond=0)
        bill = {
            "credit_card_id": cc["_id"], "bill_cycle": cycle,
            "total_amount": m(total), "paid_amount": m(0), "min_repay": m(D(total * min_rate)),
            "due_date": due,
            "status": C.BILL_PAID if total == 0 else C.BILL_UNPAID,
            "txn_details": details, "repay_log": [], "created_at": now(),
        }
        bill["_id"] = db.credit_card_bill.insert_one(bill, session=s).inserted_id
        if unbilled:
            db.business_transaction.update_many(
                {"_id": {"$in": [t["_id"] for t in unbilled]}}, {"$set": {"bill_cycle": cycle}}, session=s)
        write_audit(db, user_id=g.user["_id"], action="CC_BILL", object_type="credit_card_bill",
                    object_id=card_no, result=C.RESULT_SUCCESS,
                    detail={"card_no": card_no, "cycle": cycle, "total": str(total)}, session=s)
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
    id_no = norm_id(d.get("id_no"))
    repay_type = (d.get("repay_type") or "PARTIAL").strip().upper()  # FULL/MIN/PARTIAL
    if repay_type not in ("FULL", "MIN", "PARTIAL"):  # 拼错不再静默当部分还款
        return fail("E-OP", "还款方式非法（全额/最低/部分）", 400)
    db = get_db()
    cc = db.credit_card.find_one({"card_no": card_no})
    if not cc:
        return fail("E-NOCARD", "未找到信用卡")
    bill = _oldest_unpaid_bill(db, cc["_id"])  # 优先还最早未清账单
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
    # 仅在本账单尚未还过款时约束首笔不得低于最低还款额；已部分还款(累计已满足)后补小额不再拦
    if repay_type == "PARTIAL" and dec(bill["paid_amount"]) == 0 and amount < min_repay <= remaining:
        return fail("E-2", f"首次还款不得低于最低还款额 {min_repay}")

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
        _cust, ierr = check_identity(db, cc2["customer_id"], id_no, session=s)  # 还款须核验持卡人身份
        if ierr:
            return None, ierr
        if acc["customer_id"] != cc2["customer_id"]:  # 还款储蓄账户须属持卡人本人，杜绝用他人余额清卡
            return None, ("E-OWNER", "还款储蓄账户不属于持卡人")
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) - pay)}}, session=s)
        # 恢复可用额度（不超过授信额度）
        new_avail = min(dec(cc2["available_limit"]) + pay, dec(cc2["credit_limit"]))
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(new_avail)}}, session=s)
        new_paid = dec(bill2["paid_amount"]) + pay
        new_status = C.BILL_PAID if new_paid >= dec(bill2["total_amount"]) else C.BILL_PARTIAL
        log = {"time": now().strftime("%Y-%m-%d %H:%M:%S"), "amount": m(pay),
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
    id_no = norm_id(d.get("id_no"))
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
        # 先校验出款账户（若指定），全部通过后再动账；避免校验失败却已扣额度/写流水（单机无事务时不可回滚）
        acc = None
        if payout_account:
            acc, err = check_account(db, payout_account, session=s)
            if err:
                return None, ("E-PAYOUT", f"出款账户不可用：{err[1]}")
            if not verify_owner(cust, acc):
                return None, ("E-OWNER", "出款账户不属于该持卡客户")
        new_avail = dec(cc2["available_limit"]) - amount - fee
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(new_avail)}}, session=s)
        t = write_txn(db, business_type=C.TXN_CC_CASH, amount=amount, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
        if fee > 0:
            write_txn(db, business_type=C.TXN_CC_CASH_FEE, amount=fee, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
        payout_msg = "现金出款"
        if acc is not None:  # 转入储蓄账户
            db.account.update_one({"_id": acc["_id"]},
                                  {"$set": {"balance": m(dec(acc["balance"]) + amount)}}, session=s)
            write_txn(db, business_type=C.TXN_CC_CASH_PAYOUT, amount=amount, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], account_id=acc["_id"], related_id=cc2["_id"], session=s)
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
    id_no = norm_id(d.get("id_no"))
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
        # 换新卡号但保留原风控状态：冻结卡补卡后仍冻结（需另行解冻），不借补卡绕过风控
        new_status = C.CC_FROZEN if cur == C.CC_FROZEN else C.CC_ACTIVE
        # CAS：仅当卡号/状态未变才换卡，防并发双击生成两个新卡号导致丢卡
        res = db.credit_card.update_one({"_id": cc["_id"], "card_no": cc["card_no"], "status": cur}, {
            "$set": {"card_no": new_card, "status": new_status},
            "$push": {"former_card_nos": {"card_no": cc["card_no"],
                                          "invalidated_at": now().strftime("%Y-%m-%d %H:%M:%S")}}})
        if res.matched_count == 0:
            return fail("E-2", "卡状态已变化，请刷新后重试")
        write_audit(db, user_id=g.user["_id"], action="CC_REISSUE", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS,
                    detail={"old_card": cc["card_no"], "new_card": new_card})
        tail = "（原卡号已作废，卡仍为冻结状态）" if new_status == C.CC_FROZEN else "（原卡号已作废）"
        return ok({"card_no": new_card}, f"补卡成功，新卡号 {new_card}{tail}")
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
        # 带出持卡人名下可用储蓄账号，供 UC-404 还款 / UC-405 出款直接取用
        v["repay_accounts"] = [a["account_no"] for a in
                               db.account.find({"customer_id": cc["customer_id"], "status": C.ACCOUNT_NORMAL})]
        result.append(v)
    return ok({"cards": result})
