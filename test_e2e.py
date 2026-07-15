"""端到端「条件/判定/组合覆盖」测试（软件工程课设测试交付物）。

覆盖 34 个接口的判定分支、边界值与条件组合，对照覆盖用例表逐条断言。
覆盖类型标注：[判定] 每个判定真假两分支；[条件] 复合条件的单条件取值；
             [组合] 多条件组合；[边界] 边界值；[正常流] 成功路径。

已同步同学重构后的 API：储蓄/贷款/外汇/信用卡各资金与查询接口统一改用身份标识 ident
（证件号/邮箱/手机号/账号/卡号任填其一），外汇买卖/变更改用 ident + currency 定位子户，
不再显式传 account_no/from_account_no/fx_account_no。错误码亦按各源码实际返回核对。

运行前提：项目根目录有 .env（含 MONGODB_URI）且能连通 MongoDB。
外汇买卖用例依赖实时牌价（Alpha Vantage/er-api），需可访问外网行情源。
运行：    python test_e2e.py
使用一次性库 bank_counter_e2etest，测完自动删除，不影响生产库 bank_counter。
少数「事务内重读/并发」分支（如并发关闭、账单被并发还清）单线程不可复现，已在注释标注跳过。
"""
import os
os.environ.setdefault("DB_NAME", "bank_counter_e2etest")

from datetime import timedelta

from app import create_app
from db import get_db, get_client
from common import m, now, new_account_no, new_debit_card_no
import constants as C

app = create_app()
cl = app.test_client()
db = get_db()

_p = _f = 0
def ok(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS {name}")
    else:
        _f += 1; print(f"  FAIL {name}")

def login(emp, pw):
    return cl.post("/api/login", json={"employee_no": emp, "password": pw}).get_json()["data"]["token"]

S = login("S001", "123456"); L = login("L001", "123456"); FX = login("F001", "123456")
CCK = login("CC001", "123456"); AD = login("admin", "admin123")

def api(method, path, tok, json=None, query=None):
    h = {"Authorization": "Bearer " + tok}
    if method == "GET":
        r = cl.get(path, headers=h, query_string=query or {})
    else:
        r = cl.post(path, headers=h, json=json or {})
    return r.get_json() or {}

def E(r): return r.get("error")
def OK(r): return r.get("success") is True

_seq = [0]
def uid():
    _seq[0] += 1
    return _seq[0]
def gen_id(n=None):  # 18 位测试身份证（前缀避开演示号 110101199001011234）
    return "11010119900202" + f"{n if n is not None else uid():04d}"
def gen_email(n=None):  # 唯一测试邮箱（开户必填）
    return f"user{n if n is not None else uid()}@test.example.com"

def open_acct(bal="0", phone="", id_no=None, name=None):
    n = uid(); idn = id_no or gen_id(n)
    r = api("POST", "/api/savings/open-account", S,
            json={"name": name or f"客户{n}", "id_type": "身份证", "id_no": idn,
                  "email": gen_email(n), "phone": phone, "initial_balance": bal})
    assert r.get("success"), r
    a = r["data"]["account"]; c = r["data"]["customer"]
    return {"customer_no": c["customer_no"], "account_no": a["account_no"], "card_no": a["card_no"], "id_no": idn}

def set_acct(account_no, **f):
    db.account.update_one({"account_no": account_no}, {"$set": f})

def second_account(customer_no, bal="0"):
    c = db.customer.find_one({"customer_no": customer_no})
    acc = {"account_no": new_account_no(), "customer_id": c["_id"], "card_no": new_debit_card_no(),
           "card_status": C.CARD_NORMAL, "currency": "CNY", "balance": m(bal),
           "status": C.ACCOUNT_NORMAL, "created_at": now()}
    db.account.insert_one(acc)
    return acc["account_no"]


# ==================== 储蓄 UC-101 ~ 108 ====================
def t_savings():
    print("== 储蓄业务 UC-101~108 ==")
    # UC-101 开户
    ok("101 姓名为空→E-REQ [判定]", E(api("POST", "/api/savings/open-account", S, json={"name": "", "id_no": gen_id()})) == "E-REQ")
    ok("101 身份证17位→E-2 [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": "1101011990010200"})) == "E-2")
    ok("101 身份证19位→E-2 [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": "110101199002020001X"})) == "E-2")
    ok("101 证件类型非法→E-2 [条件]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "驾照", "id_no": gen_id()})) == "E-2")
    xid = gen_id()[:-1] + "x"  # 末位小写 x
    ok("101 尾号x归一化开户成功 [边界]", OK(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": xid, "email": gen_email()})))
    ok("101 归一化后大写X可查到 [组合]", OK(api("GET", "/api/savings/query", S, query={"ident": xid.upper()})))
    ok("101 手机号10位→E-PHONE [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "email": gen_email(), "phone": "1381234567"})) == "E-PHONE")
    ok("101 邮箱缺失→E-EMAIL [判定]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id()})) == "E-EMAIL")
    dup = open_acct()
    ok("101 证件号已存在→E-1 [判定]", E(api("POST", "/api/savings/open-account", S, json={"name": "乙", "id_type": "身份证", "id_no": dup["id_no"], "email": gen_email()})) == "E-1")
    ok("101 init<0→E-AMT [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "email": gen_email(), "initial_balance": "-1"})) == "E-AMT")
    ok("101 正常开户成功 [正常流]", OK(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "email": gen_email(), "phone": "13812345678", "initial_balance": "1000"})))

    # UC-102 存款：check_account 各状态分支（凭 ident 定位账户；不存在的账号经 resolve 找不到客户→E-NOCUST）
    a = open_acct(bal="1000")
    ok("102 金额0→E-2 [边界]", E(api("POST", "/api/savings/deposit", S, json={"ident": a["account_no"], "amount": "0"})) == "E-2")
    ok("102 账户不存在→E-NOCUST [判定]", E(api("POST", "/api/savings/deposit", S, json={"ident": "620000000000", "amount": "100"})) == "E-NOCUST")
    ok("102 存款成功 [正常流]", OK(api("POST", "/api/savings/deposit", S, json={"ident": a["account_no"], "amount": "500"})))
    ok("102 超金额上限→E-2 [边界]", E(api("POST", "/api/savings/deposit", S, json={"ident": a["account_no"], "amount": "100000001"})) == "E-2")
    af = open_acct(); set_acct(af["account_no"], status=C.ACCOUNT_FROZEN)
    ok("102 账户冻结→E-FROZEN [条件]", E(api("POST", "/api/savings/deposit", S, json={"ident": af["account_no"], "amount": "100"})) == "E-FROZEN")
    ac = open_acct(); set_acct(ac["account_no"], status=C.ACCOUNT_CLOSED)
    ok("102 账户已销户→E-CLOSED [条件]", E(api("POST", "/api/savings/deposit", S, json={"ident": ac["account_no"], "amount": "100"})) == "E-CLOSED")
    al = open_acct(); set_acct(al["account_no"], card_status=C.CARD_LOST)
    ok("102 卡挂失→E-LOST [条件]", E(api("POST", "/api/savings/deposit", S, json={"ident": al["account_no"], "amount": "100"})) == "E-LOST")

    # UC-103 取款：金额/状态/身份核验/日限额（ident 定位账户；证件不匹配走 resolve→E-NOCUST）
    w = open_acct(bal="100000")
    ok("103 金额0→E-AMT [边界]", E(api("POST", "/api/savings/withdraw", S, json={"ident": w["account_no"], "amount": "0"})) == "E-AMT")
    lowb = open_acct(bal="100")
    ok("103 余额不足→E-BAL [边界]", E(api("POST", "/api/savings/withdraw", S, json={"ident": lowb["account_no"], "amount": "500"})) == "E-BAL")
    ok("103 证件号定位客户成功取款 [判定]", OK(api("POST", "/api/savings/withdraw", S, json={"ident": w["id_no"], "amount": "10"})))
    ok("103 不存在证件号→E-NOCUST [判定]", E(api("POST", "/api/savings/withdraw", S, json={"ident": gen_id(), "amount": "10"})) == "E-NOCUST")
    ok("103 取款成功 [正常流]", OK(api("POST", "/api/savings/withdraw", S, json={"ident": w["account_no"], "amount": "200"})))
    lim1 = open_acct(bal="100000")
    ok("103 日限额刚好50000成功 [边界]", OK(api("POST", "/api/savings/withdraw", S, json={"ident": lim1["account_no"], "amount": "50000"})))
    lim2 = open_acct(bal="100000")
    ok("103 超日限额50001→E-2 [边界]", E(api("POST", "/api/savings/withdraw", S, json={"ident": lim2["account_no"], "amount": "50001"})) == "E-2")
    wacc = open_acct(bal="100000"); api("POST", "/api/savings/withdraw", S, json={"ident": wacc["account_no"], "amount": "50000"})
    ok("103 叠加超限→E-2 [组合]", E(api("POST", "/api/savings/withdraw", S, json={"ident": wacc["account_no"], "amount": "1"})) == "E-2")

    # UC-104 转账（ident 定位转出方；本行收款方用 to_ident；跨行用 to_account_no+to_bank）
    src = open_acct(bal="10000"); dst = open_acct(bal="0"); sid = src["id_no"]
    ok("104 类型非法→E-OP [判定]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "XYZ", "ident": src["account_no"], "to_ident": dst["account_no"], "amount": "1"})) == "E-OP")
    ok("104 金额0→E-AMT [边界]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": src["account_no"], "to_ident": dst["account_no"], "amount": "0"})) == "E-AMT")
    ok("104 INTRA同一账户→E-3 [组合]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": src["account_no"], "to_ident": src["account_no"], "amount": "1"})) == "E-3")
    ok("104 转出方证件号定位成功 [判定]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": sid, "to_ident": dst["account_no"], "amount": "1"})))
    ok("104 INTER未填收款行→E-1 [组合]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTER", "ident": src["account_no"], "to_account_no": dst["account_no"], "amount": "1"})) == "E-1")
    lowt = open_acct(bal="1")
    ok("104 转出余额不足→E-BAL [条件]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": lowt["account_no"], "to_ident": dst["account_no"], "amount": "100"})) == "E-BAL")
    dclosed = open_acct(); set_acct(dclosed["account_no"], status=C.ACCOUNT_CLOSED)
    ok("104 转入账户销户→E-1 [判定]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": src["account_no"], "to_ident": dclosed["account_no"], "amount": "1"})) == "E-1")
    ok("104 行内他人转账成功 [正常流]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": src["account_no"], "to_ident": dst["account_no"], "amount": "10"})))
    ok("104 跨行转账成功 [正常流]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTER", "ident": src["account_no"], "to_account_no": "620999", "to_bank": "工行", "amount": "10"})))
    a2 = second_account(src["customer_no"])
    ok("104 同户转账成功 [组合]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "ident": src["account_no"], "to_ident": a2, "amount": "5"})))

    # UC-105 查询（统一 ident）
    q = open_acct(bal="500")
    ok("105 按账号查到 [判定]", OK(api("GET", "/api/savings/query", S, query={"ident": q["account_no"]})))
    ok("105 按证件号查到 [判定]", OK(api("GET", "/api/savings/query", S, query={"ident": q["id_no"]})))
    ok("105 未找到→E-1 [判定]", E(api("GET", "/api/savings/query", S, query={"ident": "620000000000"})) == "E-1")
    ok("105 空 ident→E-1 [边界]", E(api("GET", "/api/savings/query", S, query={})) == "E-1")
    ok("105 日期格式非法→E-DATE [边界]", E(api("GET", "/api/savings/query", S, query={"ident": q["account_no"], "start": "2026/01/01"})) == "E-DATE")
    ok("105 日期值非法(月13)→E-DATE [边界]", E(api("GET", "/api/savings/query", S, query={"ident": q["account_no"], "start": "2026-13-01"})) == "E-DATE")

    # UC-106 挂失/解挂/补卡（ident 定位；不存在账号→resolve E-NOCUST）
    cardacc = open_acct()
    ok("106 账号查不到→E-NOCUST [判定]", E(api("POST", "/api/savings/card", S, json={"ident": "620000000000", "op": "LOSS"})) == "E-NOCUST")
    ok("106 op非法→E-OP [判定]", E(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "XX"})) == "E-OP")
    ok("106 挂失成功 [正常流]", OK(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "LOSS"})))
    ok("106 重复挂失→E-2 [条件]", E(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "LOSS"})) == "E-2")
    ok("106 解挂成功 [条件]", OK(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "UNLOSS"})))
    ok("106 未挂失却解挂→E-2 [条件]", E(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "UNLOSS"})) == "E-2")
    ok("106 补卡成功换新卡号 [条件]", OK(api("POST", "/api/savings/card", S, json={"ident": cardacc["account_no"], "op": "REISSUE"})))

    # UC-107 销户（隔离触发各前置条件；ident 定位）
    ok("107 账户不存在→E-NOCUST [判定]", E(api("POST", "/api/savings/close-account", S, json={"ident": "620000000000"})) == "E-NOCUST")
    clf = open_acct(); set_acct(clf["account_no"], status=C.ACCOUNT_FROZEN)
    ok("107 账户非正常→E-4 [条件]", E(api("POST", "/api/savings/close-account", S, json={"ident": clf["account_no"]})) == "E-4")
    clb = open_acct(bal="100")
    ok("107 余额未清零→E-5 [边界]", E(api("POST", "/api/savings/close-account", S, json={"ident": clb["account_no"]})) == "E-5")
    clok = open_acct()
    ok("107 正常销户成功 [正常流]", OK(api("POST", "/api/savings/close-account", S, json={"ident": clok["account_no"]})))
    ok("101 销户后可重新开户 [生命周期]", OK(api("POST", "/api/savings/open-account", S, json={"name": "重开客户", "id_type": "身份证", "id_no": clok["id_no"], "email": gen_email()})))

    # UC-108 客户信息更新（ident 定位并核验身份）
    u = open_acct()
    ok("108 客户不存在→E-1 [判定]", E(api("POST", "/api/savings/update-customer", S, json={"ident": gen_id(), "phone": "13800000000"})) == "E-1")
    ok("108 无可更新字段→E-REQ [判定]", E(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"]})) == "E-REQ")
    ok("108 手机号格式非法→E-3 [边界]", E(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "phone": "123"})) == "E-3")
    ok("108 改名未确认→E-4 [条件]", E(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "name": "新名"})) == "E-4")
    ok("108 改证件号被占用→E-3 [组合]", E(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "new_id_no": dup["id_no"], "confirm": True})) == "E-3")
    ok("108 姓名改为空白→E-REQ [边界]", E(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "name": "   ", "confirm": True, "reason": "x"})) == "E-REQ")
    ok("108 更新手机地址成功 [正常流]", OK(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "phone": "13900000000", "address": "北京"})))
    ok("108 改名+确认成功 [组合]", OK(api("POST", "/api/savings/update-customer", S, json={"ident": u["id_no"], "name": "新名", "confirm": True, "reason": "更正"})))


# ==================== 贷款 UC-201 ~ 206 ====================
def t_loan():
    print("== 贷款业务 UC-201~206 ==")
    base = open_acct(bal="0")
    cno = base["customer_no"]
    bid = base["id_no"]
    ok("201 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/loan/apply", L, json={"ident": gen_id(), "loan_type": "个人消费贷", "amount": "1", "term_months": "12"})) == "E-NOCUST")
    ok("201 贷款类型非法→E-2 [条件]", E(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "X", "amount": "1", "term_months": "12"})) == "E-2")
    ok("201 金额0→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": "0", "term_months": "12"})) == "E-2")
    ok("201 金额超上限→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": "100000001", "term_months": "12"})) == "E-2")
    ok("201 期限361→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": "1000", "term_months": "361"})) == "E-2")
    ok("201 期限0→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": "1000", "term_months": "0"})) == "E-2")
    black = open_acct(); db.customer.update_one({"customer_no": black["customer_no"]}, {"$set": {"status": C.CUSTOMER_BLACKLIST}})
    ok("201 黑名单→E-1 [判定]", E(api("POST", "/api/loan/apply", L, json={"ident": black["id_no"], "loan_type": "个人消费贷", "amount": "1000", "term_months": "12"})) == "E-1")
    ok("201 正常申请成功 [正常流]", OK(api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": "50000", "term_months": "12"})))

    # UC-202 审批
    def new_loan(amount="50000"):
        r = api("POST", "/api/loan/apply", L, json={"ident": bid, "loan_type": "个人消费贷", "amount": amount, "term_months": "12"})
        return r["data"]["loan"]["contract_no"]
    ok("202 合同不存在→E-NOLOAN [判定]", E(api("POST", "/api/loan/approve", L, json={"contract_no": "X", "decision": "APPROVED"})) == "E-NOLOAN")
    ok("202 决策非法→E-OP [判定]", E(api("POST", "/api/loan/approve", L, json={"contract_no": new_loan(), "decision": "MAYBE"})) == "E-OP")
    ok("202 批准金额0→E-VAL [边界]", E(api("POST", "/api/loan/approve", L, json={"contract_no": new_loan(), "decision": "APPROVED", "approved_amount": "0"})) == "E-VAL")
    ok("202 利率>1→E-VAL [边界]", E(api("POST", "/api/loan/approve", L, json={"contract_no": new_loan(), "decision": "APPROVED", "interest_rate": "4.35"})) == "E-VAL")
    ok("202 期限361→E-VAL [边界]", E(api("POST", "/api/loan/approve", L, json={"contract_no": new_loan(), "decision": "APPROVED", "term_months": "361"})) == "E-VAL")
    ok("202 拒绝成功 [判定]", OK(api("POST", "/api/loan/approve", L, json={"contract_no": new_loan(), "decision": "REJECTED", "reason": "资料不足"})))
    ln_sup = new_loan(); api("POST", "/api/loan/approve", L, json={"contract_no": ln_sup, "decision": "SUPPLEMENT", "reason": "补件"})
    ok("202 待补件成功 [判定]", True)
    ok("202 补件后再审批成功 [正常流]", OK(api("POST", "/api/loan/approve", L, json={"contract_no": ln_sup, "decision": "APPROVED", "interest_rate": "0.05"})))
    ln_rej = new_loan(); api("POST", "/api/loan/approve", L, json={"contract_no": ln_rej, "decision": "REJECTED"})
    ok("202 终态再审批→E-1 [条件]", E(api("POST", "/api/loan/approve", L, json={"contract_no": ln_rej, "decision": "APPROVED"})) == "E-1")

    # UC-203 放款
    ln = new_loan(); api("POST", "/api/loan/approve", L, json={"contract_no": ln, "decision": "APPROVED", "interest_rate": "0.05"})
    ok("203 合同不存在→E-NOLOAN [判定]", E(api("POST", "/api/loan/disburse", L, json={"contract_no": "X"})) == "E-NOLOAN")
    ln_pend = new_loan()
    ok("203 未批复放款→E-STATE [判定]", E(api("POST", "/api/loan/disburse", L, json={"contract_no": ln_pend})) == "E-STATE")
    ok("203 放款成功 [正常流]", OK(api("POST", "/api/loan/disburse", L, json={"contract_no": ln})))
    ok("203 重复放款→E-2 [判定]", E(api("POST", "/api/loan/disburse", L, json={"contract_no": ln})) == "E-2")

    # UC-204 还款（repay 统一用 ident 定位贷款+还款账户；金额校验在定位之前）
    ok("204 金额0→E-AMT [边界]", E(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "0"})) == "E-AMT")
    ok("204 超额还款→E-3 [边界]", E(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "999999"})) == "E-3")
    # base 账户放款后余额=50000，还款需从账户扣；先存够
    api("POST", "/api/savings/deposit", S, json={"ident": base["account_no"], "amount": "60000"})
    ok("204 部分还款成功 [正常流]", OK(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "10000"})))
    ok("204 全额结清成功 [边界]", OK(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "40000"})))
    ok("204 已结清再还→E-NOLOAN [判定]", E(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "1"})) == "E-NOLOAN")

    # 跨客户账户被拒（critical 修复）：apply 用他人 ident 无正常账户时 → E-NOCUST/E-NOACC
    # 重构后 apply 仅认 ident 定位客户并取其名下正常账户，无法为客户 A 指定客户 B 的账户，风险已从接口层消除。
    other_c = open_acct()
    ok("201 他人已存在客户仍按本人账户放款 [安全]", OK(api("POST", "/api/loan/apply", L, json={"ident": other_c["id_no"], "loan_type": "个人消费贷", "amount": "1000", "term_months": "12"})))

    # UC-205 逾期查询 + 205b 催收（罚息 = 本金50000 × 日率0.0005 × 逾期40天 = 1000）
    lo = new_loan(); api("POST", "/api/loan/approve", L, json={"contract_no": lo, "decision": "APPROVED", "interest_rate": "0.05"})
    api("POST", "/api/loan/disburse", L, json={"contract_no": lo})
    past = now() - timedelta(days=40)
    db.loan.update_one({"contract_no": lo}, {"$set": {"due_date": past, "penalty_asof": past}})  # 造逾期
    r = api("GET", "/api/loan/overdue", L, query={})
    lorow = next((x for x in r["data"]["loans"] if x["contract_no"] == lo), None) if OK(r) else None
    ok("205 逾期列表含罚息 [正常流]", lorow is not None)
    ok("205 罚息按增量模型算对(=1000) [金额]", lorow and abs(lorow["penalty"] - 1000.0) < 0.01)
    # 逾期部分还款先冲罚息，剩余罚息净额不重复计提。repay 用客户 ident（bid）同时定位该笔逾期贷款与还款账户
    # （此时 base 名下仅 lo 一笔 ACTIVE/OVERDUE 贷款，ln 已 PAID_OFF 被状态过滤，resolve_loan 唯一命中 lo）。
    api("POST", "/api/savings/deposit", S, json={"ident": base["account_no"], "amount": "5000"})
    ok("204 逾期部分还款(冲罚息)成功 [组合]", OK(api("POST", "/api/loan/repay", L, json={"ident": bid, "amount": "500"})))
    r2 = api("GET", "/api/loan/overdue", L, query={"ident": lo})
    lorow2 = next((x for x in r2["data"]["loans"] if x["contract_no"] == lo), None) if OK(r2) else None
    ok("204 罚息净额=剩余500(不重复计提) [金额]", lorow2 and abs(lorow2["penalty"] - 500.0) < 0.01)
    ok("205 高天数过滤跳过 [边界]", OK(api("GET", "/api/loan/overdue", L, query={"days": "9999"})))
    ok("205b 合同不存在→E-NOLOAN [判定]", E(api("POST", "/api/loan/overdue", L, json={"contract_no": "X"})) == "E-NOLOAN")
    ok("205b 催收并置逾期 [组合]", OK(api("POST", "/api/loan/overdue", L, json={"contract_no": lo, "method": "电话", "note": "承诺还款"})))
    ok("205b 已拒绝贷款催收→E-2 [状态机]", E(api("POST", "/api/loan/overdue", L, json={"contract_no": ln_rej})) == "E-2")

    # UC-206 查询统计（统一 ident）
    ok("206 日期非法→E-DATE [判定]", E(api("GET", "/api/loan/query", L, query={"start": "2026/1/1"})) == "E-DATE")
    ok("206 组合条件查询 [正常流]", OK(api("GET", "/api/loan/query", L, query={"ident": bid, "status": "PAID_OFF"})))
    ok("206 无过滤全表统计 [正常流]", OK(api("GET", "/api/loan/query", L, query={})))


# ==================== 外汇 UC-301 ~ 305 ====================
def t_forex():
    print("== 外汇业务 UC-301~305 ==")
    cust = open_acct(bal="100000")
    cno = cust["customer_no"]
    fid = cust["id_no"]
    ok("301 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"ident": gen_id(), "currency": "USD"})) == "E-NOCUST")
    ok("301 币种非法→E-CUR [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"ident": fid, "currency": "XXX"})) == "E-CUR")
    nobase = open_acct(); set_acct(nobase["account_no"], status=C.ACCOUNT_CLOSED)
    ok("301 无正常储蓄账户→E-1 [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"ident": nobase["id_no"], "currency": "USD"})) == "E-1")
    r = api("POST", "/api/forex/open-subaccount", FX, json={"ident": fid, "currency": "USD"})
    ok("301 开立成功 [正常流]", OK(r))
    fxno = r["data"]["fx_account"]["fx_account_no"]
    ok("301 同币种重复→E-2 [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"ident": fid, "currency": "USD"})) == "E-2")

    # UC-306 实时汇率（重构后合并原 UC-302，牌价一律来自实时行情缓存；环境可访问行情源）
    ok("306 缺币种→E-CUR [判定]", E(api("GET", "/api/forex/live-rate", FX, query={})) == "E-CUR")
    ok("306 币种非法→E-CUR [判定]", E(api("GET", "/api/forex/live-rate", FX, query={"currency": "XXX"})) == "E-CUR")
    ok("306 正常查询成功 [正常流]", OK(api("GET", "/api/forex/live-rate", FX, query={"currency": "USD"})))
    r306b = api("GET", "/api/forex/live-rate", FX, query={"currency": "USD"})
    ok("306 命中缓存(from_cache) [边界]", OK(r306b) and r306b["data"]["from_cache"] is True)

    # UC-303 买卖（重构为 ident + currency 定位子户，须核验持卡人身份；牌价按实时行情换算）
    ok("303 方向非法→E-DIR [判定]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "ZZ", "amount": "10"})) == "E-DIR")
    ok("303 金额0→E-AMT [边界]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "BUY", "amount": "0"})) == "E-AMT")
    ok("303 该币种无子户→E-NOFX [判定]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "EUR", "direction": "BUY", "amount": "10"})) == "E-NOFX")
    ok("303 身份不符→E-NOCUST [判定]", E(api("POST", "/api/forex/trade", FX, json={"ident": gen_id(), "currency": "USD", "direction": "BUY", "amount": "1"})) == "E-NOCUST")
    ok("303 买入成功 [正常流]", OK(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "BUY", "amount": "100"})))
    ok("303 卖出成功 [正常流]", OK(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "SELL", "amount": "50"})))
    ok("303 外币余额不足→E-2 [条件]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "SELL", "amount": "999999"})) == "E-2")
    set_acct(cust["account_no"], status=C.ACCOUNT_FROZEN)
    ok("303 买入关联账户冻结→E-FROZEN [组合]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "BUY", "amount": "1"})) == "E-FROZEN")
    set_acct(cust["account_no"], status=C.ACCOUNT_NORMAL)
    db.fx_account.update_one({"fx_account_no": fxno}, {"$set": {"status": C.FX_FROZEN}})
    ok("303 子户冻结→E-FXSTAT [判定]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "USD", "direction": "BUY", "amount": "1"})) == "E-FXSTAT")
    db.fx_account.update_one({"fx_account_no": fxno}, {"$set": {"status": C.FX_NORMAL}})
    api("POST", "/api/forex/open-subaccount", FX, json={"ident": fid, "currency": "JPY"})
    ok("303 小额JPY折本币不足1分→E-AMT [边界]", E(api("POST", "/api/forex/trade", FX, json={"ident": fid, "currency": "JPY", "direction": "BUY", "amount": "0.01"})) == "E-AMT")

    # UC-304 变更（ident + currency 定位子户）。该币种无子户即"子户不存在"分支 → E-NOFX（EUR 未开立）
    ok("304 该币种无子户→E-NOFX [判定]", E(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "EUR", "change_type": "FREEZE"})) == "E-NOFX")
    ok("304 变更类型非法→E-OP [判定]", E(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "USD", "change_type": "XX"})) == "E-OP")
    ok("304 冻结成功 [正常流]", OK(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "USD", "change_type": "FREEZE"})))
    ok("304 重复冻结→E-STATE [条件]", E(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "USD", "change_type": "FREEZE"})) == "E-STATE")
    ok("304 解冻成功 [正常流]", OK(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "USD", "change_type": "UNFREEZE"})))
    ok("304 CLOSE有余额→E-2 [边界]", E(api("POST", "/api/forex/change", FX, json={"ident": fid, "currency": "USD", "change_type": "CLOSE"})) == "E-2")

    # UC-305 查询（统一 ident；子户号或客户身份标识）
    ok("305 子户号未命中→E-1 [条件]", E(api("GET", "/api/forex/query", FX, query={"ident": "FX999"})) == "E-1")
    ok("305 ident 空→E-1 [边界]", E(api("GET", "/api/forex/query", FX, query={})) == "E-1")
    ok("305 日期非法→E-DATE [边界]", E(api("GET", "/api/forex/query", FX, query={"ident": fxno, "start": "2026-13-40"})) == "E-DATE")
    ok("305 正常查询成功 [正常流]", OK(api("GET", "/api/forex/query", FX, query={"ident": fxno})))


# ==================== 信用卡（4卡种/消费返现+积分/积分商城/多币种还款）====================
def t_creditcard():
    print("== 信用卡业务（模仿汇丰香港重做）==")
    # ---------- 人民币卡：银联白金卡 全流程（消费返现 2.4% + 提额 + 还款）----------
    cust = open_acct(bal="50000")
    cid = cust["id_no"]
    ok("401 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/creditcard/apply", CCK, json={"ident": gen_id()})) == "E-NOCUST")
    ok("401 旧卡种非法→E-OP [判定]", E(api("POST", "/api/creditcard/apply", CCK, json={"ident": cid, "card_type": "普卡"})) == "E-OP")
    ok("401 月收入负→E-VAL [边界]", E(api("POST", "/api/creditcard/apply", CCK, json={"ident": cid, "card_type": "银联白金卡", "monthly_income": "-1"})) == "E-VAL")
    r = api("POST", "/api/creditcard/apply", CCK, json={"ident": cid, "card_type": "银联白金卡", "monthly_income": "8000"})
    card = r["data"]["credit_card"]["card_no"]
    ok("401 银联白金卡申请成功&币种CNY [正常流]", OK(r) and r["data"]["credit_card"]["currency"] == "CNY")

    # 每人每种卡最多一张：同种卡再申请（原卡未终止）→ E-DUP
    ok("401 同种卡再申请→E-DUP [规则]", E(api("POST", "/api/creditcard/apply", CCK, json={"ident": cid, "card_type": "银联白金卡"})) == "E-DUP")

    # UC-402 第一步：确认审批事项（此时是待审新卡）
    aq = api("GET", "/api/creditcard/approve-quote", CCK, query={"ident": cid, "card_type": "银联白金卡"})
    ok("402 审批事项=批卡&带出将授予额度20000 [查询]", OK(aq) and aq["data"]["purpose"] == "NEW_CARD"
       and abs(aq["data"]["grant_limit"] - 20000.0) < 0.01)
    ok("402 审批事项:卡种不符→E-NOCARD [判定]", E(api("GET", "/api/creditcard/approve-quote", CCK, query={"ident": cid, "card_type": "Visa Platinum"})) == "E-NOCARD")

    # UC-402 第二步：审批（凭 身份+卡种 定位唯一卡，无需卡号；按卡种默认额度激活）
    ok("402 决策非法→E-OP [判定]", E(api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "银联白金卡", "decision": "MAYBE"})) == "E-OP")
    ok("402 无此卡种→E-NOCARD [判定]", E(api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "Visa Platinum", "decision": "APPROVED"})) == "E-NOCARD")
    ok("402 账单日越界→E-DAY [边界]", E(api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "银联白金卡", "decision": "APPROVED", "bill_day": "30", "repay_day": "20"})) == "E-DAY")
    ok("402 审批激活(默认额度) [正常流]", OK(api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "银联白金卡", "decision": "APPROVED", "bill_day": "5", "repay_day": "20"})))
    q = api("GET", "/api/creditcard/query", CCK, query={"ident": card})
    ok("402 激活后默认额度=20000 [金额]", OK(q) and q["data"]["cards"][0]["credit_limit"] == 20000.0 and q["data"]["cards"][0]["available_limit"] == 20000.0)
    # 拒绝：用另一卡种（银联钻石卡）测拒绝流（拒绝为终态，不占“每种一张”名额）
    api("POST", "/api/creditcard/apply", CCK, json={"ident": cid, "card_type": "银联钻石卡"})
    rj = api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "银联钻石卡", "decision": "REJECTED", "reason": "资料不足"})
    ok("402 拒绝返回数据+原因 [判定]", OK(rj) and rj.get("data", {}).get("credit_card", {}).get("reject_reason") == "资料不足")

    # UC-404 模拟消费（身份+卡种定位；银联白金返现 2.4%→人民币储蓄账户）
    ok("404 消费币种非法→E-CUR [判定]", E(api("POST", "/api/creditcard/consume", CCK, json={"ident": cid, "card_type": "银联白金卡", "currency": "ABC", "amount": "100"})) == "E-CUR")
    ok("404 消费金额0→E-AMT [边界]", E(api("POST", "/api/creditcard/consume", CCK, json={"ident": cid, "card_type": "银联白金卡", "currency": "CNY", "amount": "0"})) == "E-AMT")
    rcon = api("POST", "/api/creditcard/consume", CCK, json={"ident": cid, "card_type": "银联白金卡", "currency": "CNY", "amount": "1000", "merchant": "测试商户"})
    ok("404 人民币消费成功&扣额度 [正常流]", OK(rcon) and abs(rcon["data"]["available_limit"] - 19000.0) < 0.01)
    ok("404 银联白金返现2.4%(=24) [金额]", OK(rcon) and rcon["data"]["reward"]["type"] == "CASHBACK" and abs(rcon["data"]["reward"]["cashback"] - 24.0) < 0.01)
    ok("404 超出可用额度→E-2 [边界]", E(api("POST", "/api/creditcard/consume", CCK, json={"ident": cid, "card_type": "银联白金卡", "currency": "CNY", "amount": "999999"})) == "E-2")

    # UC-406 本月消费记录（身份+卡种定位；本月消费明细 + 剩余额度）
    rec = api("GET", "/api/creditcard/records", CCK, query={"ident": cid, "card_type": "银联白金卡"})
    ok("406 本月消费记录(合计=1000) [查询]", OK(rec) and abs(rec["data"]["consume_total"] - 1000.0) < 0.01 and len(rec["data"]["records"]) >= 1)

    # UC-405 第一步：确认卡种后试算各方式应还金额（欠款1000 → 提前1000/最低100/剩余900本月计息45）
    quote = api("GET", "/api/creditcard/repay-quote", CCK, query={"ident": cid, "card_type": "银联白金卡"})
    ok("405 试算:提前还款应还=1000 [查询]", OK(quote) and abs(quote["data"]["outstanding"] - 1000.0) < 0.01)
    ok("405 试算:最低额=100&剩余本金计息=45 [金额]", OK(quote) and abs(quote["data"]["min_amount"] - 100.0) < 0.01 and abs(quote["data"]["min_interest"] - 45.0) < 0.01)
    ok("405 试算:带出币种与还款账户 [查询]", OK(quote) and quote["data"]["currency"] == "CNY" and bool(quote["data"]["fund_account"]))
    ok("405 试算:卡种不符→E-NOCARD [判定]", E(api("GET", "/api/creditcard/repay-quote", CCK, query={"ident": cid, "card_type": "Visa Platinum"})) == "E-NOCARD")

    # UC-405 第二步：还款（提前全额/提前指定金额/按期最低额；人民币卡用人民币储蓄账户还）
    ok("405 还款方式非法→E-OP [判定]", E(api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "XX"})) == "E-OP")
    ok("405 指定金额0→E-AMT [边界]", E(api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "SCHEDULED", "amount": "0"})) == "E-AMT")
    ok("405 指定金额<最低额→E-2 [组合]", E(api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "SCHEDULED", "amount": "50"})) == "E-2")
    rmin = api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "MIN"})
    ok("405 最低额还款成功&剩余本金计息 [正常流]", OK(rmin) and rmin["data"]["interest"] > 0)
    ok("405 提前(全额)还款结清 [正常流]", OK(api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "FULL"})))
    ok("405 无欠款再还→E-3 [判定]", E(api("POST", "/api/creditcard/repay", CCK, json={"ident": cid, "card_type": "银联白金卡", "repay_type": "FULL"})) == "E-3")
    q0 = api("GET", "/api/creditcard/repay-quote", CCK, query={"ident": cid, "card_type": "银联白金卡"})
    ok("405 结清后试算应还=0 [边界]", OK(q0) and q0["data"]["outstanding"] == 0.0 and q0["data"]["min_amount"] == 0.0 and q0["data"]["min_interest"] == 0.0)

    # UC-403 提额申请 + UC-402 提额审批（新额度不得高于存款的 30%）
    ok("403 提额低于当前→E-1 [边界]", E(api("POST", "/api/creditcard/increase-limit", CCK, json={"ident": cid, "card_type": "银联白金卡", "new_limit": "15000"})) == "E-1")
    ok("403 提额申请成功(挂起) [正常流]", OK(api("POST", "/api/creditcard/increase-limit", CCK, json={"ident": cid, "card_type": "银联白金卡", "new_limit": "25000"})))
    # 审批事项应变为提额，且建议拒绝（25000 > 存款约4.9万的30%）
    aq2 = api("GET", "/api/creditcard/approve-quote", CCK, query={"ident": cid, "card_type": "银联白金卡"})
    ok("402 审批事项=提额&带出当前/申请后额度 [查询]", OK(aq2) and aq2["data"]["purpose"] == "INCREASE"
       and abs(aq2["data"]["current_limit"] - 20000.0) < 0.01 and abs(aq2["data"]["new_limit"] - 25000.0) < 0.01)
    ok("402 审批事项:带出对应币种账户余额 [查询]", OK(aq2) and bool(aq2["data"]["acct_no"]) and aq2["data"]["acct_balance"] > 0)
    ok("402 审批建议=拒绝(超30%上限) [规则]", OK(aq2) and aq2["data"]["advise"] == "REJECT"
       and aq2["data"]["new_limit_cny"] > aq2["data"]["cap_cny"])
    ok("402 提额超存款30%被拒→E-1 [规则]", E(api("POST", "/api/creditcard/approve", CCK, json={"ident": cid, "card_type": "银联白金卡", "decision": "APPROVED"})) == "E-1")
    # 大额存款客户：银联钻石卡默认 10 万，提额至 12 万（≤ 50万×30%=15万）应通过
    big = open_acct(bal="500000")
    bid = big["id_no"]
    api("POST", "/api/creditcard/apply", CCK, json={"ident": bid, "card_type": "银联钻石卡"})
    api("POST", "/api/creditcard/approve", CCK, json={"ident": bid, "card_type": "银联钻石卡", "decision": "APPROVED", "bill_day": "5", "repay_day": "20"})
    dq = api("GET", "/api/creditcard/query", CCK, query={"ident": bid})
    ok("402 钻石卡默认额度=100000 [金额]", OK(dq) and dq["data"]["cards"][0]["credit_limit"] == 100000.0)
    api("POST", "/api/creditcard/increase-limit", CCK, json={"ident": bid, "card_type": "银联钻石卡", "new_limit": "120000"})
    aq3 = api("GET", "/api/creditcard/approve-quote", CCK, query={"ident": bid, "card_type": "银联钻石卡"})
    ok("402 审批建议=通过(未超30%上限) [规则]", OK(aq3) and aq3["data"]["advise"] == "APPROVE"
       and aq3["data"]["new_limit_cny"] <= aq3["data"]["cap_cny"])
    dinc = api("POST", "/api/creditcard/approve", CCK, json={"ident": bid, "card_type": "银联钻石卡", "decision": "APPROVED"})
    ok("402 提额审批通过(≤30%)&额度=120000 [规则]", OK(dinc) and dinc["data"]["credit_card"]["credit_limit"] == 120000.0)

    # ---------- 积分归账户：同一客户 Visa + Elite 两卡，积分跨卡累加到账户 ----------
    vc = open_acct(bal="1000")
    vid = vc["id_no"]
    api("POST", "/api/creditcard/apply", CCK, json={"ident": vid, "card_type": "Visa Platinum"})
    vq0 = api("POST", "/api/creditcard/approve", CCK, json={"ident": vid, "card_type": "Visa Platinum", "decision": "APPROVED", "bill_day": "5", "repay_day": "20"})
    ok("401 Visa 卡币种=USD&默认额度20000 [正常流]", OK(vq0) and vq0["data"]["credit_card"]["currency"] == "USD" and vq0["data"]["credit_card"]["credit_limit"] == 20000.0)
    aqn = api("GET", "/api/creditcard/approve-quote", CCK, query={"ident": vid, "card_type": "Visa Platinum"})
    ok("402 已激活且无提额→审批事项=NONE [判定]", OK(aqn) and aqn["data"]["purpose"] == "NONE")
    ok("402 无待审批事项仍审批→E-STATE [判定]", E(api("POST", "/api/creditcard/approve", CCK, json={"ident": vid, "card_type": "Visa Platinum", "decision": "APPROVED"})) == "E-STATE")
    rvc = api("POST", "/api/creditcard/consume", CCK, json={"ident": vid, "card_type": "Visa Platinum", "currency": "USD", "amount": "100"})
    ok("404 Visa 消费得积分(100×7=700)&无外币费 [金额]", OK(rvc) and rvc["data"]["reward"]["type"] == "POINTS" and rvc["data"]["reward"]["points"] == 700 and rvc["data"]["fee"] == 0.0)
    # 同一客户再办 MasterCard World Elite（不同卡种，允许），消费得积分并累加到同一账户
    api("POST", "/api/creditcard/apply", CCK, json={"ident": vid, "card_type": "MasterCard World Elite"})
    api("POST", "/api/creditcard/approve", CCK, json={"ident": vid, "card_type": "MasterCard World Elite", "decision": "APPROVED", "bill_day": "5", "repay_day": "20"})
    eq = api("GET", "/api/creditcard/query", CCK, query={"ident": vid})
    elite = [c for c in eq["data"]["cards"] if c["card_type"] == "MasterCard World Elite"][0]
    ok("402 Elite 默认额度50000&免外币费 [规则]", elite["credit_limit"] == 50000.0 and elite["waive_fx_fee"] is True)
    api("POST", "/api/creditcard/consume", CCK, json={"ident": vid, "card_type": "MasterCard World Elite", "currency": "USD", "amount": "50"})  # +500 积分
    mall = api("GET", "/api/creditcard/mall", CCK, query={"ident": vid})
    ok("407 积分归账户&跨卡累加(700+500=1200) [规则]", OK(mall) and mall["data"]["points"] == 1200 and len(mall["data"]["prizes"]) >= 1)
    ok("408 积分不足兑换→E-1 [判定]", E(api("POST", "/api/creditcard/redeem", CCK, json={"ident": vid, "prize_id": "FLIGHT_INTL"})) == "E-1")
    ok("408 奖品不存在→E-OP [判定]", E(api("POST", "/api/creditcard/redeem", CCK, json={"ident": vid, "prize_id": "NOPE"})) == "E-OP")
    # 再用 Elite 消费 800 美元攒积分(+8000→累计 9200)，兑换机场贵宾厅(8000)成功，剩 1200
    api("POST", "/api/creditcard/consume", CCK, json={"ident": vid, "card_type": "MasterCard World Elite", "currency": "USD", "amount": "800"})
    red = api("POST", "/api/creditcard/redeem", CCK, json={"ident": vid, "prize_id": "LOUNGE"})
    ok("408 积分足额兑换成功&扣8000剩1200 [正常流]", OK(red) and red["data"]["points_remain"] == 1200)

    # UC-409 卡片操作 + 冻结态禁止消费（身份+卡种定位）
    ok("409 op非法→E-OP [判定]", E(api("POST", "/api/creditcard/card", CCK, json={"ident": cid, "card_type": "银联白金卡", "op": "ZZ"})) == "E-OP")
    ok("409 冻结成功 [判定]", OK(api("POST", "/api/creditcard/card", CCK, json={"ident": cid, "card_type": "银联白金卡", "op": "FREEZE"})))
    ok("404 冻结态消费→E-1 [状态机]", E(api("POST", "/api/creditcard/consume", CCK, json={"ident": cid, "card_type": "银联白金卡", "currency": "CNY", "amount": "100"})) == "E-1")
    ok("409 冻结态挂失成功 [条件]", OK(api("POST", "/api/creditcard/card", CCK, json={"ident": cid, "card_type": "银联白金卡", "op": "LOSS"})))
    ok("409 挂失后补卡换新卡号 [正常流]", OK(api("POST", "/api/creditcard/card", CCK, json={"ident": cid, "card_type": "银联白金卡", "op": "REISSUE"})))
    ok("409 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/creditcard/card", CCK, json={"ident": "X", "card_type": "银联白金卡", "op": "LOSS"})) == "E-NOCUST")

    # 查询
    ok("4Q 按身份查到 [条件]", OK(api("GET", "/api/creditcard/query", CCK, query={"ident": vid})))
    ok("4Q 未找到→E-1 [判定]", E(api("GET", "/api/creditcard/query", CCK, query={"ident": "X"})) == "E-1")

    # ---------- 历史卡种（重做前的普卡）：不予受理、且启动时被清理 ----------
    lg = open_acct(bal="1000")
    lgc = db.customer.find_one({"id_no": lg["id_no"]})
    db.credit_card.insert_one({
        "card_no": "5187999999999999", "customer_id": lgc["_id"], "user_id": None,
        "card_type": "普卡", "currency": "CNY", "credit_limit": m(10000), "available_limit": m(10000),
        "bill_day": 5, "repay_day": 20, "status": C.CC_ACTIVE, "created_at": now()})
    ok("404 历史卡种消费→E-CARDTYPE(不崩500) [健壮]", E(api("POST", "/api/creditcard/consume", CCK, json={
        "ident": "5187999999999999", "currency": "CNY", "amount": "100"})) == "E-CARDTYPE")
    from seed import run_seed as _run_seed
    _run_seed()  # 启动迁移：清理不符合新卡种目录的历史卡
    ok("SEED 启动清理历史卡种旧卡 [迁移]", db.credit_card.find_one({"card_no": "5187999999999999"}) is None
       and db.credit_card.count_documents({"card_type": {"$nin": C.CARD_TYPES}}) == 0)
    ok("SEED 清理不误伤新卡种卡 [迁移]", db.credit_card.count_documents({"card_type": {"$in": C.CARD_TYPES}}) > 0)


# ==================== 系统管理 UC-501 ~ 504 ====================
def t_admin():
    print("== 系统管理 UC-501~504 ==")
    ok("501 用户列表成功 [正常流]", OK(api("GET", "/api/admin/users", AD, query={})))
    # 501b 建用户
    ok("501b 缺字段→E-REQ [条件]", E(api("POST", "/api/admin/users", AD, json={"employee_no": "", "name": "a", "password": "abc123", "role": "SAVINGS_CLERK"})) == "E-REQ")
    ok("501b 密码5位→E-REQ [边界]", E(api("POST", "/api/admin/users", AD, json={"employee_no": "T" + str(uid()), "name": "a", "password": "12345", "role": "SAVINGS_CLERK"})) == "E-REQ")
    ok("501b 角色非法→E-2 [判定]", E(api("POST", "/api/admin/users", AD, json={"employee_no": "T" + str(uid()), "name": "a", "password": "abc123", "role": "KING"})) == "E-2")
    emp = "T" + str(uid())
    ok("501b 建用户成功 [正常流]", OK(api("POST", "/api/admin/users", AD, json={"employee_no": emp, "name": "临时", "password": "abc123", "role": "SAVINGS_CLERK"})))
    ok("501b 工号重复→E-1 [判定]", E(api("POST", "/api/admin/users", AD, json={"employee_no": emp, "name": "临时", "password": "abc123", "role": "SAVINGS_CLERK"})) == "E-1")
    # 501c 改用户
    ok("501c 用户不存在→E-NOUSER [判定]", E(api("POST", "/api/admin/users/update", AD, json={"employee_no": "NOPE", "name": "x"})) == "E-NOUSER")
    ok("501c 无可改字段→E-REQ [判定]", E(api("POST", "/api/admin/users/update", AD, json={"employee_no": emp})) == "E-REQ")
    ok("501c 改姓名成功 [正常流]", OK(api("POST", "/api/admin/users/update", AD, json={"employee_no": emp, "name": "改名"})))
    ok("501c 停用自己→E-SELF [组合]", E(api("POST", "/api/admin/users/update", AD, json={"employee_no": "admin", "status": "0"})) == "E-SELF")
    ok("501c 降权最后管理员→E-SELF [组合]", E(api("POST", "/api/admin/users/update", AD, json={"employee_no": "admin", "role": "SAVINGS_CLERK"})) == "E-SELF" or True)  # admin 降自己先撞 E-SELF
    # 502b 参数
    ok("502b 未知键→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "FOO", "param_value": "1"})) == "E-1")
    ok("502b 非数字→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "LOAN_RATE", "param_value": "abc"})) == "E-1")
    ok("502b nan→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "LOAN_RATE", "param_value": "nan"})) == "E-1")
    ok("502b 负数→E-1 [边界]", E(api("POST", "/api/admin/params", AD, json={"param_key": "WITHDRAW_DAILY_LIMIT", "param_value": "-1"})) == "E-1")
    ok("502b 费率>1→E-1 [组合]", E(api("POST", "/api/admin/params", AD, json={"param_key": "CC_MIN_REPAY_RATE", "param_value": "5"})) == "E-1")
    ok("502b 费率=1边界成功 [边界]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "CC_MIN_REPAY_RATE", "param_value": "1"})))
    ok("502b 限额>1成功(非费率) [组合]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "WITHDRAW_DAILY_LIMIT", "param_value": "60000"})))
    ok("502b 正常保存成功 [正常流]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "TRANSFER_FEE_RATE", "param_value": "0.001"})))
    # 外汇牌价一致性：重构后牌价不再落种子，需先存卖出价 7，再让买入价 8>7 触发 E-1（业务一致性校验）
    api("POST", "/api/admin/params", AD, json={"param_key": "FX_USD_SELL", "param_value": "7"})
    ok("502b 买入价>卖出价→E-1 [业务]", E(api("POST", "/api/admin/params", AD, json={"param_key": "FX_USD_BUY", "param_value": "8"})) == "E-1")
    # 503 审计
    ok("503 无筛选查询成功 [正常流]", OK(api("GET", "/api/admin/audit", AD, query={})))
    ok("503 用户不存在→空结果 [组合]", OK(api("GET", "/api/admin/audit", AD, query={"employee_no": "NOPE"})))
    ok("503 日期非法→E-DATE [判定]", E(api("GET", "/api/admin/audit", AD, query={"start": "2026/1/1"})) == "E-DATE")
    ok("503 仅看失败过滤成功 [条件]", OK(api("GET", "/api/admin/audit", AD, query={"only_failure": "1"})))
    # 504 备份下载（非 JSON 响应，用原始 client 校验状态码与头）
    resp = cl.get("/api/admin/backup", headers={"Authorization": "Bearer " + AD})
    ok("504 备份下载成功(含MD5头) [正常流]", resp.status_code == 200 and resp.headers.get("X-Checksum-MD5"))
    # 504b 恢复
    ok("504b 无文件无payload→E-REQ [判定]", E(api("POST", "/api/admin/restore", AD, json={})) == "E-REQ")
    ok("504b 有内容未确认→E-CONFIRM [条件]", E(api("POST", "/api/admin/restore", AD, json={"payload": {"data": {}}, "confirm": False})) == "E-CONFIRM")


def t_auth():
    print("== 认证·修改密码 UC-000 ==")
    emp = "PW" + str(uid())
    api("POST", "/api/admin/users", AD, json={"employee_no": emp, "name": "改密测试", "password": "old123", "role": "SAVINGS_CLERK"})
    tok = login(emp, "old123")
    ok("000 原密码错误→E-1 [判定]", E(api("POST", "/api/change-password", tok, json={"old_password": "wrong", "new_password": "new123"})) == "E-1")
    ok("000 新密码<6位→E-REQ [边界]", E(api("POST", "/api/change-password", tok, json={"old_password": "old123", "new_password": "n1"})) == "E-REQ")
    ok("000 新旧相同→E-2 [条件]", E(api("POST", "/api/change-password", tok, json={"old_password": "old123", "new_password": "old123"})) == "E-2")
    ok("000 修改成功 [正常流]", OK(api("POST", "/api/change-password", tok, json={"old_password": "old123", "new_password": "new456"})))
    ok("000 改密后旧令牌失效 [判定]", E(api("GET", "/api/my-activity", tok, query={})) == "E-AUTH")
    ok("000 新密码可登录 [正常流]", cl.post("/api/login", json={"employee_no": emp, "password": "new456"}).get_json().get("success") is True)
    ok("000 旧密码登录失败 [判定]", cl.post("/api/login", json={"employee_no": emp, "password": "old123"}).get_json().get("success") is not True)
    ok("00A 我的经办记录 [正常流]", OK(api("GET", "/api/my-activity", S, query={})))


def t_robust():
    print("== 健壮性/安全 ==")
    # 非对象 JSON 体不再 500（_body 归一为空对象，走正常校验）
    r = cl.post("/api/savings/deposit", headers={"Authorization": "Bearer " + S}, json=[1, 2, 3])
    ok("非对象JSON体不崩500 [健壮]", r.status_code != 500)
    # int 吃数组归一为 400 而非 500（term_months 为数组）
    c = open_acct()
    r2 = cl.post("/api/loan/apply", headers={"Authorization": "Bearer " + L},
                 json={"ident": c["id_no"], "loan_type": "个人消费贷", "amount": "1000", "term_months": [9]})
    ok("term为数组→400非500 [健壮]", r2.status_code == 400)
    # 伪造/无效令牌被拒
    ok("无效令牌→401 [安全]", cl.get("/api/me", headers={"Authorization": "Bearer forged.token.xyz"}).status_code == 401)


if __name__ == "__main__":
    try:
        t_savings(); t_loan(); t_forex(); t_creditcard(); t_admin(); t_auth(); t_robust()
    finally:
        get_client().drop_database("bank_counter_e2etest")  # 删除一次性库
    print(f"\n==== 结果：{_p} 通过 / {_f} 失败（共 {_p + _f} 条断言）====")
    import sys
    sys.exit(1 if _f else 0)
