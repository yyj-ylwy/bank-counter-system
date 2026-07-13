"""外汇业务子系统 UC-301 ~ UC-305（参与者：外汇业务员）。

约定：牌价的「买入价」= 银行向客户买入外币时用（客户卖出外币）；
      「卖出价」= 银行卖出外币给客户时用（客户买入外币）。
"""
import json
import time
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from flask import Blueprint, request, g
from pymongo.errors import DuplicateKeyError

import config
import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, D6, dec, m, now, norm_id, check_identity,
    find_customer, resolve_fx_account, check_account, write_txn, write_audit,
    get_param, get_param_dec, customer_view, txn_view, parse_date_range, new_fx_account_no,
)

bp = Blueprint("forex", __name__, url_prefix="/api/forex")
clerk = require_role(C.ROLE_FOREX)


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


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


# ========== 实时行情：按需拉取，一次取全部币种，缓存 30 分钟 ==========
# 设计：不再在 system_param 保存“牌价兜底”，牌价一律来自实时行情。
# 主源 open.er-api.com：一次 HTTP 请求即返回全部币种对 CNY 的汇率（免密钥、无逐币种限流），要么全有要么全无，
#   避免了 Alpha Vantage “一次要打 9 个请求、免费仅 25 次/天、突发只成功前几个”导致的“只剩美元”问题。
# 兜底 Alpha Vantage：仅当主源故障/缺某币种时，用其 Key 逐个补齐（通常不触发）。
# 缓存：任一功能(汇率查询/买卖/挂牌)需要牌价时，若已集齐全部币种且距上次拉取 < 30 分钟则直接用内存缓存、不调 API；
#   若上次未集齐(主源临时故障)，60 秒即可重试，直至集齐后再进入 30 分钟缓存。UC-302 查单币种也命中同一缓存。
_ER_API = "https://open.er-api.com/v6/latest/CNY"
_rate_cache = {}         # currency -> {"mid","buy","sell","as_of","ts"(monotonic)}；按币种独立缓存
_RATE_TTL = 1800         # 每币种缓存窗口：30 分钟


def parse_av(text):
    """解析 Alpha Vantage CURRENCY_EXCHANGE_RATE 响应。返回 (中间价 Decimal 或 None, 说明)。"""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, "行情响应解析失败"
    r = data.get("Realtime Currency Exchange Rate")
    if r and r.get("5. Exchange Rate"):
        try:
            return Decimal(str(r["5. Exchange Rate"])), r.get("6. Last Refreshed", "")
        except InvalidOperation:
            return None, "行情汇率字段异常"
    for k in ("Note", "Information", "Error Message"):  # 限流/错误响应
        if data.get(k):
            return None, str(data[k])[:120]
    return None, "行情响应格式无法识别"


def _fetch_mid_raw(currency):
    """无缓存地取某币种对 CNY 的实时中间价。返回 (Decimal 或 None, 来源说明/错误)。"""
    if not config.ALPHAVANTAGE_API_KEY:
        return None, "未配置 Alpha Vantage API Key（环境变量 ALPHAVANTAGE_API_KEY）"
    q = urllib.parse.urlencode({"function": "CURRENCY_EXCHANGE_RATE", "from_currency": currency,
                                "to_currency": "CNY", "apikey": config.ALPHAVANTAGE_API_KEY})
    try:
        req = urllib.request.Request("https://www.alphavantage.co/query?" + q,
                                     headers={"User-Agent": "Mozilla/5.0"})  # 带 UA，避免被判爬虫
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001  网络/超时等
        return None, f"AV连接失败：{type(e).__name__}:{e}"
    return parse_av(text)


def _fetch_all_mids_erapi():
    """主源：一次请求取全部支持币种对 CNY 的中间价。返回 ({currency: Decimal}, as_of, error 或 None)。"""
    try:
        with urllib.request.urlopen(_ER_API, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001  网络/超时/解析
        return {}, "", f"行情服务连接失败：{e}"
    if data.get("result") != "success":
        return {}, "", "行情服务返回异常"
    as_of = str(data.get("time_last_update_utc", ""))[:25]
    rates = data.get("rates") or {}
    out = {}
    for c in C.SUPPORTED_CURRENCIES:
        v = rates.get(c)  # rates[c] = 每 1 CNY 兑 c；取倒数得 1 单位 c 兑 CNY（即牌价中间价）
        if v:
            try:
                out[c] = Decimal(1) / Decimal(str(v))
            except (InvalidOperation, ZeroDivisionError):
                pass
    return out, as_of, None


def quote_from_mid(mid, spread):
    """由中间价与点差算挂牌买卖价：卖出价=中间价×(1+点差)，买入价=中间价×(1-点差)。"""
    sell = D6(mid * (Decimal(1) + spread))   # 银行卖出（客户买入）价，偏高
    buy = D6(mid * (Decimal(1) - spread))    # 银行买入（客户卖出）价，偏低
    return buy, sell


def refresh_rates(db, currency):
    """取单个币种的实时牌价：Alpha Vantage 实时（1 次调用，不触发批量限流）；失败则 er-api 兜底。
    按币种缓存 30 分钟。返回 (rate_dict{mid,buy,sell,as_of} 或 None, error 或 None, from_cache_bool)。"""
    currency = (currency or "").strip().upper()
    if currency not in C.PARAM_FX:
        return None, "不支持的币种", False
    now_m = time.monotonic()
    cached = _rate_cache.get(currency)
    if cached and (now_m - cached["ts"]) < _RATE_TTL:
        return cached, None, True
    spread = get_param_dec(db, C.P_FX_SPREAD, "0.003")
    mid, info = _fetch_mid_raw(currency)  # 主源：AV 实时（单币种）
    src = info
    av_err = None
    if mid is None:  # AV 失败/限流 → er-api 兜底（按日参考价）
        av_err = info
        er_mids, er_asof, er_err = _fetch_all_mids_erapi()
        if currency in er_mids:
            mid, src = er_mids[currency], f"参考价·{er_asof}"
        else:
            return None, (er_err or info or "行情暂不可用"), False
    buy, sell = quote_from_mid(mid, spread)
    rec = {"mid": D6(mid), "buy": buy, "sell": sell, "as_of": src, "ts": now_m, "av_err": av_err}
    _rate_cache[currency] = rec
    return rec, None, False


def read_rate(db, currency, direction):
    """取某币种按方向的适用牌价：BUY=客户买入外币(用卖出价) / SELL=客户卖出外币(用买入价)。
    返回 (rate, rate_type) 或 (None, None)。牌价来自 30 分钟实时缓存。"""
    if currency not in C.PARAM_FX:
        return None, None
    r, _err, _c = refresh_rates(db, currency)  # 单币种向 AV 实时拉取
    if not r:
        return None, None
    return (r["sell"], "SELL") if direction == "BUY" else (r["buy"], "BUY")


# ---------- UC-306 外汇实时汇率查询（合并原 UC-302 汇率确认与 UC-307 实时挂牌）----------
# 牌价一律来自实时行情缓存：查询即“确认牌价”，买卖(UC-303)按此换算，无需单独的确认/挂牌步骤。
@bp.get("/live-rate")
@clerk
def live_rate():
    db = get_db()
    cur = (request.args.get("currency") or "").strip().upper()
    if not cur:
        return fail("E-CUR", "请选择要查询的币种", 400)
    if cur not in C.PARAM_FX:
        return fail("E-CUR", "不支持的币种", 400)
    r, err, cached = refresh_rates(db, cur)  # 单币种：AV 实时（不触发批量限流）
    if not r:
        return fail("E-1", f"{cur} 实时牌价暂不可用：{err}")
    write_audit(db, user_id=g.user["_id"], action="FX_RATE_QUERY", object_type="system_param",
                object_id=cur, result=C.RESULT_SUCCESS, detail={"currency": cur, "from_cache": cached})
    return ok({"rates": [{"currency": cur, "mid": float(r["mid"]), "buy": float(r["buy"]),
                          "sell": float(r["sell"]), "as_of": r["as_of"]}],
               "from_cache": cached, "cache_ttl_min": _RATE_TTL // 60,
               "av_err": r.get("av_err"),  # 诊断：AV 失败原因（为空表示走了 AV 实时）
               "note": "买入价=银行买入(客户卖出)、卖出价=银行卖出(客户买入)；来自 Alpha Vantage 实时行情，按币种缓存 30 分钟。外汇买卖即按此牌价换算。"})


# ---------- UC-301 外汇子户开立 ----------
@bp.post("/open-subaccount")
@clerk
def open_subaccount():
    d = _body()
    db = get_db()
    cust = find_customer(db, ident=(d.get("ident") or "").strip() or None)
    if not cust:
        return fail("E-NOCUST", "未找到客户")
    currency = (d.get("currency") or "").strip().upper()
    if currency not in C.SUPPORTED_CURRENCIES:
        return fail("E-CUR", f"不支持的币种，仅支持 {'/'.join(C.SUPPORTED_CURRENCIES)}", 400)

    base = db.account.find_one({"customer_id": cust["_id"], "status": C.ACCOUNT_NORMAL})
    if not base:  # E-1 无有效储蓄账户
        return fail("E-1", "客户无有效储蓄账户，请先开立储蓄账户")
    def txn(s):
        if db.fx_account.count_documents({"customer_id": cust["_id"], "currency": currency,
                                          "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}, session=s):
            return None, ("E-2", f"客户已有 {currency} 外汇子户")
        fx = {
            "fx_account_no": new_fx_account_no(s), "customer_id": cust["_id"],
            "base_account_id": base["_id"], "currency": currency, "balance": m(0),
            "status": C.FX_NORMAL, "created_at": now(),
        }
        fx["_id"] = db.fx_account.insert_one(fx, session=s).inserted_id
        write_audit(db, user_id=g.user["_id"], action="FX_OPEN", object_type="fx_account",
                    object_id=fx["fx_account_no"], result=C.RESULT_SUCCESS, detail={"currency": currency}, session=s)
        return fx, None
    try:
        fx, err = run_in_transaction(txn)
    except DuplicateKeyError:
        return fail("E-2", f"客户已有 {currency} 外汇子户")
    if err:
        return fail(err[0], err[1])
    return ok({"fx_account": fx_view(fx, cust, base)}, "外汇子户开立成功")


# ---------- UC-303 外汇买卖确认 ----------
@bp.post("/trade")
@clerk
def trade():
    d = _body()
    ident = (d.get("ident") or "").strip()
    currency = (d.get("currency") or "").strip().upper()      # 币种：定位该客户对应外汇子户
    direction = (d.get("direction") or "").strip().upper()  # BUY=客户买入外币 / SELL=客户卖出外币
    foreign = D(d.get("amount") or 0)  # 外币金额
    if direction not in ("BUY", "SELL"):
        return fail("E-DIR", "买卖方向非法（BUY/SELL）", 400)
    if foreign <= 0:
        return fail("E-AMT", "外币金额必须大于零", 400)

    db = get_db()
    fx, ferr = resolve_fx_account(db, ident, currency)  # 凭任意身份标识+币种定位外汇子户
    if ferr:
        return fail(ferr[0], ferr[1])
    fx_no = fx["fx_account_no"]
    if fx["status"] != C.FX_NORMAL:
        return fail("E-FXSTAT", f"外汇子户状态为「{C.FX_STATUS_LABEL.get(fx['status'])}」，不可交易")

    r, rtype = read_rate(db, fx["currency"], direction)
    if r is None:  # E-1 实时牌价暂不可用（未配 Key / 限流 / 网络异常）
        return fail("E-1", "该币种实时牌价暂不可用（行情未配置/限流/网络异常），请稍后重试")
    cny = D(foreign * r)
    if cny <= 0:  # 小额外币按汇率折算后本币不足 1 分，拒绝，避免 0 元换外币/换 0 元本币
        return fail("E-AMT", "金额过小：按当前汇率折算的本币金额不足 0.01 元，请增加数量", 400)
    base = db.account.find_one({"_id": fx["base_account_id"]})
    if not base:
        return fail("E-NOACC", "关联储蓄账户不存在")

    def txn(s):
        fxa = db.fx_account.find_one({"_id": fx["_id"]}, session=s)  # 事务内重读子户，读改写一致
        if fxa["status"] != C.FX_NORMAL:
            return None, ("E-FXSTAT", f"外汇子户状态为「{C.FX_STATUS_LABEL.get(fxa['status'])}」，不可交易")
        base_acc = db.account.find_one({"_id": fxa["base_account_id"]}, session=s)
        if not base_acc:
            return None, ("E-NOACC", "关联储蓄账户不存在")
        if direction == "BUY":  # 客户买入外币：扣本币，加外币
            acc, err = check_account(db, base_acc["account_no"], need_amount=cny, session=s)  # E-2 余额不足
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
            acc, err = check_account(db, base_acc["account_no"], session=s)
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
        return {"cny_amount": float(cny), "foreign": float(foreign), "currency": fxa["currency"],
                "direction": direction, "rate": float(r), "rate_type": rtype,
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
    ctype = (d.get("change_type") or "").strip().upper()  # FREEZE/UNFREEZE/CLOSE
    ident = (d.get("ident") or "").strip()   # 任意身份标识（证件号/邮箱/手机号/外汇子户号）
    currency = (d.get("currency") or "").strip().upper()
    db = get_db()
    if ctype not in ("FREEZE", "UNFREEZE", "CLOSE"):
        return fail("E-OP", "变更类型非法（冻结/解冻/注销）", 400)
    fx, ferr = resolve_fx_account(db, ident, currency, d.get("fx_account_no"))  # 凭任意身份标识定位并核验归属
    if ferr:
        return fail(ferr[0], ferr[1])
    fx_no = fx["fx_account_no"]
    if fx["status"] == C.FX_CLOSED:  # 注销是终态，不能再变更
        return fail("E-CLOSED", "外汇账户已注销，不能再变更")

    def txn(s):
        f = db.fx_account.find_one({"_id": fx["_id"]}, session=s)  # 事务内重读，防并发（如注销校验后被 trade 充值）
        if f["status"] == C.FX_CLOSED:
            return None, ("E-CLOSED", "外汇账户已注销，不能再变更")
        updates = {}
        if ctype == "FREEZE":
            if f["status"] != C.FX_NORMAL:
                return None, ("E-STATE", "仅正常状态可冻结")
            updates["status"] = C.FX_FROZEN
        elif ctype == "UNFREEZE":
            if f["status"] != C.FX_FROZEN:
                return None, ("E-STATE", "仅冻结状态可解冻")
            updates["status"] = C.FX_NORMAL
        else:  # CLOSE 注销
            if dec(f["balance"]) != 0:  # 事务内重读余额，避免并发充值后仍被注销
                return None, ("E-2", f"账户仍有余额 {dec(f['balance'])}，请先结清后再注销")
            updates["status"] = C.FX_CLOSED
        db.fx_account.update_one({"_id": f["_id"]}, {"$set": updates}, session=s)
        write_audit(db, user_id=g.user["_id"], action=f"FX_{ctype}", object_type="fx_account",
                    object_id=fx_no, result=C.RESULT_SUCCESS,
                    detail={"reason": (d.get("reason") or "").strip()}, session=s)
        return updates, None

    updates, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    fx = db.fx_account.find_one({"_id": fx["_id"]})
    cust = db.customer.find_one({"_id": fx["customer_id"]})
    base = db.account.find_one({"_id": fx["base_account_id"]})
    return ok({"fx_account": fx_view(fx, cust, base)}, "外汇账户变更成功")


# ---------- UC-305 外汇余额与历史查询 ----------
@bp.get("/query")
@clerk
def query():
    db = get_db()
    ident = (request.args.get("ident") or "").strip()

    fx_accounts = []
    if ident:
        # ident 可能是外汇子户号
        fx = db.fx_account.find_one({"fx_account_no": ident})
        if fx:
            fx_accounts = [fx]
        else:
            cust = find_customer(db, ident=ident or None)
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
                object_id=ident or "-", result=C.RESULT_SUCCESS)
    return ok({"fx_accounts": views, "history": hist,
               "hint": None if hist else "该时段无外汇交易记录"})
