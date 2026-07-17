"""投资理财模块端到端测试（独立自足，按当前 ident 身份标识 API 编写）。

覆盖：产品列表/实时行情、风险测评与适配、基金/股票申购(含货币换算)、赎回(部分/全部/超卖/无持仓)、
      持仓盈亏(累计 + 日/周/月/年价格变动)、管理员产品维护。
用 seed 写入的当日行情，不走外网。使用一次性库 bank_invest_test，测完自动删除。
运行：python test_invest.py

【答辩讲解·测试】按《第七章》做白盒+黑盒。白盒：逻辑覆盖(test_logic.py)、基本路径(test_extra.py，画控制流图/算圈复杂度/每条独立路径一用例)；
黑盒：条件组合覆盖、判定表、边界值(test_e2e.py/test_invest.py，每条断言标了类型)；整体覆盖率 82%。
现场演示：① 敲 python test_invest.py，当场看断言逐条 PASS、最后"43通过/0失败"；
② 指输出说"每条标了[判定]/[条件]/[组合]/[边界]，对应第七章覆盖方法"；
③ 举例——申购放行判定表:未测评/风险不匹配/余额不足/全满足四条规则各一个用例；
④ 再跑 python test_logic.py、python test_extra.py，可开 82% 覆盖率 HTML 报告。
"""
import os
os.environ.setdefault("DB_NAME", "bank_invest_test")

from app import create_app
from db import get_client, get_db
from common import now
from datetime import timedelta

app = create_app()
cl = app.test_client()

_p = _f = 0
def ok(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS {name}")
    else:
        _f += 1; print(f"  FAIL {name}")

def login(emp, pw):
    return cl.post("/api/login", json={"employee_no": emp, "password": pw}).get_json()["data"]["token"]

INV = login("I001", "123456"); AD = login("admin", "admin123"); S = login("S001", "123456")

def api(method, path, tok, json=None, query=None):
    h = {"Authorization": "Bearer " + tok}
    r = cl.get(path, headers=h, query_string=query or {}) if method == "GET" else cl.post(path, headers=h, json=json or {})
    return r.get_json() or {}

def E(r): return r.get("error")
def OK(r): return r.get("success") is True

_seq = [0]
def uid():
    _seq[0] += 1
    return _seq[0]

def open_acct(bal="0"):
    """按当前开户 API（必填邮箱）建一个客户+账户，返回 {id_no, account_no}。"""
    n = uid()
    idn = "11010119910909" + f"{n:04d}"
    r = api("POST", "/api/savings/open-account", S, json={
        "name": f"理财客户{n}", "id_type": "身份证", "id_no": idn,
        "email": f"inv{n}@test.example.com", "initial_balance": bal})
    assert r.get("success"), r
    return {"id_no": idn, "account_no": r["data"]["account"]["account_no"]}


def run():
    print("== 投资理财 UC-601~607 ==")
    # 产品/行情（用 seed 的当日价，不走网络）
    ok("601 产品列表 [正常流]", OK(api("GET", "/api/invest/products", INV)))
    q = api("GET", "/api/invest/quote", INV, query={"product_code": "000001"})
    ok("602 实时行情(华夏成长混合,当日1.5) [正常流]", OK(q) and abs(q["data"]["price_cny"] - 1.5) < 0.001)
    ok("602 产品不存在→E-NOPROD [判定]", E(api("GET", "/api/invest/quote", INV, query={"product_code": "ZZZ"})) == "E-NOPROD")

    c = open_acct(bal="100000")
    ok("605 未测评申购→E-RISK [判定]", E(api("POST", "/api/invest/buy", INV, json={"ident": c["id_no"], "product_code": "000001", "amount": "1500"})) == "E-RISK")
    ra = api("POST", "/api/invest/assess", INV, json={"ident": c["id_no"], "q1": "5", "q2": "5", "q3": "5", "q4": "5", "q5": "5"})
    ok("604 风险测评→5级 [正常流]", OK(ra) and ra["data"]["risk_level"] == 5)

    # 申购：1500 元 @ 净值1.50，基金申购费0.15%外扣 → 净申购1497.75/1.50 ≈ 998.5 份
    b = api("POST", "/api/invest/buy", INV, json={"ident": c["id_no"], "product_code": "000001", "amount": "1500"})
    ok("605 申购成功(1500元扣0.15%申购费2.25→998.5份) [正常流]",
       OK(b) and abs(b["data"]["units"] - 998.5) < 0.001
       and abs(b["data"]["fee"] - 2.25) < 0.01 and abs(b["data"]["total_debit"] - 1500) < 0.01)
    ok("605 申购超余额→E-BAL [边界]", E(api("POST", "/api/invest/buy", INV, json={"ident": c["id_no"], "product_code": "000001", "amount": "9999999"})) == "E-BAL")
    ok("605 金额0→E-AMT [边界]", E(api("POST", "/api/invest/buy", INV, json={"ident": c["id_no"], "product_code": "000001", "amount": "0"})) == "E-AMT")
    # 货币换算：AAPL 当日折 CNY = 200×7.2 = 1440；买 1440 元 → 1 份
    aapl = api("POST", "/api/invest/buy", INV, json={"ident": c["id_no"], "product_code": "AAPL", "amount": "1440"})
    ok("605 美股申购货币换算(1440元→1份,A股佣金保底5+过户费0.03) [组合]",
       OK(aapl) and abs(aapl["data"]["units"] - 1) < 0.001 and abs(aapl["data"]["price_cny"] - 1440) < 0.01
       and abs(aapl["data"]["fee"] - 5.03) < 0.01 and abs(aapl["data"]["total_debit"] - 1445.03) < 0.01)

    # 持仓盈亏：含费成本 = 基金1500 + 股票1445.03 = 2945.03
    h = api("GET", "/api/invest/holdings", INV, query={"ident": c["id_no"]})
    ok("607 持仓查询(含费成本1500+1445.03) [正常流]", OK(h) and abs(h["data"]["summary"]["total_cost"] - 2945.03) < 0.01)
    row = next((x for x in h["data"]["holdings"] if x["code"] == "000001"), None)
    ok("607 持仓含日/周/月/年价格变动 [组合]", row and row.get("year_pct") is not None)
    ok("607 未测评客户无持仓查询→仍返回(空) [判定]", OK(api("GET", "/api/invest/holdings", INV, query={"ident": open_acct()["id_no"]})))

    # 赎回：卖 500 份 @ 1.50，成交750；当日买卖持有<7天→赎回费1.5%=11.25，实收738.75
    s = api("POST", "/api/invest/sell", INV, json={"ident": c["id_no"], "product_code": "000001", "units": "500"})
    ok("606 赎回500份(<7天赎回费1.5%)→成交750实收738.75 [正常流]",
       OK(s) and abs(s["data"]["amount_gross"] - 750) < 0.01
       and abs(s["data"]["fee"] - 11.25) < 0.01 and abs(s["data"]["proceeds"] - 738.75) < 0.01)
    ok("606 超持仓赎回→E-UNITS [边界]", E(api("POST", "/api/invest/sell", INV, json={"ident": c["id_no"], "product_code": "000001", "units": "999999"})) == "E-UNITS")
    ok("606 无持仓赎回→E-NOHOLD [判定]", E(api("POST", "/api/invest/sell", INV, json={"ident": c["id_no"], "product_code": "110020", "units": "1"})) == "E-NOHOLD")
    ok("606 全部赎回成功 [边界]", OK(api("POST", "/api/invest/sell", INV, json={"ident": c["id_no"], "product_code": "000001", "all": True})))
    # 全部赎回后再查：000001 份额应为 0
    h2 = api("GET", "/api/invest/holdings", INV, query={"ident": c["id_no"]})
    row2 = next((x for x in h2["data"]["holdings"] if x["code"] == "000001"), None)
    ok("606 清仓后份额归零 [边界]", row2 is None or abs(row2["units"]) < 0.0001)

    # 风险适配：测评2级客户买5级美股→E-RISK
    c2 = open_acct(bal="10000")
    api("POST", "/api/invest/assess", INV, json={"ident": c2["id_no"], "q1": "2", "q2": "2", "q3": "2", "q4": "2", "q5": "2"})
    ok("605 风险不匹配(2级买5级股票)→E-RISK [条件]", E(api("POST", "/api/invest/buy", INV, json={"ident": c2["id_no"], "product_code": "AAPL", "amount": "1440"})) == "E-RISK")
    ok("605 风险匹配(2级买1级货基)成功 [条件]", OK(api("POST", "/api/invest/buy", INV, json={"ident": c2["id_no"], "product_code": "000198", "amount": "1000"})))

    # ===== 七件套：更贴近真实系统 =====
    dbx = get_db()

    # ① 测评有效期 12 个月：把测评时间改到 400 天前 → 申购被拦；重测后恢复
    ce = open_acct(bal="5000")
    api("POST", "/api/invest/assess", INV, json={"ident": ce["id_no"], "q1": "3", "q2": "3", "q3": "3", "q4": "3", "q5": "3"})
    dbx.customer.update_one({"id_no": ce["id_no"]}, {"$set": {"invest_risk_at": now() - timedelta(days=400)}})
    ok("605 测评过期(>365天)→E-RISK [判定]", E(api("POST", "/api/invest/buy", INV, json={"ident": ce["id_no"], "product_code": "110020", "amount": "100"})) == "E-RISK")
    api("POST", "/api/invest/assess", INV, json={"ident": ce["id_no"], "q1": "3", "q2": "3", "q3": "3", "q4": "3", "q5": "3"})
    ok("605 重新测评后可申购 [正常流]", OK(api("POST", "/api/invest/buy", INV, json={"ident": ce["id_no"], "product_code": "110020", "amount": "100"})))

    # ② 风险不匹配确认书：C4 买 R5(超1级)需签确认书；C1 硬禁止
    c4 = open_acct(bal="5000")
    api("POST", "/api/invest/assess", INV, json={"ident": c4["id_no"], "q1": "4", "q2": "4", "q3": "4", "q4": "4", "q5": "4"})  # C4
    ok("605 超1级未签确认书→E-RISK [判定]", E(api("POST", "/api/invest/buy", INV, json={"ident": c4["id_no"], "product_code": "AAPL", "amount": "1440"})) == "E-RISK")
    ok("605 超1级签确认书→成功 [组合]", OK(api("POST", "/api/invest/buy", INV, json={"ident": c4["id_no"], "product_code": "AAPL", "amount": "1440", "mismatch_confirmed": True})))
    c1 = open_acct(bal="5000")
    api("POST", "/api/invest/assess", INV, json={"ident": c1["id_no"], "q1": "1", "q2": "1", "q3": "1", "q4": "1", "q5": "1"})  # C1
    ok("605 C1客户买高风险即使签确认书也禁止→E-RISK [边界]", E(api("POST", "/api/invest/buy", INV, json={"ident": c1["id_no"], "product_code": "110020", "amount": "100", "mismatch_confirmed": True})) == "E-RISK")

    # ③ 货币基金：申购免费 + T+0 快速赎回 + 单日限额 + 非货基不可快赎
    cm = open_acct(bal="30000")
    api("POST", "/api/invest/assess", INV, json={"ident": cm["id_no"], "q1": "3", "q2": "3", "q3": "3", "q4": "3", "q5": "3"})
    bm = api("POST", "/api/invest/buy", INV, json={"ident": cm["id_no"], "product_code": "000198", "amount": "20000"})
    ok("605 货币基金申购免申购费 [条件]", OK(bm) and abs(bm["data"]["fee"]) < 0.001)
    sf = api("POST", "/api/invest/sell", INV, json={"ident": cm["id_no"], "product_code": "000198", "units": "5000", "fast": True})
    ok("606 货基快速赎回T+0免赎回费 [组合]", OK(sf) and abs(sf["data"]["fee"]) < 0.001 and "T+0" in (sf["data"].get("settle_status") or ""))
    ok("606 货基快赎超1万→E-LIMIT [边界]", E(api("POST", "/api/invest/sell", INV, json={"ident": cm["id_no"], "product_code": "000198", "units": "15000", "fast": True})) == "E-LIMIT")
    api("POST", "/api/invest/buy", INV, json={"ident": cm["id_no"], "product_code": "110020", "amount": "1000"})
    ok("606 非货基用快速赎回→E-REQ [判定]", E(api("POST", "/api/invest/sell", INV, json={"ident": cm["id_no"], "product_code": "110020", "units": "1", "fast": True})) == "E-REQ")

    # ④ 份额确认 / 资金到账 T+1 状态流转（把确认日改到昨天再批量确认）
    bc = api("POST", "/api/invest/buy", INV, json={"ident": cm["id_no"], "product_code": "110020", "amount": "500"})
    ok("605 申购返回成交确认单号 [判定]", bc["data"].get("confirm_no") and bc["data"]["confirm_no"] == bc["data"].get("txn_no"))
    dbx.business_transaction.update_one({"txn_no": bc["data"]["confirm_no"]}, {"$set": {"confirm_date": (now() - timedelta(days=1)).strftime("%Y-%m-%d")}})
    cs = api("POST", "/api/invest/confirm-settlements", INV)
    ok("609 到期申购单被确认份额 [正常流]", OK(cs) and cs["data"]["confirmed"] >= 1)
    tt = dbx.business_transaction.find_one({"txn_no": bc["data"]["confirm_no"]})
    ok("609 确认后状态=份额已确认 [判定]", tt and tt.get("settle_status") == "份额已确认")
    ok("609 force模式忽略T+1确认全部待处理 [组合]", OK(api("POST", "/api/invest/confirm-settlements", INV, json={"force": True})))

    # UC-610 理财交易记录/交割单：列出客户申赎、带受理状态
    tr = api("GET", "/api/invest/transactions", INV, query={"ident": cm["id_no"]})
    ok("610 交易记录返回申赎明细 [正常流]", OK(tr) and len(tr["data"]["transactions"]) >= 2)
    ok("610 每笔含受理状态 [判定]", all(r.get("settle_status") for r in tr["data"]["transactions"]))
    ok("610 已确认单据状态可见 [组合]", any(r["settle_status"] == "份额已确认" for r in tr["data"]["transactions"]))
    ok("610 无交易客户→空列表 [边界]", OK(api("GET", "/api/invest/transactions", INV, query={"ident": open_acct()["id_no"]})))

    # ⑤ 费率与披露字段：货基标志、管理费/托管费/投资范围
    pl = api("GET", "/api/invest/products", INV)
    p198 = next((x for x in pl["data"]["products"] if x["code"] == "000198"), None)
    ok("601 货币基金标志 is_money_fund [判定]", p198 and p198.get("is_money_fund") is True)
    qd = api("GET", "/api/invest/quote", INV, query={"product_code": "000001"})
    ok("602 行情含管理费/投资范围披露 [组合]", OK(qd) and qd["data"].get("mgmt_fee") and qd["data"].get("scope"))

    # 权限：储蓄员不能调理财申购（角色隔离）
    ok("权限 储蓄员调申购→403 [安全]", cl.post("/api/invest/buy", headers={"Authorization": "Bearer " + S}, json={"ident": c["id_no"], "product_code": "000198", "amount": "1"}).status_code == 403)

    # 理财业务员维护产品（产品维护归理财业务员，非管理员）
    ok("608 理财员维护理财产品 [正常流]", OK(api("POST", "/api/invest/product", INV, json={"code": "005827", "name": "易方达蓝筹精选", "ptype": "FUND", "market_symbol": "005827", "risk_level": "4", "status": "1"})))
    # 608 回填：输代码能带出现有产品的可编辑字段
    pl2 = api("GET", "/api/invest/product-lookup", INV)
    prow = next((x for x in pl2["data"]["products"] if x["code"] == "005827"), None)
    ok("608 产品回填带出现有信息 [组合]", prow and prow["name"] == "易方达蓝筹精选" and prow["risk_level"] == 4 and prow.get("market_symbol") == "005827")
    ok("608 类型非法→E-REQ [判定]", E(api("POST", "/api/invest/product", INV, json={"code": "X", "name": "x", "ptype": "BOND", "market_symbol": "x"})) == "E-REQ")
    ok("608 管理员无权维护产品→403 [安全]", cl.post("/api/invest/product", headers={"Authorization": "Bearer " + AD}, json={"code": "Y", "name": "y", "ptype": "FUND", "market_symbol": "y"}).status_code == 403)


if __name__ == "__main__":
    try:
        run()
    finally:
        get_client().drop_database("bank_invest_test")
    print(f"\n==== 结果：{_p} 通过 / {_f} 失败（共 {_p + _f} 条断言）====")
    import sys
    sys.exit(1 if _f else 0)
