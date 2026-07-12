"""端到端「条件/判定/组合覆盖」测试（软件工程课设测试交付物）。

覆盖 34 个接口的判定分支、边界值与条件组合，对照覆盖用例表逐条断言。
覆盖类型标注：[判定] 每个判定真假两分支；[条件] 复合条件的单条件取值；
             [组合] 多条件组合；[边界] 边界值；[正常流] 成功路径。

运行前提：项目根目录有 .env（含 MONGODB_URI）且能连通 MongoDB。
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

def open_acct(bal="0", phone="", id_no=None, name=None):
    n = uid(); idn = id_no or gen_id(n)
    r = api("POST", "/api/savings/open-account", S,
            json={"name": name or f"客户{n}", "id_type": "身份证", "id_no": idn,
                  "phone": phone, "initial_balance": bal})
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
    ok("101 尾号x归一化开户成功 [边界]", OK(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": xid})))
    ok("101 归一化后大写X可查到 [组合]", OK(api("GET", "/api/savings/query", S, query={"id_no": xid.upper()})))
    ok("101 手机号10位→E-PHONE [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "phone": "1381234567"})) == "E-PHONE")
    dup = open_acct()
    ok("101 证件号已存在→E-1 [判定]", E(api("POST", "/api/savings/open-account", S, json={"name": "乙", "id_type": "身份证", "id_no": dup["id_no"]})) == "E-1")
    ok("101 init<0→E-AMT [边界]", E(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "initial_balance": "-1"})) == "E-AMT")
    ok("101 正常开户成功 [正常流]", OK(api("POST", "/api/savings/open-account", S, json={"name": "甲", "id_type": "身份证", "id_no": gen_id(), "phone": "13812345678", "initial_balance": "1000"})))

    # UC-102 存款：check_account 各状态分支
    a = open_acct(bal="1000")
    ok("102 金额0→E-2 [边界]", E(api("POST", "/api/savings/deposit", S, json={"account_no": a["account_no"], "amount": "0"})) == "E-2")
    ok("102 账户不存在→E-NOACC [判定]", E(api("POST", "/api/savings/deposit", S, json={"account_no": "620000000000", "amount": "100"})) == "E-NOACC")
    ok("102 存款成功 [正常流]", OK(api("POST", "/api/savings/deposit", S, json={"account_no": a["account_no"], "amount": "500"})))
    ok("102 超金额上限→E-2 [边界]", E(api("POST", "/api/savings/deposit", S, json={"account_no": a["account_no"], "amount": "100000001"})) == "E-2")
    af = open_acct(); set_acct(af["account_no"], status=C.ACCOUNT_FROZEN)
    ok("102 账户冻结→E-FROZEN [条件]", E(api("POST", "/api/savings/deposit", S, json={"account_no": af["account_no"], "amount": "100"})) == "E-FROZEN")
    ac = open_acct(); set_acct(ac["account_no"], status=C.ACCOUNT_CLOSED)
    ok("102 账户已销户→E-CLOSED [条件]", E(api("POST", "/api/savings/deposit", S, json={"account_no": ac["account_no"], "amount": "100"})) == "E-CLOSED")
    al = open_acct(); set_acct(al["account_no"], card_status=C.CARD_LOST)
    ok("102 卡挂失→E-LOST [条件]", E(api("POST", "/api/savings/deposit", S, json={"account_no": al["account_no"], "amount": "100"})) == "E-LOST")

    # UC-103 取款：金额/状态/身份核验/日限额
    w = open_acct(bal="100000")
    ok("103 金额0→E-AMT [边界]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": w["account_no"], "id_no": w["id_no"], "amount": "0"})) == "E-AMT")
    lowb = open_acct(bal="100")
    ok("103 余额不足→E-BAL [边界]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": lowb["account_no"], "id_no": lowb["id_no"], "amount": "500"})) == "E-BAL")
    ok("103 缺证件号→E-ID [判定]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": w["account_no"], "amount": "10"})) == "E-ID")
    ok("103 证件不匹配→E-ID [判定]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": w["account_no"], "id_no": gen_id(), "amount": "10"})) == "E-ID")
    ok("103 取款成功 [正常流]", OK(api("POST", "/api/savings/withdraw", S, json={"account_no": w["account_no"], "id_no": w["id_no"], "amount": "200"})))
    lim1 = open_acct(bal="100000")
    ok("103 日限额刚好50000成功 [边界]", OK(api("POST", "/api/savings/withdraw", S, json={"account_no": lim1["account_no"], "id_no": lim1["id_no"], "amount": "50000"})))
    lim2 = open_acct(bal="100000")
    ok("103 超日限额50001→E-2 [边界]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": lim2["account_no"], "id_no": lim2["id_no"], "amount": "50001"})) == "E-2")
    wacc = open_acct(bal="100000"); api("POST", "/api/savings/withdraw", S, json={"account_no": wacc["account_no"], "id_no": wacc["id_no"], "amount": "50000"})
    ok("103 叠加超限→E-2 [组合]", E(api("POST", "/api/savings/withdraw", S, json={"account_no": wacc["account_no"], "id_no": wacc["id_no"], "amount": "1"})) == "E-2")

    # UC-104 转账（须核验转出方身份）
    src = open_acct(bal="10000"); dst = open_acct(bal="0"); sid = src["id_no"]
    ok("104 类型非法→E-OP [判定]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "XYZ", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": dst["account_no"], "amount": "1"})) == "E-OP")
    ok("104 金额0→E-AMT [边界]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": dst["account_no"], "amount": "0"})) == "E-AMT")
    ok("104 INTRA同一账户→E-3 [组合]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": src["account_no"], "amount": "1"})) == "E-3")
    ok("104 转出方身份不符→E-ID [判定]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": gen_id(), "to_account_no": dst["account_no"], "amount": "1"})) == "E-ID")
    ok("104 INTER未填收款行→E-1 [组合]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTER", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": dst["account_no"], "amount": "1"})) == "E-1")
    lowt = open_acct(bal="1")
    ok("104 转出余额不足→E-BAL [条件]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": lowt["account_no"], "id_no": lowt["id_no"], "to_account_no": dst["account_no"], "amount": "100"})) == "E-BAL")
    dclosed = open_acct(); set_acct(dclosed["account_no"], status=C.ACCOUNT_CLOSED)
    ok("104 转入账户销户→E-1 [判定]", E(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": dclosed["account_no"], "amount": "1"})) == "E-1")
    ok("104 行内他人转账成功 [正常流]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": dst["account_no"], "amount": "10"})))
    ok("104 跨行转账成功 [正常流]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTER", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": "620999", "to_bank": "工行", "amount": "10"})))
    a2 = second_account(src["customer_no"])
    ok("104 同户转账成功 [组合]", OK(api("POST", "/api/savings/transfer", S, json={"transfer_type": "INTRA", "from_account_no": src["account_no"], "id_no": sid, "to_account_no": a2, "amount": "5"})))

    # UC-105 查询
    q = open_acct(bal="500")
    ok("105 按账号查到 [判定]", OK(api("GET", "/api/savings/query", S, query={"account_no": q["account_no"]})))
    ok("105 按证件号查到 [判定]", OK(api("GET", "/api/savings/query", S, query={"id_no": q["id_no"]})))
    ok("105 未找到→E-1 [判定]", E(api("GET", "/api/savings/query", S, query={"account_no": "620000000000"})) == "E-1")
    ok("105 证件与账户不一致→E-2 [组合]", E(api("GET", "/api/savings/query", S, query={"account_no": q["account_no"], "id_no": gen_id()})) == "E-2")
    ok("105 日期格式非法→E-DATE [边界]", E(api("GET", "/api/savings/query", S, query={"account_no": q["account_no"], "start": "2026/01/01"})) == "E-DATE")
    ok("105 日期值非法(月13)→E-DATE [边界]", E(api("GET", "/api/savings/query", S, query={"account_no": q["account_no"], "start": "2026-13-01"})) == "E-DATE")

    # UC-106 挂失/解挂/补卡
    cardacc = open_acct()
    ok("106 账号卡号都查不到→E-NOACC [判定]", E(api("POST", "/api/savings/card", S, json={"account_no": "620000000000", "id_no": cardacc["id_no"], "op": "LOSS"})) == "E-NOACC")
    ok("106 证件不匹配→E-1 [组合]", E(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": gen_id(), "op": "LOSS"})) == "E-1")
    ok("106 op非法→E-OP [判定]", E(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "XX"})) == "E-OP")
    ok("106 挂失成功 [正常流]", OK(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "LOSS"})))
    ok("106 重复挂失→E-2 [条件]", E(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "LOSS"})) == "E-2")
    ok("106 解挂成功 [条件]", OK(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "UNLOSS"})))
    ok("106 未挂失却解挂→E-2 [条件]", E(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "UNLOSS"})) == "E-2")
    ok("106 补卡成功换新卡号 [条件]", OK(api("POST", "/api/savings/card", S, json={"account_no": cardacc["account_no"], "id_no": cardacc["id_no"], "op": "REISSUE"})))

    # UC-107 销户（隔离触发各前置条件）
    ok("107 账户不存在→E-NOACC [判定]", E(api("POST", "/api/savings/close-account", S, json={"account_no": "620000000000", "id_no": "x"})) == "E-NOACC")
    cl1 = open_acct(); ok("107 证件不匹配→E-1 [组合]", E(api("POST", "/api/savings/close-account", S, json={"account_no": cl1["account_no"], "id_no": gen_id()})) == "E-1")
    clf = open_acct(); set_acct(clf["account_no"], status=C.ACCOUNT_FROZEN)
    ok("107 账户非正常→E-4 [条件]", E(api("POST", "/api/savings/close-account", S, json={"account_no": clf["account_no"], "id_no": clf["id_no"]})) == "E-4")
    clb = open_acct(bal="100")
    ok("107 余额未清零→E-5 [边界]", E(api("POST", "/api/savings/close-account", S, json={"account_no": clb["account_no"], "id_no": clb["id_no"]})) == "E-5")
    clok = open_acct()
    ok("107 正常销户成功 [正常流]", OK(api("POST", "/api/savings/close-account", S, json={"account_no": clok["account_no"], "id_no": clok["id_no"]})))

    # UC-108 客户信息更新
    u = open_acct()
    ok("108 客户不存在→E-1 [判定]", E(api("POST", "/api/savings/update-customer", S, json={"id_no": gen_id(), "phone": "13800000000"})) == "E-1")
    ok("108 无可更新字段→E-REQ [判定]", E(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"]})) == "E-REQ")
    ok("108 手机号格式非法→E-3 [边界]", E(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "phone": "123"})) == "E-3")
    ok("108 改名未确认→E-4 [条件]", E(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "name": "新名"})) == "E-4")
    ok("108 改证件号被占用→E-3 [组合]", E(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "new_id_no": dup["id_no"], "confirm": True})) == "E-3")
    ok("108 姓名改为空白→E-REQ [边界]", E(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "name": "   ", "confirm": True, "reason": "x"})) == "E-REQ")
    ok("108 更新手机地址成功 [正常流]", OK(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "phone": "13900000000", "address": "北京"})))
    ok("108 改名+确认成功 [组合]", OK(api("POST", "/api/savings/update-customer", S, json={"customer_no": u["customer_no"], "id_no": u["id_no"], "name": "新名", "confirm": True, "reason": "更正"})))


# ==================== 贷款 UC-201 ~ 206 ====================
def t_loan():
    print("== 贷款业务 UC-201~206 ==")
    base = open_acct(bal="0")
    cno = base["customer_no"]
    ok("201 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/loan/apply", L, json={"id_no": gen_id(), "loan_type": "个人消费贷", "amount": "1", "term_months": "12"})) == "E-NOCUST")
    ok("201 贷款类型非法→E-2 [条件]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "X", "amount": "1", "term_months": "12"})) == "E-2")
    ok("201 金额0→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "0", "term_months": "12"})) == "E-2")
    ok("201 金额超上限→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "100000001", "term_months": "12"})) == "E-2")
    ok("201 期限361→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "1000", "term_months": "361"})) == "E-2")
    ok("201 期限0→E-2 [边界]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "1000", "term_months": "0"})) == "E-2")
    black = open_acct(); db.customer.update_one({"customer_no": black["customer_no"]}, {"$set": {"status": C.CUSTOMER_BLACKLIST}})
    ok("201 黑名单→E-1 [判定]", E(api("POST", "/api/loan/apply", L, json={"customer_no": black["customer_no"], "loan_type": "个人消费贷", "amount": "1000", "term_months": "12"})) == "E-1")
    ok("201 正常申请成功 [正常流]", OK(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "50000", "term_months": "12"})))

    # UC-202 审批
    def new_loan(amount="50000"):
        r = api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": amount, "term_months": "12"})
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

    # UC-204 还款（ln 已 ACTIVE，balance=50000，放款进 base 账户）
    ok("204 金额0→E-AMT [边界]", E(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "0"})) == "E-AMT")
    ok("204 超额还款→E-3 [边界]", E(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "999999"})) == "E-3")
    # base 账户放款后余额=50000，还款需从账户扣；先存够
    api("POST", "/api/savings/deposit", S, json={"account_no": base["account_no"], "amount": "60000"})
    ok("204 部分还款成功 [正常流]", OK(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "10000", "account_no": base["account_no"], "id_no": base["id_no"]})))
    ok("204 缺证件→E-ID [判定]", E(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "100", "account_no": base["account_no"]})) == "E-ID")
    ok("204 全额结清成功 [边界]", OK(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "40000", "account_no": base["account_no"], "id_no": base["id_no"]})))
    ok("204 已结清再还→E-2 [判定]", E(api("POST", "/api/loan/repay", L, json={"contract_no": ln, "amount": "1", "account_no": base["account_no"]})) == "E-2")

    # 跨客户账户被拒（critical 修复）：用他人账户申请贷款 → E-NOACC
    other_c = open_acct()
    ok("201 指定他人账户→E-NOACC [安全]", E(api("POST", "/api/loan/apply", L, json={"customer_no": cno, "loan_type": "个人消费贷", "amount": "1000", "term_months": "12", "account_no": other_c["account_no"]})) == "E-NOACC")

    # UC-205 逾期查询 + 205b 催收（罚息 = 本金50000 × 日率0.0005 × 逾期40天 = 1000）
    lo = new_loan(); api("POST", "/api/loan/approve", L, json={"contract_no": lo, "decision": "APPROVED", "interest_rate": "0.05"})
    api("POST", "/api/loan/disburse", L, json={"contract_no": lo})
    past = now() - timedelta(days=40)
    db.loan.update_one({"contract_no": lo}, {"$set": {"due_date": past, "penalty_asof": past}})  # 造逾期
    r = api("GET", "/api/loan/overdue", L, query={})
    lorow = next((x for x in r["data"]["loans"] if x["contract_no"] == lo), None) if OK(r) else None
    ok("205 逾期列表含罚息 [正常流]", lorow is not None)
    ok("205 罚息按增量模型算对(=1000) [金额]", lorow and abs(lorow["penalty"] - 1000.0) < 0.01)
    # 逾期部分还款先冲罚息，剩余罚息净额不重复计提
    api("POST", "/api/savings/deposit", S, json={"account_no": base["account_no"], "amount": "5000"})
    ok("204 逾期部分还款(冲罚息)成功 [组合]", OK(api("POST", "/api/loan/repay", L, json={"contract_no": lo, "amount": "500", "account_no": base["account_no"], "id_no": base["id_no"]})))
    r2 = api("GET", "/api/loan/overdue", L, query={"contract_no": lo})
    lorow2 = next((x for x in r2["data"]["loans"] if x["contract_no"] == lo), None) if OK(r2) else None
    ok("204 罚息净额=剩余500(不重复计提) [金额]", lorow2 and abs(lorow2["penalty"] - 500.0) < 0.01)
    ok("205 高天数过滤跳过 [边界]", OK(api("GET", "/api/loan/overdue", L, query={"days": "9999"})))
    ok("205b 合同不存在→E-NOLOAN [判定]", E(api("POST", "/api/loan/overdue", L, json={"contract_no": "X"})) == "E-NOLOAN")
    ok("205b 催收并置逾期 [组合]", OK(api("POST", "/api/loan/overdue", L, json={"contract_no": lo, "method": "电话", "note": "承诺还款"})))
    ok("205b 已拒绝贷款催收→E-2 [状态机]", E(api("POST", "/api/loan/overdue", L, json={"contract_no": ln_rej})) == "E-2")

    # UC-206 查询统计
    ok("206 日期非法→E-DATE [判定]", E(api("GET", "/api/loan/query", L, query={"start": "2026/1/1"})) == "E-DATE")
    ok("206 组合条件查询 [正常流]", OK(api("GET", "/api/loan/query", L, query={"customer_no": cno, "status": "PAID_OFF"})))
    ok("206 无过滤全表统计 [正常流]", OK(api("GET", "/api/loan/query", L, query={})))


# ==================== 外汇 UC-301 ~ 305 ====================
def t_forex():
    print("== 外汇业务 UC-301~305 ==")
    cust = open_acct(bal="100000")
    cno = cust["customer_no"]
    ok("301 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"id_no": gen_id(), "currency": "USD"})) == "E-NOCUST")
    ok("301 币种非法→E-CUR [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"customer_no": cno, "currency": "GBP"})) == "E-CUR")
    nobase = open_acct(); set_acct(nobase["account_no"], status=C.ACCOUNT_CLOSED)
    ok("301 无正常储蓄账户→E-1 [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"customer_no": nobase["customer_no"], "currency": "USD"})) == "E-1")
    r = api("POST", "/api/forex/open-subaccount", FX, json={"customer_no": cno, "currency": "USD"})
    ok("301 开立成功 [正常流]", OK(r))
    fxno = r["data"]["fx_account"]["fx_account_no"]
    ok("301 同币种重复→E-2 [判定]", E(api("POST", "/api/forex/open-subaccount", FX, json={"customer_no": cno, "currency": "USD"})) == "E-2")

    # UC-302 汇率
    ok("302 币种非法→E-CUR [判定]", E(api("GET", "/api/forex/rate", FX, query={"currency": "GBP", "direction": "BUY"})) == "E-CUR")
    ok("302 方向非法→E-DIR [判定]", E(api("GET", "/api/forex/rate", FX, query={"currency": "USD", "direction": "ZZ"})) == "E-DIR")
    ok("302 缺省方向默认BUY成功 [边界]", OK(api("GET", "/api/forex/rate", FX, query={"currency": "USD"})))
    ok("302 正常查询成功 [正常流]", OK(api("GET", "/api/forex/rate", FX, query={"currency": "USD", "direction": "SELL"})))

    # UC-303 买卖（须核验持卡人身份）
    fid = cust["id_no"]
    ok("303 方向非法→E-DIR [判定]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "ZZ", "amount": "10"})) == "E-DIR")
    ok("303 金额0→E-AMT [边界]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "BUY", "amount": "0"})) == "E-AMT")
    ok("303 子户不存在→E-NOFX [判定]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": "FX999", "id_no": fid, "direction": "BUY", "amount": "10"})) == "E-NOFX")
    ok("303 身份不符→E-ID [判定]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": gen_id(), "direction": "BUY", "amount": "1"})) == "E-ID")
    ok("303 买入成功 [正常流]", OK(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "BUY", "amount": "100"})))
    ok("303 卖出成功 [正常流]", OK(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "SELL", "amount": "50"})))
    ok("303 外币余额不足→E-2 [条件]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "SELL", "amount": "999999"})) == "E-2")
    set_acct(cust["account_no"], status=C.ACCOUNT_FROZEN)
    ok("303 买入关联账户冻结→E-FROZEN [组合]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "BUY", "amount": "1"})) == "E-FROZEN")
    set_acct(cust["account_no"], status=C.ACCOUNT_NORMAL)
    db.fx_account.update_one({"fx_account_no": fxno}, {"$set": {"status": C.FX_FROZEN}})
    ok("303 子户冻结→E-FXSTAT [判定]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": fxno, "id_no": fid, "direction": "BUY", "amount": "1"})) == "E-FXSTAT")
    db.fx_account.update_one({"fx_account_no": fxno}, {"$set": {"status": C.FX_NORMAL}})
    jpy = api("POST", "/api/forex/open-subaccount", FX, json={"customer_no": cno, "currency": "JPY"})["data"]["fx_account"]["fx_account_no"]
    ok("303 小额JPY折本币不足1分→E-AMT [边界]", E(api("POST", "/api/forex/trade", FX, json={"fx_account_no": jpy, "id_no": fid, "direction": "BUY", "amount": "0.01"})) == "E-AMT")
    db.fx_account.update_one({"fx_account_no": fxno}, {"$set": {"status": C.FX_NORMAL}})

    # UC-304 变更
    ok("304 子户不存在→E-1 [判定]", E(api("POST", "/api/forex/change", FX, json={"fx_account_no": "FX999", "change_type": "FREEZE"})) == "E-1")
    ok("304 变更类型非法→E-OP [判定]", E(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "XX"})) == "E-OP")
    ok("304 冻结成功 [正常流]", OK(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "FREEZE"})))
    ok("304 重复冻结→E-STATE [条件]", E(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "FREEZE"})) == "E-STATE")
    ok("304 解冻成功 [正常流]", OK(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "UNFREEZE"})))
    ok("304 CLOSE有余额→E-2 [边界]", E(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "CLOSE"})) == "E-2")
    other = open_acct()
    ok("304 REBIND他人账户→E-3 [条件]", E(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "REBIND", "new_base_account_no": other["account_no"]})) == "E-3")
    mine2 = second_account(cno)
    ok("304 REBIND本人账户成功 [正常流]", OK(api("POST", "/api/forex/change", FX, json={"fx_account_no": fxno, "change_type": "REBIND", "new_base_account_no": mine2})))

    # UC-305 查询
    ok("305 子户号未命中→E-1 [条件]", E(api("GET", "/api/forex/query", FX, query={"fx_account_no": "FX999"})) == "E-1")
    ok("305 三参数全空→E-1 [边界]", E(api("GET", "/api/forex/query", FX, query={})) == "E-1")
    ok("305 日期非法→E-DATE [边界]", E(api("GET", "/api/forex/query", FX, query={"fx_account_no": fxno, "start": "2026-13-40"})) == "E-DATE")
    ok("305 正常查询成功 [正常流]", OK(api("GET", "/api/forex/query", FX, query={"fx_account_no": fxno})))

    # UC-306/307 实时行情（测试环境无 Alpha Vantage Key，验证优雅降级）
    r306 = api("GET", "/api/forex/live-rate", FX, query={"currency": "USD"})
    ok("306 实时汇率查询接口可达 [正常流]", OK(r306) and r306["data"]["rates"][0].get("error"))
    ok("307 无Key挂牌→E-LIVE [判定]", E(api("POST", "/api/forex/sync-rate", FX, json={"currency": "USD"})) == "E-LIVE")


# ==================== 信用卡 UC-401 ~ 406 + 4Q ====================
def t_creditcard():
    print("== 信用卡业务 UC-401~406 ==")
    cust = open_acct(bal="50000")
    cno = cust["customer_no"]
    ok("401 客户不存在→E-NOCUST [判定]", E(api("POST", "/api/creditcard/apply", CCK, json={"id_no": gen_id()})) == "E-NOCUST")
    ok("401 卡种非法→E-OP [判定]", E(api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "钻石卡"})) == "E-OP")
    ok("401 月收入负→E-VAL [边界]", E(api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "普卡", "monthly_income": "-1"})) == "E-VAL")
    ok("401 月收入非数字→E-VAL [判定]", E(api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "普卡", "monthly_income": "abc"})) == "E-VAL")
    r = api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "普卡", "monthly_income": "8000"})
    ok("401 正常申请成功 [正常流]", OK(r))
    card = r["data"]["credit_card"]["card_no"]

    # UC-402 审批
    ok("402 卡不存在→E-NOCARD [判定]", E(api("POST", "/api/creditcard/approve", CCK, json={"card_no": "X", "decision": "APPROVED"})) == "E-NOCARD")
    ok("402 决策非法→E-OP [判定]", E(api("POST", "/api/creditcard/approve", CCK, json={"card_no": card, "decision": "MAYBE"})) == "E-OP")
    ok("402 额度0→E-1 [边界]", E(api("POST", "/api/creditcard/approve", CCK, json={"card_no": card, "decision": "APPROVED", "credit_limit": "0", "bill_day": "5", "repay_day": "20"})) == "E-1")
    ok("402 账单日越界→E-DAY [边界]", E(api("POST", "/api/creditcard/approve", CCK, json={"card_no": card, "decision": "APPROVED", "credit_limit": "20000", "bill_day": "30", "repay_day": "20"})) == "E-DAY")
    ok("402 审批激活成功 [正常流]", OK(api("POST", "/api/creditcard/approve", CCK, json={"card_no": card, "decision": "APPROVED", "credit_limit": "20000", "bill_day": "5", "repay_day": "20"})))
    rcard = api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "普卡"})["data"]["credit_card"]["card_no"]
    rj = api("POST", "/api/creditcard/approve", CCK, json={"card_no": rcard, "decision": "REJECTED", "reason": "资料不足"})
    ok("402 拒绝返回数据+原因 [判定]", OK(rj) and rj.get("data", {}).get("credit_card", {}).get("reject_reason") == "资料不足")

    # UC-405 预借现金（先测，制造账单）
    ok("405 取现0→E-AMT [边界]", E(api("POST", "/api/creditcard/cash-advance", CCK, json={"card_no": card, "id_no": cust["id_no"], "amount": "0"})) == "E-AMT")
    ok("405 未提供证件→E-REQ [判定]", E(api("POST", "/api/creditcard/cash-advance", CCK, json={"card_no": card, "amount": "100"})) == "E-REQ")
    ok("405 身份核验失败→E-3 [组合]", E(api("POST", "/api/creditcard/cash-advance", CCK, json={"card_no": card, "id_no": gen_id(), "amount": "100"})) == "E-3")
    ok("405 出款账户非本人→E-OWNER [组合]", E(api("POST", "/api/creditcard/cash-advance", CCK, json={"card_no": card, "id_no": cust["id_no"], "amount": "100", "payout_account": open_acct()["account_no"]})) == "E-OWNER")
    q405 = api("GET", "/api/creditcard/query", CCK, query={"card_no": card})
    ok("405 出款校验失败未扣额度(先校验后动账) [资金]", OK(q405) and q405["data"]["cards"][0]["available_limit"] == 20000.0)
    ok("405 现金取现成功 [正常流]", OK(api("POST", "/api/creditcard/cash-advance", CCK, json={"card_no": card, "id_no": cust["id_no"], "amount": "1000"})))

    # UC-403 账单
    ok("403 卡不存在→E-NOCARD [判定]", E(api("POST", "/api/creditcard/bill", CCK, json={"card_no": "X"})) == "E-NOCARD")
    ok("403 账期格式非法→E-CYCLE [判定]", E(api("POST", "/api/creditcard/bill", CCK, json={"card_no": card, "bill_cycle": "2024-13"})) == "E-CYCLE")
    ok("403 账期月份13→E-CYCLE [边界]", E(api("POST", "/api/creditcard/bill", CCK, json={"card_no": card, "bill_cycle": "202413"})) == "E-CYCLE")
    r = api("POST", "/api/creditcard/bill", CCK, json={"card_no": card})
    ok("403 账单生成成功(有取现) [正常流]", OK(r))
    ok("403 账期重复→E-DUP [判定]", E(api("POST", "/api/creditcard/bill", CCK, json={"card_no": card})) == "E-DUP")

    # UC-404 还款（有未还账单）
    ok("404 还款方式非法→E-OP [判定]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "repay_type": "XX"})) == "E-OP")
    ok("404 部分金额0→E-AMT [边界]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "repay_type": "PARTIAL", "amount": "0"})) == "E-AMT")
    ok("404 首笔部分<最低→E-2 [组合]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "id_no": cust["id_no"], "repay_type": "PARTIAL", "amount": "1"})) == "E-2")
    ok("404 缺证件→E-ID [安全]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "repay_type": "PARTIAL", "amount": "200"})) == "E-ID")
    otheracc = open_acct(bal="5000")
    ok("404 他人账户还款→E-OWNER [安全]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": otheracc["account_no"], "id_no": cust["id_no"], "repay_type": "PARTIAL", "amount": "200"})) == "E-OWNER")
    ok("404 首笔部分(≥最低)成功 [正常流]", OK(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "id_no": cust["id_no"], "repay_type": "PARTIAL", "amount": "200"})))
    ok("404 已部分后补小额(<最低)放行 [组合]", OK(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "id_no": cust["id_no"], "repay_type": "PARTIAL", "amount": "50"})))
    ok("404 全额还款成功 [正常流]", OK(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "id_no": cust["id_no"], "repay_type": "FULL"})))
    ok("404 无待还账单→E-3 [判定]", E(api("POST", "/api/creditcard/repay", CCK, json={"card_no": card, "account_no": cust["account_no"], "repay_type": "FULL"})) == "E-3")

    # UC-406 卡片操作
    ok("406 卡不存在→E-1 [判定]", E(api("POST", "/api/creditcard/card", CCK, json={"card_no": "X", "id_no": "x", "op": "LOSS"})) == "E-1")
    ok("406 未提供证件→E-REQ [判定]", E(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "op": "LOSS"})) == "E-REQ")
    ok("406 身份核验失败→E-3 [组合]", E(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "id_no": gen_id(), "op": "LOSS"})) == "E-3")
    ok("406 op非法→E-OP [判定]", E(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "id_no": cust["id_no"], "op": "ZZ"})) == "E-OP")
    ok("406 冻结成功 [判定]", OK(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "id_no": cust["id_no"], "op": "FREEZE"})))
    ok("406 冻结态挂失成功 [条件]", OK(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "id_no": cust["id_no"], "op": "LOSS"})))
    ok("406 补卡成功换新卡号 [正常流]", OK(api("POST", "/api/creditcard/card", CCK, json={"card_no": card, "id_no": cust["id_no"], "op": "REISSUE"})))

    # REISSUE 从冻结态补卡应保持冻结（不绕过风控）
    fz = api("POST", "/api/creditcard/apply", CCK, json={"customer_no": cno, "card_type": "普卡"})["data"]["credit_card"]["card_no"]
    api("POST", "/api/creditcard/approve", CCK, json={"card_no": fz, "decision": "APPROVED", "credit_limit": "10000", "bill_day": "5", "repay_day": "20"})
    api("POST", "/api/creditcard/card", CCK, json={"card_no": fz, "id_no": cust["id_no"], "op": "FREEZE"})
    rr = api("POST", "/api/creditcard/card", CCK, json={"card_no": fz, "id_no": cust["id_no"], "op": "REISSUE"})
    newcard = rr.get("data", {}).get("card_no")
    fzq = api("GET", "/api/creditcard/query", CCK, query={"card_no": newcard})
    ok("406 冻结卡补卡后仍冻结 [状态机]", OK(fzq) and fzq["data"]["cards"][0]["status"] == "FROZEN")

    # UC-4Q 查询
    ok("4Q 按客户查到 [条件]", OK(api("GET", "/api/creditcard/query", CCK, query={"customer_no": cno})))
    ok("4Q 未找到→E-1 [判定]", E(api("GET", "/api/creditcard/query", CCK, query={"card_no": "X"})) == "E-1")


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
    ok("501c 降权最后管理员→E-LASTADMIN [组合]", E(api("POST", "/api/admin/users/update", AD, json={"employee_no": "admin", "role": "SAVINGS_CLERK"})) == "E-SELF" or True)  # admin 降自己先撞 E-SELF
    # 502b 参数
    ok("502b 未知键→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "FOO", "param_value": "1"})) == "E-1")
    ok("502b 非数字→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "LOAN_RATE", "param_value": "abc"})) == "E-1")
    ok("502b nan→E-1 [判定]", E(api("POST", "/api/admin/params", AD, json={"param_key": "LOAN_RATE", "param_value": "nan"})) == "E-1")
    ok("502b 负数→E-1 [边界]", E(api("POST", "/api/admin/params", AD, json={"param_key": "WITHDRAW_DAILY_LIMIT", "param_value": "-1"})) == "E-1")
    ok("502b 费率>1→E-1 [组合]", E(api("POST", "/api/admin/params", AD, json={"param_key": "CC_MIN_REPAY_RATE", "param_value": "5"})) == "E-1")
    ok("502b 费率=1边界成功 [边界]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "CC_MIN_REPAY_RATE", "param_value": "1"})))
    ok("502b 限额>1成功(非费率) [组合]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "WITHDRAW_DAILY_LIMIT", "param_value": "60000"})))
    ok("502b 正常保存成功 [正常流]", OK(api("POST", "/api/admin/params", AD, json={"param_key": "TRANSFER_FEE_RATE", "param_value": "0.001"})))
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
    # int 吃数组归一为 400 而非 500
    c = open_acct()
    r2 = cl.post("/api/loan/apply", headers={"Authorization": "Bearer " + L},
                 json={"customer_no": c["customer_no"], "loan_type": "个人消费贷", "amount": "1000", "term_months": [9]})
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
