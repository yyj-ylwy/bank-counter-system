"""外汇业务子系统 UC-301 ~ UC-305（参与者：外汇业务员）。

约定：牌价的「买入价」= 银行向客户买入外币时用（客户卖出外币）；
      「卖出价」= 银行卖出外币给客户时用（客户买入外币）。
"""
from flask import Blueprint, request, g

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, D6, dec, m, now,
    find_customer, check_account, write_txn, write_audit,
    get_param, customer_view, txn_view, parse_date_range, new_fx_account_no,
)

bp = Blueprint("forex", __name__, url_prefix="/api/forex")
clerk = require_role(C.ROLE_FOREX)


def _body():
    return request.get_json(force=True, silent=True) or {}


def fx_view(fx, cust=None, base=None):
    return {
        "id": str(fx["_id"]),
        "fx_account_no": fx["fx_account_no"],
        "currency": fx["currency"],
        "balance": float(dec(fx["balance"])),
        "status": fx["status"],
        "status_label": C.FX_STATUS_LABEL.get(fx["status"], "未知"),
        "customer_name": cust["name"] if cust else None,
        "customer_no": cust["customer_no"] if cust else None,
        "base_account_no": base["account_no"] if base else None,
    }


def read_rate(db, currency, direction):
    """direction: BUY=客户买入外币(用卖出价) / SELL=客户卖出外币(用买入价)。
    返回 (rate, rate_type) 或 (None, None)。"""
    if currency not in C.PARAM_FX:
        return None, None
    buy_key, sell_key = C.PARAM_FX[currency]
    if direction == "BUY":
        v = get_param(db, sell_key)
        return (D6(v), "SELL") if v is not None else (None, None)
    else:
        v = get_param(db, buy_key)
        return (D6(v), "BUY") if v is not None else (None, None)


# ---------- UC-301 外汇子户开立 ----------
@bp.post("/open-subaccount")
@clerk
def open_subaccount():
    d = _body()
    db = get_db()
    cust = find_customer(db, customer_no=(d.get("customer_no") or "").strip() or None,
                         id_no=(d.get("id_no") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户")
    currency = (d.get("currency") or "").strip().upper()
    if currency not in C.SUPPORTED_CURRENCIES:
        return fail("E-CUR", f"不支持的币种，仅支持 {'/'.join(C.SUPPORTED_CURRENCIES)}", 400)

    base = db.account.find_one({"customer_id": cust["_id"], "status": C.ACCOUNT_NORMAL})
    if not base:  # E-1 无有效储蓄账户
        return fail("E-1", "客户无有效储蓄账户，请先开立储蓄账户")
    if db.fx_account.count_documents({"customer_id": cust["_id"], "currency": currency,
                                      "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}):
        return fail("E-2", f"客户已有 {currency} 外汇子户")  # E-2 已存在

    fx = {
        "fx_account_no": new_fx_account_no(),
        "customer_id": cust["_id"],
        "base_account_id": base["_id"],
        "currency": currency,
        "balance": m(0),
        "status": C.FX_NORMAL,
        "created_at": now(),
    }
    fx["_id"] = db.fx_account.insert_one(fx).inserted_id
    write_audit(db, user_id=g.user["_id"], action="FX_OPEN", object_type="fx_account",
                object_id=fx["fx_account_no"], result=C.RESULT_SUCCESS, detail={"currency": currency})
    return ok({"fx_account": fx_view(fx, cust, base)}, "外汇子户开立成功")


# ---------- UC-302 汇率查询与确认 ----------
@bp.get("/rate")
@clerk
def rate():
    currency = (request.args.get("currency") or "").strip().upper()
    direction = (request.args.get("direction") or "BUY").strip().upper()  # 客户买入/卖出
    db = get_db()
    if currency not in C.PARAM_FX:
        return fail("E-CUR", "不支持的币种", 400)
    buy_key, sell_key = C.PARAM_FX[currency]
    buy = get_param(db, buy_key)
    sell = get_param(db, sell_key)
    if buy is None or sell is None:  # E-1 未维护牌价
        return fail("E-1", "该币种未维护牌价，请联系管理员维护参数")
    r, rtype = read_rate(db, currency, direction)
    p = db.system_param.find_one({"param_key": sell_key})
    write_audit(db, user_id=g.user["_id"], action="FX_RATE_CONFIRM", object_type="system_param",
                object_id=currency, result=C.RESULT_SUCCESS, detail={"direction": direction})
    return ok({
        "currency": currency,
        "buy_rate": float(D6(buy)), "sell_rate": float(D6(sell)),
        "apply_rate": float(r), "apply_rate_type": rtype,
        "direction": direction,
        "effective_at": p["changed_at"].strftime("%Y-%m-%d %H:%M:%S") if p and p.get("changed_at") else None,
        "note": "客户买入外币用卖出价，客户卖出外币用买入价",
    })


# ---------- UC-303 外汇买卖确认 ----------
@bp.post("/trade")
@clerk
def trade():
    d = _body()
    fx_no = (d.get("fx_account_no") or "").strip()
    direction = (d.get("direction") or "").strip().upper()  # BUY=客户买入外币 / SELL=客户卖出外币
    foreign = D(d.get("amount") or 0)  # 外币金额
    if direction not in ("BUY", "SELL"):
        return fail("E-DIR", "买卖方向非法（BUY/SELL）", 400)
    if foreign <= 0:
        return fail("E-AMT", "外币金额必须大于零", 400)

    db = get_db()
    fx = db.fx_account.find_one({"fx_account_no": fx_no})
    if not fx:
        return fail("E-NOFX", "未找到外汇子户")
    if fx["status"] != C.FX_NORMAL:
        return fail("E-FXSTAT", f"外汇子户状态为「{C.FX_STATUS_LABEL.get(fx['status'])}」，不可交易")

    r, rtype = read_rate(db, fx["currency"], direction)
    if r is None:  # E-1 汇率缺失/过期
        return fail("E-1", "该币种牌价缺失，请先维护或刷新")
    cny = D(foreign * r)
    base = db.account.find_one({"_id": fx["base_account_id"]})
    if not base:
        return fail("E-NOACC", "关联储蓄账户不存在")

    def txn(s):
        fxa = db.fx_account.find_one({"_id": fx["_id"]}, session=s)  # 事务内重读子户，读改写一致
        if fxa["status"] != C.FX_NORMAL:
            return None, ("E-FXSTAT", f"外汇子户状态为「{C.FX_STATUS_LABEL.get(fxa['status'])}」，不可交易")
        if direction == "BUY":  # 客户买入外币：扣本币，加外币
            acc, err = check_account(db, base["account_no"], need_amount=cny, session=s)  # E-2 余额不足
            if err:
                return None, err
            db.account.update_one({"_id": acc["_id"]},
                                  {"$set": {"balance": m(dec(acc["balance"]) - cny)}}, session=s)
            db.fx_account.update_one({"_id": fxa["_id"]},
                                     {"$set": {"balance": m(dec(fxa["balance"]) + foreign)}}, session=s)
            btype = C.TXN_FX_BUY
        else:  # 客户卖出外币：扣外币，加本币
            if dec(fxa["balance"]) < foreign:  # E-2 外币余额不足
                return None, ("E-2", f"外币余额不足，当前 {dec(fxa['balance'])} {fxa['currency']}")
            acc, err = check_account(db, base["account_no"], session=s)
            if err:
                return None, err
            db.fx_account.update_one({"_id": fxa["_id"]},
                                     {"$set": {"balance": m(dec(fxa["balance"]) - foreign)}}, session=s)
            db.account.update_one({"_id": acc["_id"]},
                                  {"$set": {"balance": m(dec(acc["balance"]) + cny)}}, session=s)
            btype = C.TXN_FX_SELL

        t = write_txn(db, business_type=btype, amount=foreign, user_id=g.user["_id"],
                      customer_id=fxa["customer_id"], account_id=acc["_id"],
                      related_id=fxa["_id"], fx_rate=r, fx_rate_type=rtype, session=s)
        # 补充币种与本币金额，方便外汇历史展示
        db.business_transaction.update_one(
            {"_id": t["_id"]}, {"$set": {"currency": fxa["currency"], "cny_amount": m(cny)}}, session=s)
        write_audit(db, user_id=g.user["_id"], action=btype, object_type="fx_account",
                    object_id=fx_no, result=C.RESULT_SUCCESS,
                    detail={"foreign": str(foreign), "currency": fxa["currency"],
                            "rate": str(r), "cny": str(cny)}, session=s)
        return {"cny_amount": float(cny), "rate": float(r), "rate_type": rtype,
                "txn": txn_view(t)}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok(res, f"外汇{'买入' if direction == 'BUY' else '卖出'}成功")


# ---------- UC-304 外汇账户变更 ----------
@bp.post("/change")
@clerk
def change():
    d = _body()
    fx_no = (d.get("fx_account_no") or "").strip()
    ctype = (d.get("change_type") or "").strip().upper()  # FREEZE/UNFREEZE/CLOSE/REBIND
    db = get_db()
    fx = db.fx_account.find_one({"fx_account_no": fx_no})
    if not fx:  # E-1
        return fail("E-1", "外汇子户不存在，请重新输入")
    if fx["status"] == C.FX_CLOSED:  # 关闭是终态，不能再冻结/解冻/重新关闭
        return fail("E-CLOSED", "外汇子户已关闭，不能再变更")

    updates = {}
    if ctype == "FREEZE":
        if fx["status"] != C.FX_NORMAL:
            return fail("E-STATE", "仅正常状态可冻结")
        updates["status"] = C.FX_FROZEN
    elif ctype == "UNFREEZE":
        if fx["status"] != C.FX_FROZEN:
            return fail("E-STATE", "仅冻结状态可解冻")
        updates["status"] = C.FX_NORMAL
    elif ctype == "CLOSE":
        if dec(fx["balance"]) != 0:  # E-2 有余额不可关闭
            return fail("E-2", f"子户仍有余额 {dec(fx['balance'])}，请先结清后再关闭")
        updates["status"] = C.FX_CLOSED
    elif ctype == "REBIND":
        new_no = (d.get("new_base_account_no") or "").strip()
        new_acc = db.account.find_one({"account_no": new_no})
        if not new_acc or new_acc["customer_id"] != fx["customer_id"] or new_acc["status"] != C.ACCOUNT_NORMAL:
            return fail("E-3", "新关联账户不存在、状态异常或不属于该客户")  # E-3
        updates["base_account_id"] = new_acc["_id"]
    else:
        return fail("E-OP", "变更类型非法（FREEZE/UNFREEZE/CLOSE/REBIND）", 400)

    db.fx_account.update_one({"_id": fx["_id"]}, {"$set": updates})
    write_audit(db, user_id=g.user["_id"], action=f"FX_{ctype}", object_type="fx_account",
                object_id=fx_no, result=C.RESULT_SUCCESS,
                detail={"reason": (d.get("reason") or "").strip()})
    fx.update(updates)
    cust = db.customer.find_one({"_id": fx["customer_id"]})
    base = db.account.find_one({"_id": fx["base_account_id"]})
    return ok({"fx_account": fx_view(fx, cust, base)}, "外汇账户变更成功")


# ---------- UC-305 外汇余额与历史查询 ----------
@bp.get("/query")
@clerk
def query():
    db = get_db()
    fx_no = (request.args.get("fx_account_no") or "").strip()
    customer_no = (request.args.get("customer_no") or "").strip()
    id_no = (request.args.get("id_no") or "").strip()

    fx_accounts = []
    if fx_no:
        fx = db.fx_account.find_one({"fx_account_no": fx_no})
        if fx:
            fx_accounts = [fx]
    else:
        cust = find_customer(db, customer_no=customer_no or None, id_no=id_no or None)
        if cust:
            fx_accounts = list(db.fx_account.find({"customer_id": cust["_id"]}))
    if not fx_accounts:  # E-1
        return fail("E-1", "未找到外汇账户")

    views = []
    for fx in fx_accounts:
        cust = db.customer.find_one({"_id": fx["customer_id"]})
        base = db.account.find_one({"_id": fx["base_account_id"]})
        views.append(fx_view(fx, cust, base))

    # 外汇交易历史
    q = {"related_id": {"$in": [fx["_id"] for fx in fx_accounts]},
         "business_type": {"$in": [C.TXN_FX_BUY, C.TXN_FX_SELL]}}
    rng = parse_date_range(request.args.get("start"), request.args.get("end"))
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["txn_time"] = rng
    txns = list(db.business_transaction.find(q).sort("txn_time", -1).limit(500))
    hist = []
    for t in txns:
        v = txn_view(t)
        v["currency"] = t.get("currency")
        v["cny_amount"] = float(dec(t.get("cny_amount"))) if t.get("cny_amount") is not None else None
        hist.append(v)

    write_audit(db, user_id=g.user["_id"], action="FX_QUERY", object_type="fx_account",
                object_id=fx_no or customer_no or id_no, result=C.RESULT_SUCCESS)
    return ok({"fx_accounts": views, "history": hist,
               "hint": None if hist else "该时段无外汇交易记录"})
