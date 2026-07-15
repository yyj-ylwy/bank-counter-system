"""信用卡业务子系统（参与者：信用卡业务员）——模仿汇丰香港重做。

四种卡：银联白金卡/银联钻石卡（人民币）、Visa Platinum/MasterCard World Elite（美元）。
额度模型：available_limit = 可用额度；已用额度(欠款) = credit_limit - available_limit。
消费扣减可用额度，还款恢复可用额度。银联卡消费返现入人民币储蓄账户；Visa/万事达消费得积分。
积分商城设在本模块内，可用积分兑换机票/酒店/接机等奖品。
"""
from decimal import Decimal

from flask import Blueprint, request, g

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from forex import cached_rate
from common import (
    ok, fail, D, dec, m, now, as_int,
    find_customer, resolve_credit_card, resolve_account_no, resolve_fx_account,
    check_account, write_txn, write_audit, get_param_dec, txn_view, new_credit_card_no,
)

bp = Blueprint("creditcard", __name__, url_prefix="/api/creditcard")
clerk = require_role(C.ROLE_CREDIT)

_CONSUME_CURRENCIES = ["CNY"] + C.SUPPORTED_CURRENCIES  # 消费可用币种：人民币 + 支持的外币


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


def _rate(x):
    """比例/费率转 Decimal（不能用 D()，D() 会把 0.024 量化成 0.02 丢精度）。"""
    return Decimal(str(x if x not in (None, "") else "0"))


def _spec(cc):
    return C.CARD_SPECS.get(cc.get("card_type"), {})


def _card_currency(cc):
    return _spec(cc).get("currency", "CNY")


# ============ 货币换算（经人民币中转）============
# 汇率一律取外汇模块「最近一次查询到」的中间价：本模块不主动调行情 API。
# 外汇页面(UC-306)查询/刷新某币种后，这里的折算金额（消费结算额、存款合计、提额上限等）自动跟着更新；
# 从未查过的币种则拒绝办理，并提示先去外汇模块查一次——不臆造汇率、也不偷偷去调 API。
_RATE_HINT = "，请先到外汇模块「UC-306 实时汇率查询」查询该币种汇率后再办理"


def _to_cny(db, amount, currency):
    """按最近一次查询到的汇率把 amount(currency) 折算为人民币。返回 (Decimal 或 None, err)。"""
    currency = (currency or "CNY").upper()
    if currency == "CNY":
        return D(amount), None
    r = cached_rate(currency)
    if not r or dec(r["mid"]) <= 0:
        return None, f"{currency} 尚无已查询的汇率{_RATE_HINT}"
    return D(dec(r["mid"]) * D(amount)), None


def _convert(db, amount, from_cur, to_cur):
    """货币换算：amount 从 from_cur 折算到 to_cur（经人民币中转，用最近一次查询到的汇率）。
    返回 (Decimal 或 None, err)。"""
    from_cur, to_cur = (from_cur or "CNY").upper(), (to_cur or "CNY").upper()
    if from_cur == to_cur:
        return D(amount), None
    cny, err = _to_cny(db, amount, from_cur)
    if err:
        return None, err
    if to_cur == "CNY":
        return cny, None
    r = cached_rate(to_cur)
    if not r or dec(r["mid"]) <= 0:
        return None, f"{to_cur} 尚无已查询的汇率{_RATE_HINT}"
    return D(cny / dec(r["mid"])), None


def _fund_account(db, ident, card_cur):
    """取该客户与本卡币种对应的资金账户：人民币→储蓄账户；外币→该币种外汇子户。
    还款扣款、审批看余额都用它。返回 (账号 或 None, 余额 float 或 None)。"""
    if card_cur == "CNY":
        account_no, _c, aerr = resolve_account_no(db, ident)
        if aerr:
            return None, None
        acc = db.account.find_one({"account_no": account_no})
        return (account_no, float(dec(acc["balance"]))) if acc else (None, None)
    fx, ferr = resolve_fx_account(db, ident, card_cur)
    if ferr:
        return None, None
    return fx["fx_account_no"], float(dec(fx["balance"]))


def _total_deposit_cny(db, customer_id, session=None):
    """客户名下总存款折人民币 = 储蓄账户余额 + 各外汇子户余额按最近一次查询汇率折 CNY。
    返回 (total, missing)：missing 为「有余额但汇率从未查过」的币种，非空说明折算不完整，
    调用方不得据此下结论（否则会少算存款、把本该通过的提额误拒）。余额为 0 的子户不需要汇率。"""
    total = D(0)
    missing = []
    for a in db.account.find({"customer_id": customer_id, "status": {"$ne": C.ACCOUNT_CLOSED}}, session=session):
        total += dec(a["balance"])
    for f in db.fx_account.find({"customer_id": customer_id,
                                 "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}, session=session):
        bal = dec(f["balance"])
        if bal == 0:
            continue
        cny, _err = _to_cny(db, bal, f["currency"])
        if cny is None:
            missing.append(f["currency"])
        else:
            total += cny
    return total, missing


# ============ 视图 ============
def cc_view(cc, cust=None):
    spec = _spec(cc)
    cur = spec.get("currency", "CNY")
    v = {
        "id": str(cc["_id"]),
        "card_no": cc["card_no"],
        "card_type": cc.get("card_type"),
        "network": spec.get("network"),
        "currency": cur,
        "credit_limit": float(dec(cc.get("credit_limit"))),
        "available_limit": float(dec(cc.get("available_limit"))),
        "used": float(dec(cc.get("credit_limit")) - dec(cc.get("available_limit"))),
        "cashback_rate": float(_rate(spec.get("cashback_rate"))),
        "points_per_unit": int(_rate(spec.get("points_per_unit"))),
        "fx_fee_rate": 0.0 if spec.get("waive_fx_fee") else float(_rate(spec.get("fx_fee_rate"))),
        "waive_fx_fee": bool(spec.get("waive_fx_fee")),
        "bill_day": cc.get("bill_day"),
        "repay_day": cc.get("repay_day"),
        "status": cc["status"],
        "status_label": C.CC_STATUS_LABEL.get(cc["status"], cc["status"]),
        "reject_reason": cc.get("reject_reason"),
        "customer_name": cust["name"] if cust else None,
        "customer_no": cust["customer_no"] if cust else None,
        "points": int((cust or {}).get("points", 0) or 0) if cust else None,
    }
    lr = cc.get("limit_req")
    if lr:
        v["limit_req"] = {"new_limit": float(dec(lr.get("new_limit"))), "status": lr.get("status"),
                          "requested_at": lr.get("requested_at"), "reason": lr.get("reason")}
    return v


# ---------- UC-401 信用卡申请办理 ----------
@bp.post("/apply")
@clerk
def apply():
    d = _body()
    db = get_db()
    cust = find_customer(db, ident=(d.get("ident") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户")
    if cust["status"] == C.CUSTOMER_BLACKLIST:  # E-1 严重不良记录
        return fail("E-1", "客户存在严重不良信用记录，拒绝办理")

    card_type = (d.get("card_type") or "").strip()
    if card_type not in C.CARD_TYPES:  # E-OP 卡种必须合法
        return fail("E-OP", f"卡种非法，仅支持：{'、'.join(C.CARD_TYPES)}", 400)
    income = (str(d.get("monthly_income") or "")).strip()
    if income:  # 月收入选填，填了须为非负数字
        try:
            if D(income) < 0:
                return fail("E-VAL", "月收入不能为负", 400)
        except Exception:  # noqa: BLE001
            return fail("E-VAL", "月收入应为数字", 400)

    # 每人每种卡最多一张（非终态）：同种卡已在办理/使用中则拒绝，保证「身份+卡种」唯一定位
    if db.credit_card.count_documents({"customer_id": cust["_id"], "card_type": card_type,
                                       "status": {"$in": C.CC_NON_TERMINAL}}):
        return fail("E-DUP", f"该客户已持有一张「{card_type}」，每种卡每人限一张")

    spec = C.CARD_SPECS[card_type]
    cc = {
        "card_no": new_credit_card_no(),
        "customer_id": cust["_id"],
        "user_id": g.user["_id"],
        "card_type": card_type,
        "currency": spec["currency"],
        "credit_limit": m(0),
        "available_limit": m(0),
        "bill_day": None,
        "repay_day": None,
        "status": C.CC_PENDING,
        "occupation": (d.get("occupation") or "").strip()[:C.TEXT_MAX],
        "monthly_income": income,
        "created_at": now(),
    }
    cc["_id"] = db.credit_card.insert_one(cc).inserted_id
    write_audit(db, user_id=g.user["_id"], action="CC_APPLY", object_type="credit_card",
                object_id=cc["card_no"], result=C.RESULT_SUCCESS, detail={"card_type": card_type})
    return ok({"credit_card": cc_view(cc, cust)},
              f"{card_type} 申请已提交（默认额度 {spec['default_limit']} {spec['currency']}），状态：待审核")


# ---------- UC-402 第一步：确认审批事项（只读）：这张卡客户是要批卡还是提额？----------
@bp.get("/approve-quote")
@clerk
def approve_quote():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()
    card_type = (request.args.get("card_type") or "").strip()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    spec = _spec(cc)
    card_cur = _card_currency(cc)
    lr = cc.get("limit_req")
    data = {"credit_card": cc_view(cc, cust), "currency": card_cur}

    if cc["status"] == C.CC_PENDING:            # 事项一：新卡申请待审
        data.update({"purpose": "NEW_CARD", "purpose_label": "新卡申请审批",
                     "grant_limit": float(D(spec.get("default_limit", "0")))})
        return ok(data)

    if lr and lr.get("status") == "PENDING":    # 事项二：提额申请待审
        new_limit = dec(lr["new_limit"])
        ratio = get_param_dec(db, C.P_CC_LIMIT_DEPOSIT_RATIO, "0.30")
        acct_no, acct_bal = _fund_account(db, ident, card_cur)   # 本卡币种对应账户余额
        # 审批依据与 approve() 完全一致：新额度折人民币 ≤ 存款合计(折人民币) × 比例
        new_limit_cny, cerr = _to_cny(db, new_limit, card_cur)
        deposit_cny, missing = _total_deposit_cny(db, cc["customer_id"])
        cap_cny = D(deposit_cny * ratio)
        data.update({
            "purpose": "INCREASE", "purpose_label": "提高信用额审批",
            "current_limit": float(dec(cc["credit_limit"])),
            "new_limit": float(new_limit),
            "acct_no": acct_no, "acct_balance": acct_bal,
            "ratio": float(ratio), "deposit_cny": float(deposit_cny), "cap_cny": float(cap_cny),
            "new_limit_cny": None if cerr else float(new_limit_cny),
            "requested_at": lr.get("requested_at"), "reason": lr.get("reason"),
        })
        if cerr or missing:  # 折算不完整则给不出建议，如实说明（不臆造绿灯）
            why = cerr or f"存款中 {'、'.join(missing)} 尚无已查询的汇率，存款合计折算不完整{_RATE_HINT}"
            data.update({"advise": None, "advise_label": "无法判定", "advise_reason": why})
        elif new_limit_cny <= cap_cny:
            data.update({"advise": "APPROVE", "advise_label": "建议通过",
                         "advise_reason": f"新额度折 {new_limit_cny} 元，未超过存款({deposit_cny}元)的"
                                          f"{ratio * 100:.0f}%（上限 {cap_cny} 元）"})
        else:
            data.update({"advise": "REJECT", "advise_label": "建议拒绝",
                         "advise_reason": f"新额度折 {new_limit_cny} 元，超过存款({deposit_cny}元)的"
                                          f"{ratio * 100:.0f}%（上限 {cap_cny} 元），超出 {D(new_limit_cny - cap_cny)} 元"})
        return ok(data)

    data.update({"purpose": "NONE", "purpose_label": "无待审批事项",
                 "note": f"该卡当前为「{C.CC_STATUS_LABEL.get(cc['status'], cc['status'])}」，"
                         f"既无待审新卡申请，也无待审提额申请"})
    return ok(data)


# ---------- UC-402 第二步：审批（新卡审批 与 提额审批 同一处）----------
@bp.post("/approve")
@clerk
def approve():
    d = _body()
    ident = (d.get("ident") or "").strip()
    card_type = (d.get("card_type") or "").strip()
    decision = (d.get("decision") or "").strip().upper()  # APPROVED / REJECTED
    if decision not in ("APPROVED", "REJECTED"):
        return fail("E-OP", "审批结论非法（APPROVED/REJECTED）", 400)
    db = get_db()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    card_no = cc["card_no"]
    spec = _spec(cc)
    lr = cc.get("limit_req")
    has_pending_increase = bool(lr and lr.get("status") == "PENDING")

    # ===== 情形一：新卡申请审批 =====
    if cc["status"] == C.CC_PENDING:
        if decision == "APPROVED":
            limit = D(spec.get("default_limit", "0"))  # 按卡种默认授信额度激活
            bill_day = as_int(d.get("bill_day"), 1)
            repay_day = as_int(d.get("repay_day"), 20)
            if not (1 <= bill_day <= 28 and 1 <= repay_day <= 28):
                return fail("E-DAY", "账单日/还款日应在 1~28 之间", 400)
            res = db.credit_card.update_one({"_id": cc["_id"], "status": C.CC_PENDING}, {"$set": {
                "status": C.CC_ACTIVE, "credit_limit": m(limit), "available_limit": m(limit),
                "bill_day": bill_day, "repay_day": repay_day}})
            if res.matched_count == 0:
                return fail("E-STATE", "该申请已被处理，请刷新")
            write_audit(db, user_id=g.user["_id"], action="CC_APPROVE", object_type="credit_card",
                        object_id=card_no, result=C.RESULT_SUCCESS, detail={"limit": str(limit)})
            cc = db.credit_card.find_one({"_id": cc["_id"]})
            return ok({"credit_card": cc_view(cc, cust)},
                      f"审批通过，{cc['card_type']} 已激活，授信额度 {limit} {spec['currency']}")
        # REJECTED
        res = db.credit_card.update_one({"_id": cc["_id"], "status": C.CC_PENDING}, {"$set": {
            "status": C.CC_REJECTED, "reject_reason": (d.get("reason") or "").strip()}})
        if res.matched_count == 0:
            return fail("E-STATE", "该申请已被处理，请刷新")
        write_audit(db, user_id=g.user["_id"], action="CC_REJECT", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS)
        cc = db.credit_card.find_one({"_id": cc["_id"]})
        return ok({"credit_card": cc_view(cc, cust)}, "已拒绝该信用卡申请")

    # ===== 情形二：提额申请审批 =====
    if has_pending_increase:
        new_limit = dec(lr["new_limit"])
        if decision == "APPROVED":
            # 提额上限校验：新额度折人民币 ≤ 存款 × 比例（默认 30%）
            ratio = get_param_dec(db, C.P_CC_LIMIT_DEPOSIT_RATIO, "0.30")
            new_limit_cny, cerr = _to_cny(db, new_limit, _card_currency(cc))
            if cerr:
                return fail("E-RATE", cerr)
            # 存款折算不完整就不能下结论：少算存款会把本该通过的提额误拒
            deposit_cny, missing = _total_deposit_cny(db, cc["customer_id"])
            if missing:
                return fail("E-RATE", f"该客户存款含 {'、'.join(missing)}，尚无已查询的汇率，"
                                      f"无法核算存款合计{_RATE_HINT}")
            cap = D(deposit_cny * ratio)
            if new_limit_cny > cap:
                return fail("E-1", f"提额被拒：新额度折 {new_limit_cny} 元，超过存款({deposit_cny}元)的"
                                    f"{ratio * 100:.0f}%（上限 {cap} 元）")

            def txn(s):
                cc2 = db.credit_card.find_one({"_id": cc["_id"]}, session=s)  # 事务内重读，读改写一致
                if not (cc2.get("limit_req") and cc2["limit_req"].get("status") == "PENDING"):
                    return None, ("E-STATE", "该提额申请已被处理，请刷新")
                old_limit = dec(cc2["credit_limit"])
                delta = new_limit - old_limit  # 授信额度增量，同步加到可用额度
                db.credit_card.update_one({"_id": cc2["_id"]}, {"$set": {
                    "credit_limit": m(new_limit),
                    "available_limit": m(dec(cc2["available_limit"]) + delta),
                    "limit_req.status": "APPROVED",
                    "limit_req.approved_at": now().strftime("%Y-%m-%d %H:%M:%S")}}, session=s)
                write_audit(db, user_id=g.user["_id"], action="CC_LIMIT_APPROVE", object_type="credit_card",
                            object_id=card_no, result=C.RESULT_SUCCESS,
                            detail={"old": str(old_limit), "new": str(new_limit)}, session=s)
                return True, None
            _r, err = run_in_transaction(txn)
            if err:
                return fail(err[0], err[1])
            cc = db.credit_card.find_one({"_id": cc["_id"]})
            return ok({"credit_card": cc_view(cc, cust)},
                      f"提额审批通过，新授信额度 {new_limit} {_card_currency(cc)}")
        # REJECTED 提额
        db.credit_card.update_one({"_id": cc["_id"]}, {"$set": {
            "limit_req.status": "REJECTED", "limit_req.reason": (d.get("reason") or "").strip()}})
        write_audit(db, user_id=g.user["_id"], action="CC_LIMIT_REJECT", object_type="credit_card",
                    object_id=card_no, result=C.RESULT_SUCCESS)
        cc = db.credit_card.find_one({"_id": cc["_id"]})
        return ok({"credit_card": cc_view(cc, cust)}, "已拒绝该提额申请")

    return fail("E-STATE", f"该卡当前为「{C.CC_STATUS_LABEL.get(cc['status'])}」且无待审提额申请，无需审批")


# ---------- UC-403 提高信用额申请（持卡人发起，交由 UC-402 同一处审批）----------
@bp.post("/increase-limit")
@clerk
def increase_limit():
    d = _body()
    ident = (d.get("ident") or "").strip()
    card_type = (d.get("card_type") or "").strip()
    new_limit = D(d.get("new_limit") or 0)
    db = get_db()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    if cc["status"] != C.CC_ACTIVE:
        return fail("E-STATE", f"信用卡状态为「{C.CC_STATUS_LABEL.get(cc['status'])}」，不可申请提额")
    if new_limit <= dec(cc["credit_limit"]):
        return fail("E-1", f"新额度须高于当前授信额度 {dec(cc['credit_limit'])} {_card_currency(cc)}")
    if cc.get("limit_req") and cc["limit_req"].get("status") == "PENDING":
        return fail("E-2", "已有待审批的提额申请，请等待审批")
    lr = {"new_limit": m(new_limit), "status": "PENDING",
          "requested_at": now().strftime("%Y-%m-%d %H:%M:%S"), "reason": (d.get("reason") or "").strip()}
    db.credit_card.update_one({"_id": cc["_id"]}, {"$set": {"limit_req": lr}})
    write_audit(db, user_id=g.user["_id"], action="CC_LIMIT_REQUEST", object_type="credit_card",
                object_id=cc["card_no"], result=C.RESULT_SUCCESS, detail={"new_limit": str(new_limit)})
    cc = db.credit_card.find_one({"_id": cc["_id"]})
    return ok({"credit_card": cc_view(cc, cust)},
              f"提额申请已提交（{new_limit} {_card_currency(cc)}），待信用卡业务员审批")


# ---------- UC-404 模拟消费（自定义币种+金额，真实扣减信用额）----------
@bp.post("/consume")
@clerk
def consume():
    d = _body()
    ident = (d.get("ident") or "").strip()
    card_type = (d.get("card_type") or "").strip()
    currency = (d.get("currency") or "").strip().upper()
    amount = D(d.get("amount") or 0)
    merchant = (d.get("merchant") or "").strip()[:C.TEXT_MAX]
    if currency not in _CONSUME_CURRENCIES:
        return fail("E-CUR", f"不支持的消费币种，仅支持：{'/'.join(_CONSUME_CURRENCIES)}", 400)
    if amount <= 0:
        return fail("E-AMT", "消费金额必须大于零", 400)

    db = get_db()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    if cc["status"] != C.CC_ACTIVE:  # E-1 非正常卡不可消费
        return fail("E-1", f"信用卡状态为「{C.CC_STATUS_LABEL.get(cc['status'])}」，不可消费")
    spec = _spec(cc)
    if not spec:  # 历史卡种（已不在产品目录）：无币种/费率/奖励定义，不予受理（否则取 spec 字段会崩 500）
        return fail("E-CARDTYPE", f"该卡为历史卡种「{cc.get('card_type')}」，不在当前产品目录内，请为客户申请新卡种")
    card_cur = spec["currency"]

    card_amount, cerr = _convert(db, amount, currency, card_cur)  # 折算为本卡币种
    if cerr or card_amount is None or card_amount <= 0:
        return fail("E-RATE", cerr or "消费金额折算后不足 0.01，请增加金额")
    is_foreign = currency != card_cur
    fee = D(0)
    if is_foreign and not spec.get("waive_fx_fee"):  # 外币交易手续费（World Elite 免除）
        fee = D(card_amount * _rate(spec.get("fx_fee_rate")))
    total = card_amount + fee

    def txn(s):
        cc2 = db.credit_card.find_one({"_id": cc["_id"]}, session=s)  # 事务内重读额度
        if cc2["status"] != C.CC_ACTIVE:
            return None, ("E-1", "信用卡状态已变化，不可消费")
        if total > dec(cc2["available_limit"]):  # E-2 禁止超出信用额度的交易
            return None, ("E-2", f"超出可用额度：本次需 {total} {card_cur}，可用 {dec(cc2['available_limit'])} {card_cur}")
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(dec(cc2["available_limit"]) - total)}}, session=s)
        t = write_txn(db, business_type=C.TXN_CC_CONSUME, amount=card_amount, user_id=g.user["_id"],
                      customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
        db.business_transaction.update_one({"_id": t["_id"]}, {"$set": {
            "currency": card_cur, "orig_currency": currency, "orig_amount": m(amount),
            "merchant": merchant, "is_foreign": is_foreign}}, session=s)
        if fee > 0:
            tf = write_txn(db, business_type=C.TXN_CC_FX_FEE, amount=fee, user_id=g.user["_id"],
                           customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
            db.business_transaction.update_one({"_id": tf["_id"]}, {"$set": {"currency": card_cur}}, session=s)

        reward = {"type": None, "cashback": 0.0, "points": 0}
        cashback_rate = _rate(spec.get("cashback_rate"))
        points_per = _rate(spec.get("points_per_unit"))
        if cashback_rate > 0:  # 银联卡：消费返现入人民币储蓄账户
            cashback = D(card_amount * cashback_rate)  # 本卡币种为 CNY，返现即人民币
            sav = db.account.find_one({"customer_id": cc2["customer_id"], "status": C.ACCOUNT_NORMAL}, session=s)
            if sav and cashback > 0:
                db.account.update_one({"_id": sav["_id"]},
                                      {"$set": {"balance": m(dec(sav["balance"]) + cashback)}}, session=s)
                tc = write_txn(db, business_type=C.TXN_CC_CASHBACK, amount=cashback, user_id=g.user["_id"],
                               customer_id=cc2["customer_id"], account_id=sav["_id"],
                               related_id=cc2["_id"], session=s)
                db.business_transaction.update_one({"_id": tc["_id"]}, {"$set": {"currency": "CNY"}}, session=s)
                reward = {"type": "CASHBACK", "cashback": float(cashback), "points": 0,
                          "account_no": sav["account_no"]}
        elif points_per > 0:  # Visa/万事达：每消费 1 美元得积分
            pts = int(card_amount * points_per)  # 本卡币种为 USD
            if pts > 0:
                db.customer.update_one({"_id": cc2["customer_id"]}, {"$inc": {"points": pts}}, session=s)
                reward = {"type": "POINTS", "cashback": 0.0, "points": pts}
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CC_CONSUME, object_type="credit_card",
                    object_id=cc["card_no"], result=C.RESULT_SUCCESS,
                    detail={"amount": f"{amount} {currency}", "card_amount": f"{card_amount} {card_cur}",
                            "fee": str(fee), "reward": str(reward)}, session=s)
        new_avail = dec(cc2["available_limit"]) - total
        return {"card_amount": float(card_amount), "fee": float(fee), "card_currency": card_cur,
                "orig": f"{amount} {currency}", "available_limit": float(new_avail),
                "reward": reward, "txn": txn_view(t)}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    tail = ""
    if res["reward"]["type"] == "CASHBACK":
        tail = f"，返现 {res['reward']['cashback']} 元已入储蓄账户 {res['reward']['account_no']}"
    elif res["reward"]["type"] == "POINTS":
        tail = f"，获得 {res['reward']['points']} 积分"
    return ok(res, f"消费成功，扣减可用额度 {res['card_amount'] + res['fee']} {res['card_currency']}{tail}")


# ---------- UC-405 第一步：确认卡种后试算各还款方式的应还金额（只读，不动账）----------
@bp.get("/repay-quote")
@clerk
def repay_quote():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()
    card_type = (request.args.get("card_type") or "").strip()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    card_cur = _card_currency(cc)
    outstanding = max(dec(cc["credit_limit"]) - dec(cc["available_limit"]), D(0))
    min_rate = get_param_dec(db, C.P_CC_MIN_REPAY_RATE, "0.10")
    interest_rate = get_param_dec(db, C.P_CC_MIN_INTEREST_RATE, "0.05")
    min_amount = D(outstanding * min_rate)          # 按期最低额还款应还
    # 按最低额还款后，剩余本金本月将计的循环利息（供柜员向客户告知）
    min_interest = D((outstanding - min_amount) * interest_rate) if outstanding > min_amount else D(0)

    # 还款资金来源：人民币卡→人民币储蓄账户；美元卡→美元外汇子户
    fund_no, fund_bal = _fund_account(db, ident, card_cur)

    return ok({"credit_card": cc_view(cc, cust), "currency": card_cur,
               "outstanding": float(outstanding),      # 提前还款(全额结清)应还
               "min_amount": float(min_amount),        # 按期最低额还款应还
               "min_interest": float(min_interest),    # 最低额还款后剩余本金本月利息
               "min_rate": float(min_rate), "interest_rate": float(interest_rate),
               "fund_account": fund_no, "fund_balance": fund_bal})


# ---------- UC-405 第二步：还款（提前全额/提前指定金额/按期最低额；人民币账户还人民币卡，美元账户还美元卡）----------
@bp.post("/repay")
@clerk
def repay():
    d = _body()
    ident = (d.get("ident") or "").strip()
    card_type = (d.get("card_type") or "").strip()
    repay_type = (d.get("repay_type") or "").strip().upper()  # FULL(提前全额) / SCHEDULED(提前指定金额) / MIN(按期最低额)
    if repay_type not in ("FULL", "SCHEDULED", "MIN"):
        return fail("E-OP", "还款方式非法（提前全额FULL/提前指定金额SCHEDULED/按期最低额MIN）", 400)
    db = get_db()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    card_cur = _card_currency(cc)
    outstanding = dec(cc["credit_limit"]) - dec(cc["available_limit"])
    if outstanding <= 0:  # E-3 无欠款
        return fail("E-3", "该卡当前无欠款，无需还款")

    min_rate = get_param_dec(db, C.P_CC_MIN_REPAY_RATE, "0.10")
    interest_rate = get_param_dec(db, C.P_CC_MIN_INTEREST_RATE, "0.05")
    min_amount = D(outstanding * min_rate)
    if repay_type == "FULL":
        pay = outstanding
    elif repay_type == "MIN":
        pay = min(outstanding, min_amount if min_amount > 0 else outstanding)
    else:  # SCHEDULED 提前还款(指定金额)：还指定金额（不低于最低还款额，多退到不超过欠款）
        pay = D(d.get("amount") or 0)
        if pay <= 0:
            return fail("E-AMT", "指定还款金额必须大于零", 400)
        if pay > outstanding:
            pay = outstanding  # 多还的部分不扣，等同退回储蓄账户
        elif pay < min_amount:
            return fail("E-2", f"指定还款金额不得低于最低还款额 {min_amount} {card_cur}")

    # 还款资金来源：人民币卡→人民币储蓄账户；美元卡→美元外汇子户
    if card_cur == "CNY":
        account_no, _c, aerr = resolve_account_no(db, ident)
        if aerr:
            return fail(aerr[0], aerr[1])
        fund = ("SAVINGS", account_no)
    else:
        fx, ferr = resolve_fx_account(db, ident, card_cur)
        if ferr:
            return fail(ferr[0], ferr[1])
        fund = ("FX", fx["fx_account_no"])

    def txn(s):
        cc2 = db.credit_card.find_one({"_id": cc["_id"]}, session=s)  # 事务内重读，读改写一致
        out2 = dec(cc2["credit_limit"]) - dec(cc2["available_limit"])
        if out2 <= 0:
            return None, ("E-3", "该卡当前无欠款，无需还款")
        pay2 = min(pay, out2)  # 收敛到实际欠款，防超还
        # 扣还款资金
        if fund[0] == "SAVINGS":
            acc, aerr = check_account(db, fund[1], need_amount=pay2, session=s)  # E-BAL 余额不足
            if aerr:
                return None, aerr
            if acc["customer_id"] != cc2["customer_id"]:
                return None, ("E-OWNER", "还款账户不属于持卡人")
            db.account.update_one({"_id": acc["_id"]},
                                  {"$set": {"balance": m(dec(acc["balance"]) - pay2)}}, session=s)
            acc_id = acc["_id"]
        else:  # FX 美元子户
            fxa = db.fx_account.find_one({"fx_account_no": fund[1]}, session=s)
            if not fxa or fxa["customer_id"] != cc2["customer_id"]:
                return None, ("E-OWNER", "还款外汇子户不属于持卡人")
            if fxa["status"] != C.FX_NORMAL:
                return None, ("E-FROZEN", f"外汇子户状态为「{C.FX_STATUS_LABEL.get(fxa['status'])}」，不可用于还款")
            if dec(fxa["balance"]) < pay2:
                return None, ("E-BAL", f"美元子户余额不足，当前 {dec(fxa['balance'])} {card_cur}，需 {pay2}")
            db.fx_account.update_one({"_id": fxa["_id"]},
                                     {"$set": {"balance": m(dec(fxa["balance"]) - pay2)}}, session=s)
            acc_id = fxa["base_account_id"]
        # 恢复可用额度（不超过授信额度）
        new_avail = min(dec(cc2["available_limit"]) + pay2, dec(cc2["credit_limit"]))
        # 按期最低额还款：剩余本金按月利率累计利息，计入欠款（占用额度）
        interest = D(0)
        remaining = out2 - pay2
        if repay_type == "MIN" and remaining > 0 and interest_rate > 0:
            interest = D(remaining * interest_rate)
            new_avail = new_avail - interest
        db.credit_card.update_one({"_id": cc2["_id"]},
                                  {"$set": {"available_limit": m(new_avail)}}, session=s)
        write_txn(db, business_type=C.TXN_CC_REPAY, amount=pay2, user_id=g.user["_id"],
                  customer_id=cc2["customer_id"], account_id=acc_id, related_id=cc2["_id"], session=s)
        if interest > 0:
            ti = write_txn(db, business_type=C.TXN_CC_INTEREST, amount=interest, user_id=g.user["_id"],
                           customer_id=cc2["customer_id"], related_id=cc2["_id"], session=s)
            db.business_transaction.update_one({"_id": ti["_id"]}, {"$set": {"currency": card_cur}}, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CC_REPAY, object_type="credit_card",
                    object_id=cc["card_no"], result=C.RESULT_SUCCESS,
                    detail={"type": repay_type, "pay": str(pay2), "interest": str(interest),
                            "fund": f"{fund[0]}:{fund[1]}"}, session=s)
        return {"pay": float(pay2), "interest": float(interest),
                "outstanding": float(dec(cc2["credit_limit"]) - new_avail),
                "available_limit": float(new_avail), "currency": card_cur, "fund": fund[1]}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    cc = db.credit_card.find_one({"_id": cc["_id"]})
    msg = f"还款成功，还款 {res['pay']} {res['currency']}（来源 {res['fund']}）"
    if res["interest"] > 0:
        msg += f"；剩余本金计息 {res['interest']} {res['currency']}（月利率{float(interest_rate) * 100:.0f}%）"
    if dec(cc["credit_limit"]) - dec(cc["available_limit"]) <= 0:
        msg += "，欠款已结清"
    return ok({"credit_card": cc_view(cc, cust), **res}, msg)


# ---------- UC-406 本月消费记录（每张卡：本月消费明细 + 剩余额度）----------
@bp.get("/records")
@clerk
def records():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()
    card_type = (request.args.get("card_type") or "").strip()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    mstart = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # 本月 1 号 0 点
    txns = list(db.business_transaction.find({
        "related_id": cc["_id"],
        "business_type": {"$in": [C.TXN_CC_CONSUME, C.TXN_CC_FX_FEE, C.TXN_CC_CASHBACK,
                                  C.TXN_CC_REPAY, C.TXN_CC_INTEREST]},
        "txn_time": {"$gte": mstart}}).sort("txn_time", -1).limit(500))
    recs, consume_total = [], D(0)
    for t in txns:
        v = txn_view(t)
        v["currency"] = t.get("currency")
        v["orig"] = (f"{dec(t['orig_amount'])} {t['orig_currency']}"
                     if t.get("orig_amount") is not None and t.get("orig_currency") else None)
        v["merchant"] = t.get("merchant")
        if t["business_type"] == C.TXN_CC_CONSUME:
            consume_total += dec(t["amount"])
        recs.append(v)
    write_audit(db, user_id=g.user["_id"], action="CC_RECORDS", object_type="credit_card",
                object_id=cc["card_no"], result=C.RESULT_SUCCESS)
    return ok({"credit_card": cc_view(cc, cust), "month": mstart.strftime("%Y-%m"),
               "records": recs, "consume_total": float(consume_total)})


# ---------- 积分商城：奖品清单 / 兑换记录 ----------
@bp.get("/mall")
@clerk
def mall():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()
    data = {"prizes": C.CC_PRIZES}
    if ident:
        cust = find_customer(db, ident=ident or None)
        if cust:
            data["customer_name"] = cust["name"]
            data["points"] = int(cust.get("points", 0) or 0)
            data["redemptions"] = [{
                "prize_name": r["prize_name"], "points": r["points"],
                "time": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r.get("created_at") else None}
                for r in db.cc_redemption.find({"customer_id": cust["_id"]}).sort("created_at", -1).limit(100)]
    return ok(data)


# ---------- 积分商城：兑换奖品 ----------
@bp.post("/redeem")
@clerk
def redeem():
    d = _body()
    ident = (d.get("ident") or "").strip()
    prize_id = (d.get("prize_id") or "").strip()
    prize = C.CC_PRIZE_MAP.get(prize_id)
    if not prize:
        return fail("E-OP", "奖品不存在", 400)
    db = get_db()
    cust = find_customer(db, ident=ident or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户")

    def txn(s):
        c2 = db.customer.find_one({"_id": cust["_id"]}, session=s)  # 事务内重读积分，防并发透支
        pts = int(c2.get("points", 0) or 0)
        if pts < prize["points"]:  # E-1 积分不足
            return None, ("E-1", f"积分不足：需 {prize['points']}，当前 {pts}")
        db.customer.update_one({"_id": c2["_id"]}, {"$inc": {"points": -prize["points"]}}, session=s)
        db.cc_redemption.insert_one({
            "customer_id": c2["_id"], "prize_id": prize_id, "prize_name": prize["name"],
            "points": prize["points"], "created_at": now()}, session=s)
        write_audit(db, user_id=g.user["_id"], action="CC_REDEEM", object_type="customer",
                    object_id=c2["customer_no"], result=C.RESULT_SUCCESS,
                    detail={"prize": prize["name"], "points": prize["points"]}, session=s)
        return pts - prize["points"], None

    remain, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"prize": prize["name"], "cost": prize["points"], "points_remain": remain},
              f"兑换成功：{prize['name']}，扣 {prize['points']} 积分，剩余 {remain} 积分")


# ---------- UC-407 挂失/补卡/冻结/异常 ----------
@bp.post("/card")
@clerk
def card_op():
    d = _body()
    ident = (d.get("ident") or "").strip()
    card_type = (d.get("card_type") or "").strip()
    op = (d.get("op") or "").strip().upper()  # LOSS/REISSUE/FREEZE/UNFREEZE/EXCEPTION
    db = get_db()
    cc, cust, rerr = resolve_credit_card(db, ident, card_type=card_type)  # 凭身份+卡种定位唯一卡
    if rerr:
        return fail(rerr[0], rerr[1])
    card_no = cc["card_no"]
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
        new_status = C.CC_FROZEN if cur == C.CC_FROZEN else C.CC_ACTIVE
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
                object_id=card_no, result=C.RESULT_SUCCESS, detail={"card_no": card_no})
    return ok({"card_no": new_card}, msg)


# ---------- 信用卡查询 ----------
@bp.get("/query")
@clerk
def query():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()
    cards = []
    if ident:
        cc = db.credit_card.find_one({"card_no": ident})  # ident 可能是信用卡号
        if cc:
            cards = [cc]
        else:
            cust = find_customer(db, ident=ident or None)
            if cust:
                cards = list(db.credit_card.find({"customer_id": cust["_id"]}))
    if not cards:
        return fail("E-1", "未找到信用卡")
    result = []
    for cc in cards:
        cust = db.customer.find_one({"_id": cc["customer_id"]})
        v = cc_view(cc, cust)
        # 带出持卡人名下可用还款账户：人民币卡取储蓄账号，美元卡取美元外汇子户
        if _card_currency(cc) == "CNY":
            v["repay_accounts"] = [a["account_no"] for a in
                                   db.account.find({"customer_id": cc["customer_id"], "status": C.ACCOUNT_NORMAL})]
        else:
            v["repay_accounts"] = [f["fx_account_no"] for f in
                                   db.fx_account.find({"customer_id": cc["customer_id"], "currency": "USD",
                                                       "status": C.FX_NORMAL})]
        result.append(v)
    return ok({"cards": result})
