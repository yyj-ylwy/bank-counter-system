"""幂等种子数据：首次启动自动创建管理员、各角色演示业务员、系统参数和少量演示客户。

设计为幂等：已存在则跳过，可安全地在每次启动时调用。
"""
from datetime import timedelta

from bson.decimal128 import Decimal128
from werkzeug.security import generate_password_hash

import constants as C
from db import get_db
from common import (m, D, D6, now, write_txn, new_customer_no, new_account_no, new_debit_card_no)
from invest import m4, D4

# 演示账号（README 里公布）。生产环境请让管理员改密码。
DEMO_USERS = [
    ("admin", "周志远", C.ROLE_ADMIN, "admin123"),
    ("S001", "苏文静", C.ROLE_SAVINGS, "123456"),
    ("L001", "林建华", C.ROLE_LOAN, "123456"),
    ("F001", "冯雅琴", C.ROLE_FOREX, "123456"),
    ("CC001", "蔡明轩", C.ROLE_CREDIT, "123456"),
    ("I001", "尹晓彤", C.ROLE_INVEST, "123456"),
]

# 演示理财产品：(产品代码=行情代码, 名称, 类型, 计价币种, 风险等级)
INVEST_PRODUCTS = [
    ("000198", "天弘余额宝货币", "FUND", "CNY", 1),
    ("110020", "易方达沪深300ETF联接A", "FUND", "CNY", 3),
    ("000001", "华夏成长混合", "FUND", "CNY", 4),
    ("AAPL", "苹果公司(美股)", "STOCK", "USD", 5),
]
# 产品信息披露（投资范围 / 业绩比较基准），仅展示用
INVEST_PRODUCT_SCOPE = {
    "000198": "货币市场工具：短期国债、央行票据、同业存单、协议存款等",
    "110020": "沪深300指数成份股及备选成份股，紧密跟踪沪深300指数",
    "000001": "股票、债券及货币市场工具，以成长型股票为主",
    "AAPL": "美股：苹果公司(AAPL) 普通股",
}
INVEST_PRODUCT_BENCH = {
    "000198": "七日年化收益率",
    "110020": "沪深300指数收益率×95% + 银行活期存款利率×5%",
    "000001": "沪深300指数×70% + 中证全债指数×30%",
    "AAPL": "标普500指数收益率",
}
# 演示历史行情（本币价，key=距今天数）：让持仓的日/周/月/年价格变动有数据可算（离线可跑）
_FX_SEED = "7.2000"  # 美股 USD→CNY 演示汇率
INVEST_PRICE_SEED = {
    "000198": {365: "1.0000", 30: "1.0000", 7: "1.0000", 1: "1.0000", 0: "1.0000"},
    "110020": {365: "1.1000", 30: "1.3500", 7: "1.4200", 1: "1.4500", 0: "1.4800"},
    "000001": {365: "1.2000", 30: "1.4000", 7: "1.4500", 1: "1.4800", 0: "1.5000"},
    "AAPL": {365: "180.00", 30: "195.00", 7: "198.00", 1: "199.00", 0: "200.00"},
}

# 系统参数默认值（管理员可在系统管理里修改，无需改代码）
DEFAULT_PARAMS = [
    # 外汇牌价不再落种子兜底：一律按需从 Alpha Vantage 实时拉取，写入全系统共用的汇率记录、
    # 保质期 1 小时（见 forex.refresh_rates）。
    ("RATE", C.P_LOAN_RATE, "0.0435"),
    ("RATE", C.P_LOAN_OVERDUE_RATE, "0.0005"),
    ("LIMIT", C.P_WITHDRAW_DAILY_LIMIT, "50000"),
    ("RATE", C.P_TRANSFER_FEE_RATE, "0.001"),
    ("RATE", C.P_CC_MIN_REPAY_RATE, "0.10"),
    ("RATE", C.P_CC_CASH_FEE_RATE, "0.01"),
    ("LIMIT", C.P_CC_CASH_DAILY_LIMIT, "20000"),
    ("RATE", C.P_CC_MIN_INTEREST_RATE, "0.05"),         # 最低还款后剩余本金月利率 5%
    ("RATE", C.P_CC_LIMIT_DEPOSIT_RATIO, "0.30"),       # 提额上限 = 存款 × 30%
    ("RATE", C.P_FX_SPREAD, "0.003"),   # 外汇挂牌点差 0.3%
]

DEMO_CUSTOMERS = [
    ("张三", "身份证", "110101199001011234", "13800000001", "zhangsan@example.com", "10000.00"),
    ("李四", "身份证", "110101199203054321", "13800000002", "lisi@example.com", "5000.00"),
]


def run_seed():
    db = get_db()

    # --- 用户（逐个幂等：缺哪个补哪个，便于给已有库补上新增角色，如理财业务员 I001）---
    created = 0
    for emp, name, role, pw in DEMO_USERS:
        if db.user_account.count_documents({"employee_no": emp}) == 0:
            db.user_account.insert_one({
                "employee_no": emp,
                "name": name,
                "role": role,
                "password_hash": generate_password_hash(pw),
                "status": C.USER_ACTIVE,
                "created_at": now(),
            })
            created += 1
    if created:
        print(f"[seed] 已创建 {created} 个演示用户")

    admin = db.user_account.find_one({"role": C.ROLE_ADMIN})
    admin_id = admin["_id"] if admin else None

    _purge_legacy_cards(db)
    _purge_legacy_fx_params(db)
    _purge_cc_limit_max_param(db)

    # --- 系统参数（缺失才补，已有的不覆盖）---
    for ptype, key, val in DEFAULT_PARAMS:
        if db.system_param.count_documents({"param_key": key}) == 0:
            db.system_param.insert_one({
                "param_type": ptype,
                "param_key": key,
                "param_value": val,
                "changed_by": admin_id,
                "changed_at": now(),
            })

    # --- 演示客户 + 储蓄账户 ---
    if db.customer.count_documents({}) == 0:
        for name, id_type, id_no, phone, email, balance in DEMO_CUSTOMERS:
            cust = {
                "customer_no": new_customer_no(),
                "name": name,
                "id_type": id_type,
                "id_no": id_no,
                "email": email,
                "phone": phone,
                "status": C.CUSTOMER_NORMAL,
                "points": 0,   # 信用卡积分，开户初始 0
                "created_at": now(),
            }
            cid = db.customer.insert_one(cust).inserted_id
            acc = {
                "account_no": new_account_no(),
                "customer_id": cid,
                "card_no": new_debit_card_no(),
                "card_status": C.CARD_NORMAL,
                "currency": "CNY",
                "balance": m(balance),
                "status": C.ACCOUNT_NORMAL,
                "created_at": now(),
            }
            aid = db.account.insert_one(acc).inserted_id
            # 补一条开户流水，保持"账户余额 = 流水净额"不变式（对账用）
            write_txn(db, business_type=C.TXN_OPEN, amount=D(balance), user_id=admin_id,
                      customer_id=cid, account_id=aid)
        print(f"[seed] 已创建 {len(DEMO_CUSTOMERS)} 个演示客户及账户")

    # --- 演示理财产品 + 历史行情 + 客户风险等级/持仓 ---
    if db.invest_product.count_documents({}) == 0:
        for code, name, ptype, currency, risk in INVEST_PRODUCTS:
            is_mmf = code == "000198"  # 天弘余额宝=货币基金：支持 T+0 快速赎回、申赎免费
            db.invest_product.insert_one({
                "code": code, "name": name, "ptype": ptype, "market_symbol": code,
                "currency": currency, "risk_level": risk,
                "source": "ttjj" if ptype == "FUND" else "av",
                "is_money_fund": is_mmf,
                # 费率披露（仅展示、不参与计算）：货基费率较低，其余用默认
                "mgmt_fee": ("0.0015" if is_mmf else (C.INVEST_FUND_MGMT_FEE if ptype == "FUND" else "0")),
                "custody_fee": ("0.0005" if is_mmf else (C.INVEST_FUND_CUSTODY_FEE if ptype == "FUND" else "0")),
                "scope": INVEST_PRODUCT_SCOPE.get(code, ""),
                "benchmark": INVEST_PRODUCT_BENCH.get(code, ""), "prospectus_url": "",
                "status": C.INVEST_PRODUCT_ACTIVE, "created_at": now()})
        _seed_prices(db)
        _seed_holdings(db)
        print(f"[seed] 已创建 {len(INVEST_PRODUCTS)} 个演示理财产品及历史行情")


def _purge_legacy_cards(db):
    """清理不符合当前卡种目录的历史信用卡（信用卡模块重做前的普卡/金卡/白金卡等）。

    这些卡的卡种已不在 CARD_SPECS 中，没有币种/额度/返现/积分/费率定义，既无法用
    「身份+卡种」定位，也不能参与新的消费/还款规则，故予以删除（连同其历史账单）。
    幂等：清理后不再命中，可安全地随每次启动执行。
    注意：只删卡与账单，不删 business_transaction —— 取现入账等流水曾真实改变过储蓄账户
    余额，删掉会破坏「账户余额 = 流水净额」的对账不变式；历史流水按原样保留。
    """
    legacy = list(db.credit_card.find({"card_type": {"$nin": C.CARD_TYPES}},
                                      {"_id": 1, "card_no": 1, "card_type": 1}))
    if not legacy:
        return
    ids = [c["_id"] for c in legacy]
    bills = db.credit_card_bill.delete_many({"credit_card_id": {"$in": ids}}).deleted_count
    db.credit_card.delete_many({"_id": {"$in": ids}})
    detail = "、".join(f"{c.get('card_type')}/{c['card_no']}" for c in legacy)
    print(f"[seed] 已清理 {len(legacy)} 张历史卡种信用卡（及 {bills} 条历史账单）：{detail}")


def _purge_legacy_fx_params(db):
    """清理已废弃的「每币种买入价/卖出价」系统参数（FX_USD_BUY / FX_USD_SELL / ...）。

    全行买卖价一律由实时中间价按 FX_SPREAD 统一推算（卖出=中间价×(1+点差)、买入=中间价×(1-点差)），
    这组参数早已没有任何代码读取——留在库里会被管理员参数页列出来，让人误以为改了能生效。
    幂等：清理后不再命中。只删这组键，不动 FX_SPREAD 等仍在生效的参数。
    """
    q = {"param_key": {"$regex": r"^FX_[A-Z]{3}_(BUY|SELL)$"}}
    keys = [p["param_key"] for p in db.system_param.find(q, {"param_key": 1})]
    if not keys:
        return
    db.system_param.delete_many(q)
    print(f"[seed] 已清理 {len(keys)} 个废弃的外汇买卖价参数：{'、'.join(sorted(keys))}")


def _purge_cc_limit_max_param(db):
    """清理僵尸参数「信用卡最高授信额度」CC_CREDIT_LIMIT_MAX。

    全仓库无任何业务代码读取它，留在库里会被管理员参数页列出、误以为改了能生效。
    卡种初始额度改由 cc_card_limit（覆盖值）+ CARD_SPECS（默认）决定，提额上限用 CC_LIMIT_DEPOSIT_RATIO。
    幂等：清理后不再命中。
    """
    n = db.system_param.delete_many({"param_key": "CC_CREDIT_LIMIT_MAX"}).deleted_count
    if n:
        print(f"[seed] 已清理 {n} 个僵尸参数：CC_CREDIT_LIMIT_MAX（无代码读取）")


def _seed_prices(db):
    """写演示历史行情：外币产品按 _FX_SEED 折 CNY。(product_code,date) 唯一，幂等。"""
    for code, series in INVEST_PRICE_SEED.items():
        prod = db.invest_product.find_one({"code": code})
        is_usd = prod["currency"] != "CNY"
        for days, price in series.items():
            date = (now().date() - timedelta(days=days)).strftime("%Y%m%d")
            local = D4(price)
            price_cny = D4(local * D(_FX_SEED)) if is_usd else local
            fx = D6(_FX_SEED) if is_usd else D6(1)
            db.invest_price.update_one({"product_code": code, "date": date}, {"$set": {
                "product_code": code, "date": date, "price_local": m4(local),
                "currency": prod["currency"], "price_cny": m4(price_cny),
                "fx_rate": Decimal128(fx), "source": prod["source"],
                "as_of": "seed", "created_at": now()}}, upsert=True)


def _seed_holdings(db):
    """给演示客户设风险等级；张三持有一笔华夏成长混合（500份/成本700，现价1.50→浮盈50）。"""
    zhang = db.customer.find_one({"id_no": "110101199001011234"})
    li = db.customer.find_one({"id_no": "110101199203054321"})
    if zhang:
        db.customer.update_one({"_id": zhang["_id"]}, {"$set": {"invest_risk_level": 4, "invest_risk_at": now()}})
        db.invest_holding.update_one({"customer_id": zhang["_id"], "product_code": "000001"}, {"$set": {
            "customer_id": zhang["_id"], "product_code": "000001",
            "units": m4(500), "remaining_cost_cny": m(700), "realized_pnl_cny": m(0),
            "first_buy_date": now() - timedelta(days=60),  # 已持有60天→赎回免赎回费
            "created_at": now()}}, upsert=True)
    if li:
        db.customer.update_one({"_id": li["_id"]}, {"$set": {"invest_risk_level": 2, "invest_risk_at": now()}})
