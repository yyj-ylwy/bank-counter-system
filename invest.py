"""投资理财业务子系统（参与者：理财业务员 INVEST_CLERK）。

功能：产品目录维护(管理员)、每日行情(手动刷新 + 查询懒加载)、实时行情查询、
      简化风险测评、基金/股票申购与赎回、持仓查询(累计盈亏 + 日/周/月/年价格变动)。
金额口径：份额/净值 4 位小数(m4/D4)；成本/盈亏/金额 2 位(m/D)；外币产品统一折 CNY 结算。
数据源(均免密钥/复用现有)：基金=天天基金(CNY)；股票=Alpha Vantage 美股(USD→CNY 用外汇中间价)。
手续费(A股规则)：基金申购费外扣、赎回费按持有天数递减；股票佣金(双向,最低5元)+印花税(卖出)+过户费。
申赎均生成交割单快照(fee/fee_detail/成交额/实收实付/T+1确认到账)。
ponytail: 教学模拟系统，未含分红再投/定投；区间盈亏为"价格变动"口径(不含期间现金流)。

【答辩讲解】在原有五个模块之外从零新增的完整业务：产品维护、每日行情、风险测评、基金股票申赎、持仓盈亏、交易记录。
亮点：行情自动接入(含备用数据源)、A股真实费率、合规风控(测评有效期/风险不匹配确认书)、T+1 到账状态流转。
从数据模型、外部接口、金额精度到事务原子性，是我对整套架构理解的综合体现。

本文件关键函数(UC-601~610)：upsert_product/products/product_lookup(产品维护+回填)、
quote/refresh_prices + 行情拉取 _fetch_fund_price/_fetch_fund_backup/_fetch_us_stock_price/price_for_trade(行情，含备用源)、
assess(风险测评)、buy/buy_fees(申购含费)、sell/sell_fees(赎回含费/货基快赎)、
confirm_settlements(T+1确认到账)、transactions(交易记录)、holdings(持仓盈亏)。
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime as _dt, timedelta as _td
from decimal import Decimal, ROUND_HALF_UP

from bson.decimal128 import Decimal128
from flask import Blueprint, request, g

import config
import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, D6, dec, m, now, as_int,
    resolve_account_no, check_account, write_txn, write_audit, parse_date_range,
)
from forex import refresh_rates  # 复用外汇的 CNY 中间价做货币换算

bp = Blueprint("invest", __name__, url_prefix="/api/invest")
clerk = require_role(C.ROLE_INVEST)

_Q4 = Decimal("0.0001")


def D4(x):
    """份额/净值 4 位小数。"""
    return Decimal(str(x)).quantize(_Q4, rounding=ROUND_HALF_UP)


def m4(x):
    return Decimal128(D4(x))


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}


def _truthy(v):
    """把前端传来的勾选/开关值统一判真（"1"/"true"/"yes"/"on"/True 都算真）。"""
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


def _today():
    return now().strftime("%Y%m%d")


# ==================== 行情数据源（纯 stdlib，失败返回 (None, err)）====================
def _http_get(url, timeout=8, encoding="utf-8"):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="ignore")


def _fetch_fund_backup(code):
    """备用源：天天基金 pingzhongdata，取最近一条确认单位净值(覆盖比实时估值广、免密钥)。
    返回 (price Decimal, as_of) 或 (None, err)。"""
    try:
        text = _http_get(f"http://fund.eastmoney.com/pingzhongdata/{code}.js", timeout=10)
        m = re.search(r"Data_netWorthTrend\s*=\s*(\[[^\]]*\])", text)  # 单位净值走势数组(对象内无]，可安全截取)
        if not m:
            return None, "备用源无净值走势"
        arr = json.loads(m.group(1))
        if not arr or arr[-1].get("y") is None:
            return None, "备用源净值为空"
        price = Decimal(str(arr[-1]["y"]))  # 最后一条=最近确认净值
        if price <= 0:
            return None, "备用源净值非法"
        try:
            as_of = _dt.utcfromtimestamp(arr[-1]["x"] / 1000 + 8 * 3600).strftime("%Y-%m-%d") + "(确认净值·备用源)"
        except Exception:  # noqa: BLE001
            as_of = "确认净值(备用源)"
        return price, as_of
    except Exception as e:  # noqa: BLE001
        return None, f"备用源获取失败：{e}"


def _fetch_fund_price(code):
    """基金净值：主源=天天基金实时估值(JSONP)；主源无估值/异常时回退备用源(pingzhongdata 确认净值)。
    返回 (price Decimal, as_of) 或 (None, err)。"""
    try:
        text = _http_get(f"http://fundgz.1234567.com.cn/js/{code}.js")
        mobj = re.search(r"jsonpgz\((\{.*?\})\)", text)
        if mobj:
            data = json.loads(mobj.group(1))
            raw = data.get("gsz") or data.get("dwjz")  # gsz=实时估算净值，dwjz=上一交易日确认净值
            if raw and Decimal(str(raw)) > 0:
                return Decimal(str(raw)), (data.get("gztime") or data.get("jzrq") or "")
    except Exception:  # noqa: BLE001 主源异常不直接失败，转备用源
        pass
    # 主源返回空 jsonpgz(); 或异常 → 回退备用源(确认净值)
    price, info = _fetch_fund_backup(code)
    if price is not None:
        return price, info
    return None, "该基金主源无实时估值、备用源也无确认净值（数据源不覆盖此基金，常见于货币/QDII/已清盘基金），请改用有净值的场外开放式基金"


def _fetch_us_stock_price(symbol):
    """Alpha Vantage 美股报价(USD)。返回 (price_usd Decimal, as_of) 或 (None, err)。"""
    key = getattr(config, "ALPHAVANTAGE_API_KEY", "") or ""
    if not key:
        return None, "未配置行情密钥(ALPHAVANTAGE_API_KEY)"
    try:
        q = urllib.parse.urlencode({"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key})
        data = json.loads(_http_get(f"https://www.alphavantage.co/query?{q}", timeout=10))
        quote = data.get("Global Quote") or {}
        raw = quote.get("05. price")
        if not raw:
            return None, "美股行情暂不可用(可能触发限流)"
        price = Decimal(str(raw))
        if price <= 0:
            return None, "美股价格非法"
        return price, (quote.get("07. latest trading day") or "")
    except Exception as e:  # noqa: BLE001
        return None, f"美股行情获取失败：{e}"


def _fetch_product_cny(db, product):
    """取产品最新价并折 CNY。返回 (dict{price_local,currency,price_cny,fx_rate,as_of,source}, None) 或 (None, err)。"""
    ptype, sym = product.get("ptype"), product["market_symbol"]
    if ptype == "FUND":
        price, info = _fetch_fund_price(sym)
        if price is None:
            return None, info
        return {"price_local": D4(price), "currency": "CNY", "price_cny": D4(price),
                "fx_rate": D6(1), "as_of": info, "source": "ttjj"}, None
    if ptype == "STOCK":
        price_usd, info = _fetch_us_stock_price(sym)
        if price_usd is None:
            return None, info
        rec, ferr, _ = refresh_rates(db, product.get("currency", "USD"))  # 事务外取汇率
        if not rec:
            return None, f"货币换算失败：{ferr}"
        mid = dec(rec["mid"])
        return {"price_local": D4(price_usd), "currency": product.get("currency", "USD"),
                "price_cny": D4(price_usd * mid), "fx_rate": D6(mid), "as_of": info, "source": "av"}, None
    return None, "未知产品类型"


def refresh_price(db, product, force=False):
    """拉取产品当日价并存 invest_price(幂等：当日已有且非 force 直接返回)。返回 (price_doc, err)。"""
    date = _today()
    existing = db.invest_price.find_one({"product_code": product["code"], "date": date})
    if existing and not force:
        return existing, None
    info, err = _fetch_product_cny(db, product)
    if err:
        return None, err
    doc = {"product_code": product["code"], "date": date,
           "price_local": m4(info["price_local"]), "currency": info["currency"],
           "price_cny": m4(info["price_cny"]), "fx_rate": Decimal128(info["fx_rate"]),
           "source": info["source"], "as_of": info["as_of"], "created_at": now()}
    db.invest_price.update_one({"product_code": product["code"], "date": date}, {"$set": doc}, upsert=True)
    return doc, None


def latest_price_doc(db, product, allow_fetch=True):
    """取最新价：优先当日；缺则(懒加载)尝试拉当日；再缺回退最近一条已存价。
    返回 (price_doc 或 None, is_today, err)。"""
    date = _today()
    doc = db.invest_price.find_one({"product_code": product["code"], "date": date})
    if doc:
        return doc, True, None
    if allow_fetch:
        doc, _err = refresh_price(db, product)
        if doc:
            return doc, True, None
    latest = db.invest_price.find_one({"product_code": product["code"]}, sort=[("date", -1)])
    return latest, False, (None if latest else "行情暂不可用")


def price_for_trade(db, product):
    """成交价：确保足够新鲜。返回 (price_cny Decimal, price_doc, err(code,msg)或None)。"""
    doc, _is_today, err = latest_price_doc(db, product, allow_fetch=True)
    if not doc:
        return None, None, ("E-PRICE", err or "行情暂不可用，暂无法成交")
    days_old = (now().date() - _dt.strptime(doc["date"], "%Y%m%d").date()).days
    if days_old > C.INVEST_PRICE_STALE_MAX_DAYS:  # 不能用陈旧价成交
        return None, None, ("E-PRICE", f"最新行情为 {doc['date']}，已超 {C.INVEST_PRICE_STALE_MAX_DAYS} 天，拒绝按陈价成交")
    return dec(doc["price_cny"]), doc, None


def _price_ago_cny(db, code, days_back):
    """取 (今天-days_back) 当天或之前最近一条价的 CNY 价。返回 Decimal 或 None。"""
    target = (now().date() - _td(days=days_back)).strftime("%Y%m%d")
    doc = db.invest_price.find_one({"product_code": code, "date": {"$lte": target}}, sort=[("date", -1)])
    return dec(doc["price_cny"]) if doc else None


def product_view(db, p, with_price=True):
    v = {"code": p["code"], "name": p["name"], "ptype": p["ptype"],
         "ptype_label": C.INVEST_PTYPE_LABEL.get(p["ptype"], p["ptype"]),
         "currency": p.get("currency", "CNY"),
         "risk_level": p.get("risk_level"), "risk_label": C.RISK_LEVEL_LABEL.get(p.get("risk_level"), "-"),
         "source_label": C.INVEST_SOURCE_LABEL.get(p.get("source"), p.get("source")),
         "status_label": C.INVEST_PRODUCT_STATUS_LABEL.get(p.get("status"), p.get("status")),
         "is_money_fund": bool(p.get("is_money_fund")),  # 货基→支持快速赎回
         "mgmt_fee": p.get("mgmt_fee"), "custody_fee": p.get("custody_fee"),  # 费率披露（不参与计算）
         "scope": p.get("scope"), "benchmark": p.get("benchmark"), "prospectus_url": p.get("prospectus_url")}
    if with_price:  # 列表只读已存价，不逐个联网(避免慢/限流)
        doc, is_today, _ = latest_price_doc(db, p, allow_fetch=False)
        if doc:
            v["price_cny"] = float(dec(doc["price_cny"]))
            v["price_date"] = doc["date"]
            v["stale"] = not is_today
    return v


# ==================== 手续费/税费（纯函数，一处计算，申购/赎回共用）====================
def buy_fees(ptype, amount, is_money_fund=False):
    """买入费用。基金(外扣法)：amount=申购金额(含费)，净申购=amount/(1+费率)，份额基数=净额，扣款=amount。
    股票(A股费率,外加)：amount=成交额，佣金+过户费外加，份额基数=amount，扣款=amount+费。
    货币基金：申购免费。返回 (units_base, fee_total, fee_detail{中文:金额}, total_debit)。"""
    if ptype == "FUND":
        if is_money_fund:  # 货币基金申购不收费
            return amount, D(0), {"申购费": 0.0}, amount
        rate = dec(C.INVEST_FUND_BUY_FEE)
        net = D(amount / (Decimal(1) + rate))
        fee = amount - net
        return net, fee, {"申购费": float(fee)}, amount
    comm = max(D(dec(C.INVEST_STOCK_COMMISSION) * amount), D(C.INVEST_STOCK_COMMISSION_MIN))
    transfer = D(dec(C.INVEST_STOCK_TRANSFER) * amount)
    fee = comm + transfer
    return amount, fee, {"佣金": float(comm), "过户费": float(transfer)}, amount + fee


def sell_fees(ptype, gross, hold_days=None, is_money_fund=False):
    """卖出/赎回费用。gross=成交额(份额×净值)。返回 (fee_total, fee_detail, proceeds=gross-fee)。
    基金赎回费按持有天数递减(<7天1.5%/7~30天0.5%/≥30天0)；货币基金免赎回费；股票=佣金(双向)+印花税(卖出)+过户费。"""
    if ptype == "FUND":
        if is_money_fund:  # 货币基金赎回不收费
            return D(0), {"赎回费": 0.0, "货币基金": "免赎回费"}, gross
        rate = D(0)
        for days, r in C.INVEST_FUND_REDEEM_TIERS:
            if hold_days is not None and hold_days < days:
                rate = dec(r)
                break
        fee = D(gross * rate)
        return fee, {"赎回费": float(fee), "赎回费率": float(rate), "持有天数": hold_days}, gross - fee
    comm = max(D(dec(C.INVEST_STOCK_COMMISSION) * gross), D(C.INVEST_STOCK_COMMISSION_MIN))
    stamp = D(dec(C.INVEST_STOCK_STAMP) * gross)
    transfer = D(dec(C.INVEST_STOCK_TRANSFER) * gross)
    fee = comm + stamp + transfer
    return fee, {"佣金": float(comm), "印花税": float(stamp), "过户费": float(transfer)}, gross - fee


# ==================== UC-601 产品目录（理财业务员维护 / 查看）====================
@bp.post("/product")
@clerk
def upsert_product():
    d = _body()
    code = (d.get("code") or "").strip()
    name = (d.get("name") or "").strip()
    ptype = (d.get("ptype") or "").strip().upper()
    symbol = (d.get("market_symbol") or "").strip()
    if not code or not name or not symbol:
        return fail("E-REQ", "产品代码/名称/行情代码为必填", 400)
    if ptype not in C.INVEST_PTYPE_LABEL:
        return fail("E-REQ", "产品类型非法（FUND 基金 / STOCK 股票）", 400)
    risk = as_int(d.get("risk_level"), 3)
    if risk not in C.RISK_LEVEL_LABEL:
        return fail("E-REQ", "风险等级应为 1-5", 400)
    status = as_int(d.get("status"), C.INVEST_PRODUCT_ACTIVE)
    currency = "CNY" if ptype == "FUND" else (d.get("currency") or "USD").strip().upper()
    is_mmf = ptype == "FUND" and _truthy(d.get("is_money_fund"))  # 货币基金：支持 T+0 快速赎回(限额)
    # 管理费/托管费：仅信息披露（每日计提、已含在净值里），不参与任何金额计算；缺省取默认费率
    mgmt_fee = str(d.get("mgmt_fee") or (C.INVEST_FUND_MGMT_FEE if ptype == "FUND" else "0")).strip()
    custody_fee = str(d.get("custody_fee") or (C.INVEST_FUND_CUSTODY_FEE if ptype == "FUND" else "0")).strip()
    db = get_db()
    db.invest_product.update_one({"code": code}, {"$set": {
        "code": code, "name": name, "ptype": ptype, "market_symbol": symbol,
        "currency": currency, "risk_level": risk,
        "source": "ttjj" if ptype == "FUND" else "av",
        "is_money_fund": is_mmf, "mgmt_fee": mgmt_fee, "custody_fee": custody_fee,  # 货基标志 + 费率披露
        "scope": (d.get("scope") or "").strip(),          # 投资范围（披露）
        "benchmark": (d.get("benchmark") or "").strip(),  # 业绩比较基准（披露）
        "prospectus_url": (d.get("prospectus_url") or "").strip(),  # 招募说明书链接（披露）
        "status": status, "updated_at": now()}}, upsert=True)
    write_audit(db, user_id=g.user["_id"], action="INVEST_PRODUCT", object_type="invest_product",
                object_id=code, result=C.RESULT_SUCCESS, detail={"name": name, "ptype": ptype})
    return ok({"code": code, "name": name}, "产品已保存")


@bp.get("/products")
@clerk
def products():
    db = get_db()
    ps = list(db.invest_product.find({"status": C.INVEST_PRODUCT_ACTIVE}).sort("code", 1))
    return ok({"products": [product_view(db, p) for p in ps], "hint": None if ps else "暂无在售产品"})


@bp.get("/product-lookup")
@clerk
def product_lookup():
    """产品维护回填：返回全部产品(含停售)的可编辑字段，供 UC-608 输代码点「查询并回填」带出后再改。"""
    db = get_db()
    ps = list(db.invest_product.find().sort("code", 1))
    return ok({"products": [{
        "code": p["code"], "name": p.get("name"), "ptype": p.get("ptype"),
        "market_symbol": p.get("market_symbol"), "currency": p.get("currency"),
        "risk_level": p.get("risk_level"), "is_money_fund": bool(p.get("is_money_fund")),
        "mgmt_fee": p.get("mgmt_fee"), "custody_fee": p.get("custody_fee"),
        "scope": p.get("scope"), "benchmark": p.get("benchmark"),
        "prospectus_url": p.get("prospectus_url"), "status": p.get("status"),
    } for p in ps]})


# ==================== UC-602 行情：实时查询 / 每日刷新 ====================
@bp.get("/quote")
@clerk
def quote():
    db = get_db()
    p = db.invest_product.find_one({"code": (request.args.get("product_code") or "").strip()})
    if not p:
        return fail("E-NOPROD", "未找到该产品")
    doc, is_today, err = latest_price_doc(db, p, allow_fetch=True)
    if not doc:
        return fail("E-PRICE", err or "行情暂不可用")
    return ok({"code": p["code"], "name": p["name"], "ptype_label": C.INVEST_PTYPE_LABEL.get(p["ptype"]),
               "currency": p.get("currency", "CNY"),
               "price_local": float(dec(doc["price_local"])), "price_cny": float(dec(doc["price_cny"])),
               "fx_rate": float(dec(doc.get("fx_rate", 1))), "date": doc["date"], "as_of": doc.get("as_of"),
               "stale": not is_today, "source_label": C.INVEST_SOURCE_LABEL.get(doc.get("source")),
               "is_money_fund": bool(p.get("is_money_fund")),
               "mgmt_fee": p.get("mgmt_fee"), "custody_fee": p.get("custody_fee"),  # 费率披露
               "scope": p.get("scope"), "benchmark": p.get("benchmark"), "prospectus_url": p.get("prospectus_url")})


@bp.post("/refresh-prices")
@clerk
def refresh_prices():
    """每日行情更新：拉取全部在售产品当日价（幂等，(code,date) 唯一防重复）。"""
    db = get_db()
    updated, failed = [], []
    for p in db.invest_product.find({"status": C.INVEST_PRODUCT_ACTIVE}):
        doc, err = refresh_price(db, p, force=True)
        if doc:
            updated.append({"code": p["code"], "name": p["name"],
                            "price_cny": float(dec(doc["price_cny"])), "date": doc["date"]})
        else:
            failed.append({"code": p["code"], "reason": err})
    write_audit(db, user_id=g.user["_id"], action="INVEST_REFRESH", object_type="invest_product",
                object_id="-", result=C.RESULT_SUCCESS, detail={"updated": len(updated), "failed": len(failed)})
    return ok({"updated": updated, "failed": failed,
               "hint": f"已更新 {len(updated)} 个产品当日行情" + (f"，{len(failed)} 个失败" if failed else "")})


# ==================== UC-603 风险测评（简化）====================
@bp.post("/assess")
@clerk
def assess():
    """5 题各 1-5 分，均值取整为风险承受等级(1-5)，写入客户档案。买入时校验产品风险≤客户等级。"""
    d = _body()
    db = get_db()
    _acc, cust, rerr = resolve_account_no(db, (d.get("ident") or "").strip())
    if rerr:
        return fail(rerr[0], rerr[1])
    try:
        answers = [as_int(d.get(f"q{i}")) for i in range(1, 6)]
    except ValueError:
        return fail("E-REQ", "测评答案需为数字", 400)
    if any(a < 1 or a > 5 for a in answers):
        return fail("E-REQ", "每题应为 1-5 分", 400)
    level = max(1, min(5, int((sum(answers) + len(answers) // 2) // len(answers))))  # 四舍五入到整数等级
    db.customer.update_one({"_id": cust["_id"]}, {"$set": {"invest_risk_level": level, "invest_risk_at": now()}})
    write_audit(db, user_id=g.user["_id"], action="INVEST_ASSESS", object_type="customer",
                object_id=cust["customer_no"], result=C.RESULT_SUCCESS, detail={"level": level})
    return ok({"customer_no": cust["customer_no"], "name": cust["name"],
               "risk_level": level, "risk_label": C.RISK_LEVEL_LABEL[level]},
              f"风险测评完成：{C.RISK_LEVEL_LABEL[level]}风险承受等级")


# ==================== UC-604 申购（买入）====================
@bp.post("/buy")
@clerk
def buy():
    d = _body()
    code = (d.get("product_code") or "").strip()
    amount = D(d.get("amount") or 0)
    if amount <= 0 or amount > C.TXN_AMOUNT_MAX:
        return fail("E-AMT", f"申购金额须大于 0 且不超过 {C.TXN_AMOUNT_MAX:,}", 400)
    db = get_db()
    account_no, cust, rerr = resolve_account_no(db, (d.get("ident") or "").strip())
    if rerr:
        return fail(rerr[0], rerr[1])
    p = db.invest_product.find_one({"code": code})
    if not p or p.get("status") != C.INVEST_PRODUCT_ACTIVE:
        return fail("E-NOPROD", "产品不存在或已停售")
    cust_risk = cust.get("invest_risk_level")  # 风险适配
    if cust_risk is None:
        return fail("E-RISK", "该客户尚未做风险测评，请先测评")
    assessed_at = cust.get("invest_risk_at")  # 测评有效期：超 12 个月失效，须重做（赎回不受限）
    if assessed_at and (now() - assessed_at).days > C.INVEST_ASSESS_VALID_DAYS:
        return fail("E-RISK", f"该客户风险测评已过期（超 {C.INVEST_ASSESS_VALID_DAYS} 天），请先重新测评再申购")
    prod_risk = p.get("risk_level", 1)
    risk_mismatch = prod_risk > cust_risk  # 产品风险高于客户承受等级
    if risk_mismatch:  # 风险不匹配：C1 硬禁止 / 超 2 级硬禁止 / 超 1 级须签《风险不匹配确认书》
        if cust_risk == 1:
            return fail("E-RISK", "客户为最低风险承受等级(C1)，按适当性规定禁止购买高于其承受能力的产品")
        if prod_risk - cust_risk >= 2:
            return fail("E-RISK", f"产品风险「{C.RISK_LEVEL_LABEL.get(prod_risk)}」比客户承受等级「{C.RISK_LEVEL_LABEL.get(cust_risk)}」高 2 级及以上，不可申购")
        if not _truthy(d.get("mismatch_confirmed")):  # 超 1 级：客户坚持须先签确认书
            return fail("E-RISK", f"产品风险「{C.RISK_LEVEL_LABEL.get(prod_risk)}」高于客户承受等级「{C.RISK_LEVEL_LABEL.get(cust_risk)}」；如客户坚持购买，请签署《风险不匹配确认书》(勾选后重新提交)")
    price_cny, pdoc, perr = price_for_trade(db, p)  # 事务外取新鲜价
    if perr:
        return fail(perr[0], perr[1])
    units_base, fee, fee_detail, total_debit = buy_fees(p["ptype"], amount, p.get("is_money_fund"))  # 计费(基金外扣/股票外加/货基免费)
    units = D4(units_base / price_cny)
    if units <= 0:
        return fail("E-AMT", "申购金额过小，折算份额不足", 400)
    confirm_date = (now() + _td(days=C.INVEST_CONFIRM_DAYS)).strftime("%Y-%m-%d")  # T+N 确认日

    def txn(s):
        acc, err = check_account(db, account_no, need_amount=total_debit, session=s)  # 扣含费总额
        if err:
            return None, err
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) - total_debit)}}, session=s)
        h = db.invest_holding.find_one({"customer_id": cust["_id"], "product_code": code}, session=s)
        if h:  # 加权平均成本：份额、含费成本各自累加；首次买入日保留(供赎回费持有期档位)
            db.invest_holding.update_one({"_id": h["_id"]}, {"$set": {
                "units": m4(dec(h["units"]) + units),
                "remaining_cost_cny": m(dec(h["remaining_cost_cny"]) + total_debit)}}, session=s)
        else:
            db.invest_holding.insert_one({"customer_id": cust["_id"], "product_code": code,
                "units": m4(units), "remaining_cost_cny": m(total_debit), "realized_pnl_cny": m(0),
                "first_buy_date": now(), "created_at": now()}, session=s)
        t = write_txn(db, business_type=C.TXN_INVEST_BUY, amount=total_debit, user_id=g.user["_id"],
                      customer_id=cust["_id"], account_id=acc["_id"], session=s)
        db.business_transaction.update_one({"_id": t["_id"]}, {"$set": {  # 交易/交割单快照
            "product_code": code, "units": m4(units), "price_cny": m4(price_cny), "price_date": pdoc["date"],
            "fee": m(fee), "fee_detail": fee_detail, "amount_gross": m(amount),
            "confirm_date": confirm_date, "settle_status": C.INVEST_SETTLE_STATUS["BUY_PENDING"],
            "risk_mismatch": risk_mismatch}}, session=s)  # 是否风险不匹配签约购买(留痕)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_INVEST_BUY, object_type="invest_holding",
                    object_id=code, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount), "fee": str(fee), "units": str(units), "price_cny": str(price_cny),
                            "account_no": account_no, "risk_mismatch": risk_mismatch}, session=s)
        return t, None

    t, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"product": p["name"], "ptype": C.INVEST_PTYPE_LABEL.get(p["ptype"]),
               "amount": float(amount), "fee": float(fee), "fee_detail": fee_detail,
               "total_debit": float(total_debit), "units": float(units), "price_cny": float(price_cny),
               "price_date": pdoc["date"], "confirm_date": confirm_date,
               "settle_status": C.INVEST_SETTLE_STATUS["BUY_PENDING"], "risk_mismatch": risk_mismatch,
               "confirm_no": t["txn_no"], "txn_no": t["txn_no"]}, "申购成功")  # confirm_no=成交确认单号


# ==================== UC-605 赎回（卖出）====================
@bp.post("/sell")
@clerk
def sell():
    d = _body()
    code = (d.get("product_code") or "").strip()
    sell_all = bool(d.get("all"))
    sell_units = D4(d.get("units") or 0)
    if not sell_all and sell_units <= 0:
        return fail("E-AMT", "赎回份额须大于 0（或传 all=true 全部赎回）", 400)
    db = get_db()
    account_no, cust, rerr = resolve_account_no(db, (d.get("ident") or "").strip())
    if rerr:
        return fail(rerr[0], rerr[1])
    p = db.invest_product.find_one({"code": code})
    if not p:
        return fail("E-NOPROD", "未找到该产品")
    h0 = db.invest_holding.find_one({"customer_id": cust["_id"], "product_code": code})
    if not h0 or dec(h0["units"]) <= 0:
        return fail("E-NOHOLD", "该客户没有此产品的持仓")
    price_cny, pdoc, perr = price_for_trade(db, p)  # 事务外取新鲜价
    if perr:
        return fail(perr[0], perr[1])
    fast = _truthy(d.get("fast"))  # 货币基金快速赎回(T+0)
    if fast and not p.get("is_money_fund"):
        return fail("E-REQ", "仅货币基金支持快速赎回(T+0)，普通产品请取消勾选「快速赎回」", 400)

    def txn(s):
        h = db.invest_holding.find_one({"customer_id": cust["_id"], "product_code": code}, session=s)  # 事务内重读
        held = dec(h["units"])
        remaining_cost = dec(h["remaining_cost_cny"])
        realized = dec(h.get("realized_pnl_cny", 0))
        su = held if sell_all else sell_units
        if su > held:  # 防超卖
            return None, ("E-UNITS", f"赎回份额超过持仓：持有 {held}，赎回 {su}")
        gross = D(su * price_cny)  # 成交额(费前)
        if fast and gross > D(C.INVEST_MMF_FAST_REDEEM_MAX):  # 货基快赎单日限额 1 万
            return None, ("E-LIMIT", f"货币基金快速赎回单日限额 {C.INVEST_MMF_FAST_REDEEM_MAX} 元，本次成交额 {gross}，请改用普通赎回")
        fbd = h.get("first_buy_date")
        hold_days = (now() - fbd).days if fbd else None  # 持有天数→赎回费档位
        fee, fee_detail, proceeds = sell_fees(p["ptype"], gross, hold_days, p.get("is_money_fund"))  # 计费，实收=费后
        if su >= held:  # 全部赎回：残余成本/份额归零，避免尾差
            removed_cost, new_units, new_cost = remaining_cost, D4(0), D(0)
        else:  # 部分赎回：按加权平均成本比例结转
            removed_cost = D(remaining_cost * su / held)
            new_units, new_cost = held - su, remaining_cost - removed_cost
        realized_delta = proceeds - removed_cost  # 已实现盈亏(含费口径：实收−结转成本)
        acc, err = check_account(db, account_no, session=s)  # 状态(销户/冻结/挂失)
        if err:
            return None, err
        db.account.update_one({"_id": acc["_id"]},
                              {"$set": {"balance": m(dec(acc["balance"]) + proceeds)}}, session=s)
        db.invest_holding.update_one({"_id": h["_id"]}, {"$set": {
            "units": m4(new_units), "remaining_cost_cny": m(new_cost),
            "realized_pnl_cny": m(realized + realized_delta)}}, session=s)
        t = write_txn(db, business_type=C.TXN_INVEST_SELL, amount=proceeds, user_id=g.user["_id"],
                      customer_id=cust["_id"], account_id=acc["_id"], session=s)
        if fast:  # 货基快速赎回：当日到账(T+0)
            settle_date, settle_status = now().strftime("%Y-%m-%d"), C.INVEST_SETTLE_STATUS["SELL_FAST_DONE"]
        else:  # 普通赎回：T+N 到账，先记待到账
            settle_date = (now() + _td(days=C.INVEST_SETTLE_DAYS)).strftime("%Y-%m-%d")
            settle_status = C.INVEST_SETTLE_STATUS["SELL_PENDING"]
        db.business_transaction.update_one({"_id": t["_id"]}, {"$set": {  # 交易/交割单快照
            "product_code": code, "units": m4(su), "price_cny": m4(price_cny), "price_date": pdoc["date"],
            "amount_gross": m(gross), "fee": m(fee), "fee_detail": fee_detail,
            "removed_cost": m(removed_cost), "realized": m(realized_delta),
            "settle_date": settle_date, "settle_status": settle_status}}, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_INVEST_SELL, object_type="invest_holding",
                    object_id=code, result=C.RESULT_SUCCESS,
                    detail={"units": str(su), "gross": str(gross), "fee": str(fee), "proceeds": str(proceeds),
                            "realized": str(realized_delta), "account_no": account_no, "fast": fast}, session=s)
        return {"units": float(su), "amount_gross": float(gross), "fee": float(fee), "fee_detail": fee_detail,
                "proceeds": float(proceeds), "realized": float(realized_delta), "fast": fast,
                "settle_date": settle_date, "settle_status": settle_status,
                "confirm_no": t["txn_no"], "txn_no": t["txn_no"]}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"product": p["name"], "ptype": C.INVEST_PTYPE_LABEL.get(p["ptype"]),
               "price_cny": float(price_cny), "price_date": pdoc["date"], **res}, "赎回成功")


# ==================== UC-609 份额确认 / 资金到账（T+1 状态流转，批量）====================
@bp.post("/confirm-settlements")
@clerk
def confirm_settlements():
    """把到了确认日/到账日的申赎单据从"待确认/待到账"翻成"已确认/已到账"。
    force=true：演示用，忽略 T+1 日期直接确认全部待处理（当天下的单确认日是明天，否则当天看不到流转）。
    ponytail: 教学简化——份额/资金在下单时已即时入账，这里只做交割单状态位的 T+1 流转(可演示、可讲)。"""
    force = _truthy(_body().get("force"))  # 演示开关：不管确认日/到账日是否到期
    db = get_db()
    today = now().strftime("%Y-%m-%d")  # 与快照里 confirm_date/settle_date 同为 YYYY-MM-DD，可直接字符串比较
    buy_q = {"business_type": C.TXN_INVEST_BUY, "settle_status": C.INVEST_SETTLE_STATUS["BUY_PENDING"]}
    sell_q = {"business_type": C.TXN_INVEST_SELL, "settle_status": C.INVEST_SETTLE_STATUS["SELL_PENDING"]}
    if not force:  # 真实模式：只处理确认日/到账日已到（<=今天）的
        buy_q["confirm_date"] = {"$lte": today}
        sell_q["settle_date"] = {"$lte": today}
    confirmed = db.business_transaction.update_many(buy_q,   # 申购 → 份额已确认
        {"$set": {"settle_status": C.INVEST_SETTLE_STATUS["BUY_DONE"]}}).modified_count
    settled = db.business_transaction.update_many(sell_q,    # 赎回 → 资金已到账
        {"$set": {"settle_status": C.INVEST_SETTLE_STATUS["SELL_DONE"]}}).modified_count
    write_audit(db, user_id=g.user["_id"], action="INVEST_SETTLE", object_type="business_transaction",
                object_id="-", result=C.RESULT_SUCCESS, detail={"confirmed": confirmed, "settled": settled, "force": force})
    tail = "（演示：忽略T+1日期，确认全部待处理）" if force else f"（截至 {today} 到期的；未到确认日/到账日的不处理）"
    return ok({"confirmed": confirmed, "settled": settled},
              f"已确认份额 {confirmed} 笔、资金到账 {settled} 笔{tail}")


# ==================== UC-610 理财交易记录 / 交割单查询 ====================
@bp.get("/transactions")
@clerk
def transactions():
    """列出客户的理财申赎记录(交割单)：产品/类型/成交价/份额/金额/费用/受理状态/确认到账日/已实现盈亏。
    与 UC-607 持仓(当前快照)互补——这里看的是每一笔申赎的历史与状态(有没有到账/确认)。"""
    db = get_db()
    _acc, cust, rerr = resolve_account_no(db, (request.args.get("ident") or "").strip())
    if rerr:
        return fail(rerr[0], rerr[1])
    q = {"customer_id": cust["_id"], "business_type": {"$in": [C.TXN_INVEST_BUY, C.TXN_INVEST_SELL]}}
    rng = parse_date_range(request.args.get("start"), request.args.get("end"))  # 按成交时间做日期筛选
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["txn_time"] = rng
    rows, prod_cache, pending = [], {}, 0
    for t in db.business_transaction.find(q).sort("txn_time", -1).limit(200):  # 最近 200 笔，倒序
        code = t.get("product_code")
        if code not in prod_cache:
            prod_cache[code] = db.invest_product.find_one({"code": code})
        p = prod_cache[code]
        is_buy = t["business_type"] == C.TXN_INVEST_BUY
        status = t.get("settle_status")
        if status in (C.INVEST_SETTLE_STATUS["BUY_PENDING"], C.INVEST_SETTLE_STATUS["SELL_PENDING"]):
            pending += 1  # 待确认/待到账 计一笔
        rows.append({
            "txn_no": t["txn_no"],
            "txn_time": t["txn_time"].strftime("%Y-%m-%d %H:%M:%S") if t.get("txn_time") else None,
            "type": "申购" if is_buy else "赎回",
            "product": (p["name"] if p else str(code)) + f"（{code}）",
            "units": float(dec(t.get("units", 0))),
            "price_cny": float(dec(t.get("price_cny", 0))),
            "amount": float(dec(t.get("amount", 0))),       # 申购=实付合计、赎回=实收到账
            "fee": float(dec(t.get("fee", 0))),
            "settle_status": status,
            "expect_date": t.get("confirm_date") if is_buy else t.get("settle_date"),  # 确认日/到账日
            "realized": float(dec(t["realized"])) if (not is_buy and t.get("realized") is not None) else None,
        })
    return ok({"customer": {"customer_no": cust["customer_no"], "name": cust["name"]},
               "transactions": rows, "pending": pending,
               "hint": None if rows else "该客户暂无理财交易记录"})


# ==================== UC-606 持仓查询（累计盈亏 + 日/周/月/年价格变动）====================
@bp.get("/holdings")
@clerk
def holdings():
    db = get_db()
    account_no, cust, rerr = resolve_account_no(db, (request.args.get("ident") or "").strip())
    if rerr:
        return fail(rerr[0], rerr[1])
    rows, tot_mv, tot_cost, tot_real = [], D(0), D(0), D(0)
    for h in db.invest_holding.find({"customer_id": cust["_id"]}):
        units = dec(h["units"])
        realized = dec(h.get("realized_pnl_cny", 0))
        if units <= 0 and realized == 0:
            continue
        p = db.invest_product.find_one({"code": h["product_code"]})
        doc, is_today, _ = latest_price_doc(db, p, allow_fetch=False) if p else (None, False, None)
        price_cny = dec(doc["price_cny"]) if doc else D(0)
        remaining_cost = dec(h["remaining_cost_cny"])
        market_value = D(units * price_cny)
        unrealized = market_value - remaining_cost
        row = {"code": h["product_code"], "name": p["name"] if p else h["product_code"],
               "units": float(units), "price_cny": float(price_cny),
               "price_date": doc["date"] if doc else None, "stale": (not is_today) if doc else True,
               "cost": float(remaining_cost), "market_value": float(market_value),
               "unrealized": float(unrealized),
               "unrealized_pct": (float(unrealized / remaining_cost) if remaining_cost > 0 else None),
               "realized": float(realized), "cumulative": float(realized + unrealized)}
        for k, dback in (("day", 1), ("week", 7), ("month", 30), ("year", 365)):  # 价格变动口径
            pago = _price_ago_cny(db, h["product_code"], dback)
            if pago is not None and pago > 0 and doc and units > 0:
                row[k + "_change"] = float(D(units * (price_cny - pago)))
                row[k + "_pct"] = float(price_cny / pago - 1)
            else:
                row[k + "_change"] = row[k + "_pct"] = None
        rows.append(row)
        tot_mv += market_value
        tot_cost += remaining_cost
        tot_real += realized
    lvl = cust.get("invest_risk_level")
    return ok({"customer": {"customer_no": cust["customer_no"], "name": cust["name"],
                            "risk_level": lvl, "risk_label": C.RISK_LEVEL_LABEL.get(lvl, "未测评")},
               "holdings": rows, "hint": None if rows else "该客户暂无持仓",
               "summary": {"total_market_value": float(tot_mv), "total_cost": float(tot_cost),
                           "total_unrealized": float(tot_mv - tot_cost), "total_realized": float(tot_real),
                           "total_pnl": float(tot_real + (tot_mv - tot_cost))}})


# ---------- 纯逻辑自检：加权平均成本结转与盈亏（ponytail: 资金路径留一处可跑校验）----------
if __name__ == "__main__":
    # 买 1000 @2.00 → 500 份，成本 1000；再买 1000 @2.50 → +400 份，共 900 份，成本 2000
    units1, cost = D4(D(1000) / D("2.00")), D(1000)          # 500.0000, 1000
    units2 = D4(D(1000) / D("2.50"))                         # 400.0000
    units, cost = units1 + units2, cost + D(1000)            # 900.0000, 2000
    assert units == D4(900) and cost == D(2000)
    # 现价 3.00，卖 300 份：收益=900，结转成本=2000*300/900=666.67，已实现=900-666.67=233.33
    price = D("3.00"); su = D4(300)
    proceeds = D(su * price)                                 # 900.00
    removed = D(cost * su / units)                           # 666.67
    realized = proceeds - removed                            # 233.33
    assert proceeds == D(900) and removed == D("666.67") and realized == D("233.33")
    units, cost = units - su, cost - removed                 # 600, 1333.33
    # 未实现 = 市值 - 剩余成本 = 600*3 - 1333.33 = 466.67；累计 = 已实现 + 未实现 = 700.00
    unrealized = D(units * price) - cost
    assert unrealized == D("466.67") and (realized + unrealized) == D("700.00")
    # 全部赎回归零
    su2 = units
    removed2, units, cost = cost, D4(0), D(0)
    assert units == D4(0) and cost == D(0)

    # 手续费：基金外扣法（净申购=金额/(1+费率)，扣款=金额）
    net, fee, det, debit = buy_fees("FUND", D(1000))
    assert net == D("998.50") and fee == D("1.50") and debit == D(1000)
    # 股票申购（外加）：佣金<最低时取 5 元 + 过户费
    _, feeS, _, debitS = buy_fees("STOCK", D(1440))
    assert feeS == D("5.03") and debitS == D("1445.03")               # 5(佣金保底)+0.03(过户费)
    assert buy_fees("STOCK", D(100))[1] == D(5)                       # 佣金最低 5 元封底
    # 基金赎回费按持有天数递减
    assert sell_fees("FUND", D(750), 0)[2] == D("738.75")            # <7天 1.5% → 实收 738.75
    assert sell_fees("FUND", D(750), 10)[2] == D("746.25")           # 7~30天 0.5%
    assert sell_fees("FUND", D(750), 40)[2] == D(750)               # ≥30天 免赎回费
    # 股票卖出：佣金+印花税(单边)+过户费
    assert sell_fees("STOCK", D(1440), None)[2] == D("1434.25")      # 1440-5-0.72-0.03
    # 货币基金：申购、赎回均免费（不论持有天数）
    assert buy_fees("FUND", D(1000), True) == (D(1000), D(0), {"申购费": 0.0}, D(1000))
    assert sell_fees("FUND", D(750), 0, True)[0] == D(0) and sell_fees("FUND", D(750), 0, True)[2] == D(750)
    print("invest self-check OK: 加权平均成本/已实现/未实现/清仓归零/手续费(申赎股票)/货基免费 全部通过")
