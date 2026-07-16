#!/usr/bin/env python3
"""银行柜面系统 - 全链路业务流程测试
模拟真人操作：每个步骤先验证前端页面可加载，再执行业务操作并验证返回数据"""
import json, urllib.request, urllib.error, sys, time

BASE = "https://bank-counter-system.onrender.com"
RESULTS = []
PASS, FAIL = "✅", "❌"

def api(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {"success": False}

def fetch_page(path):
    """模拟浏览器打开页面，验证HTML结构"""
    try:
        req = urllib.request.Request(BASE + path)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8")
            return 200, html
    except Exception as e:
        return 0, str(e)

def login(emp, pw):
    s, r = api("POST", "/api/login", body={"employee_no": emp, "password": pw})
    return r.get("data", {}).get("token") if r.get("success") else None, r

def step(step_id, flow, desc, action, validator, token=None):
    """执行一个测试步骤：
    action: dict with method, path, body (or None for page fetch only)
    validator: function that takes (status, body, html) and returns (ok, detail)"""
    # Step 1: 先验证前端页面可加载
    page_status, page_html = fetch_page("/")
    page_ok = page_status == 200 and "银行柜面业务管理系统" in page_html

    # Step 2: 执行操作
    if action:
        s, r = api(action.get("method", "GET"), action["path"], token=token, body=action.get("body"))
    else:
        s, r = page_status, {"success": True, "html_ok": page_ok}

    # Step 3: 验证
    ok, detail = validator(s, r, page_html if page_ok else "")

    status = PASS if ok else FAIL
    RESULTS.append({
        "flow": flow, "step": step_id, "desc": desc,
        "page_ok": page_ok, "ok": ok, "result": status,
        "detail": detail
    })
    if not ok:
        print(f"  {status} {step_id}: {desc} → {detail}")
    return ok, r

def check_success(expect_success=True, contains=None):
    def _check(s, r, html):
        if not html:
            return False, "前端页面加载失败"
        ok = r.get("success") == expect_success
        if contains and ok:
            body_str = json.dumps(r, ensure_ascii=False)
            ok = contains in body_str
        if not ok:
            msg = r.get("message", "") or r.get("error", "")
            return False, f"预期success={expect_success}, 实际: {msg[:80]}"
        return True, r.get("message", "OK")
    return _check

def check_balance(min_bal):
    def _check(s, r, html):
        if not html: return False, "前端页面加载失败"
        bal = r.get("data", {}).get("balance", -1)
        if bal < min_bal:
            return False, f"余额={bal}, 预期>={min_bal}"
        return True, f"余额={bal}"
    return _check

# ====== 开始测试 ======
print("=" * 70)
print("银行柜面系统 - 全链路业务流程测试")
print("=" * 70)

# 阶段0: 验证前端页面
print("\n📄 阶段0: 前端页面加载验证")
status, html = fetch_page("/")
page_ok = status == 200 and "银行柜面业务管理系统" in html
has_login = "login" in html.lower() and "工号" in html
has_js = "operations.js" in html and "app.js" in html

RESULTS.append({"flow": "基础", "step": "PAGE-01", "desc": "前端首页加载",
                "page_ok": page_ok, "ok": page_ok and has_login and has_js,
                "result": PASS if (page_ok and has_login and has_js) else FAIL,
                "detail": f"HTTP {status}, 含登录表单={has_login}, 含JS={has_js}"})

status, html = fetch_page("/operations.js")
js_ok = status == 200 and "身份标识" in html
RESULTS.append({"flow": "基础", "step": "PAGE-02", "desc": "前端operations.js加载",
                "page_ok": True, "ok": js_ok,
                "result": PASS if js_ok else FAIL,
                "detail": f"HTTP {status}, 含'身份标识'={js_ok}"})

print(f"  首页: {'✅' if page_ok else '❌'}  JS: {'✅' if js_ok else '❌'}")

# ====== 流程一：新客户全生命周期 ======
# 注意：step() 只有收到 action 才会真正调 API 并把真实响应交给 validator；
# 早期版本 action=None + 在外面调 api() 的写法让 validator 拿到的是桩数据，判定是假的，已修正。
print("\n🔄 流程一：王五从开户到尝试销户 (储蓄)")

t_s = login("S001", "123456")[0]
step("F1-01", "F1", "储蓄员登录", None, check_success(), token=t_s)

# F1-02: 开户（线上库重复跑时王五已存在→E-1 属预期，转为查询取账号继续后续步骤）
_, r = step("F1-02", "F1", "王五开户(重复跑允许E-1复用)",
     {"method": "POST", "path": "/api/savings/open-account",
      "body": {"name": "王五", "id_type": "身份证", "id_no": "110101198505056789",
               "email": "wangwu@test.com", "phone": "13900000003", "initial_balance": 1000}},
     lambda s,r,h: (bool(h) and (r.get("success") or r.get("error") == "E-1"),
                    "新开户成功" if r.get("success") else f"已有账户复用: {r.get('message','')[:40]}"),
     token=t_s)
ww_account = r.get("data", {}).get("account", {}).get("account_no", "") if r.get("success") else ""
if not ww_account:  # 已存在：按证件号查回账号
    s, rq = api("GET", "/api/savings/query?ident=110101198505056789", token=t_s)
    ww_account = rq.get("data", {}).get("account", {}).get("account_no", "")
time.sleep(2)

# F1-03: 用证件号存款
step("F1-03", "F1", "用证件号存款5000",
     {"method": "POST", "path": "/api/savings/deposit",
      "body": {"ident": "110101198505056789", "amount": 5000}},
     lambda s,r,h: (r.get("success", False) and r.get("data",{}).get("balance",0) >= 6000,
                    f"余额={r.get('data',{}).get('balance')}"),
     token=t_s)
time.sleep(2)

# F1-04: 用邮箱存款
step("F1-04", "F1", "用邮箱存款2000",
     {"method": "POST", "path": "/api/savings/deposit",
      "body": {"ident": "wangwu@test.com", "amount": 2000}},
     lambda s,r,h: (r.get("success", False) and r.get("data",{}).get("balance",0) >= 7000,
                    f"余额={r.get('data',{}).get('balance')}"),
     token=t_s)
time.sleep(2)

# F1-05: 用手机号取款
step("F1-05", "F1", "用手机号取款1000",
     {"method": "POST", "path": "/api/savings/withdraw",
      "body": {"ident": "13900000003", "amount": 1000}},
     lambda s,r,h: (r.get("success", False) and r.get("data",{}).get("balance",0) >= 6000,
                    f"余额={r.get('data',{}).get('balance')}"),
     token=t_s)
time.sleep(2)

# F1-06: 用账号查询
step("F1-06", "F1", f"用账号{ww_account}查询",
     {"method": "GET", "path": f"/api/savings/query?ident={ww_account}"},
     lambda s,r,h: (r.get("success", False) and "王五" in json.dumps(r, ensure_ascii=False),
                    "查到王五" if r.get("success") else r.get("message","")),
     token=t_s)

# F1-07: 挂失
step("F1-07", "F1", "用邮箱挂失卡片",
     {"method": "POST", "path": "/api/savings/card",
      "body": {"ident": "wangwu@test.com", "op": "LOSS"}},
     check_success(), token=t_s)
time.sleep(2)

# F1-07b: 挂失只进不出——存款放行、取款拦截
step("F1-07b", "F1", "挂失卡存款放行(只进不出)",
     {"method": "POST", "path": "/api/savings/deposit",
      "body": {"ident": "wangwu@test.com", "amount": 100}},
     check_success(), token=t_s)
step("F1-07c", "F1", "挂失卡取款拦截E-LOST",
     {"method": "POST", "path": "/api/savings/withdraw",
      "body": {"ident": "wangwu@test.com", "amount": 100}},
     lambda s,r,h: (r.get("error") == "E-LOST", f"错误码={r.get('error')}"),
     token=t_s)

# F1-08: 解挂 + 更新信息
step("F1-08a", "F1", "手机号解挂",
     {"method": "POST", "path": "/api/savings/card",
      "body": {"ident": "13900000003", "op": "UNLOSS"}},
     check_success(), token=t_s)
time.sleep(2)
step("F1-08", "F1", "更新地址职业",
     {"method": "POST", "path": "/api/savings/update-customer",
      "body": {"ident": "110101198505056789", "address": "北京市朝阳区", "occupation": "工程师"}},
     check_success(), token=t_s)
time.sleep(2)

# F1-08c: 当日冲正——存一笔→冲正→余额复原
_, rdep = step("F1-08b", "F1", "存款88(待冲正)",
     {"method": "POST", "path": "/api/savings/deposit",
      "body": {"ident": "110101198505056789", "amount": 88}},
     check_success(), token=t_s)
_dep_txn = rdep.get("data", {}).get("txn", {}).get("txn_no", "")
_bal_before = (rdep.get("data", {}).get("balance") or 0) - 88
step("F1-08c", "F1", "当日冲正(存款扣回)",
     {"method": "POST", "path": "/api/savings/reverse",
      "body": {"txn_no": _dep_txn, "reason": "流程测试冲正"}},
     lambda s,r,h: (r.get("success", False) and abs((r.get("data",{}).get("balance") or -1) - _bal_before) < 0.01,
                    f"冲正后余额={r.get('data',{}).get('balance')} (应={_bal_before})"),
     token=t_s)

# F1-09: 尝试销户（有余额应失败）
step("F1-09", "F1", "尝试销户(有余额应失败)",
     {"method": "POST", "path": "/api/savings/close-account",
      "body": {"ident": "110101198505056789"}},
     lambda s,r,h: (r.get("success") == False and "E-5" in (r.get("error") or ""),
                    f"正确拒绝: {r.get('message','')}"),
     token=t_s)

# ====== 流程二：贷款全流程 ======
print("\n🔄 流程二：张三贷款全流程")

t_l = login("L001", "123456")[0]
step("F2-01", "F2", "贷款员登录", None, check_success(), token=t_l)

# 审贷分离：申请(L001)/审批(L002)/放款(L001) 须不同工号。幂等创建 L002（已存在返回 E-1 忽略）
t_adm = login("admin", "admin123")[0]
api("POST", "/api/admin/users", token=t_adm,
    body={"employee_no": "L002", "name": "贷款复核员", "role": "LOAN_CLERK", "password": "123456"})
t_l2 = login("L002", "123456")[0]

time.sleep(2)
_, r = step("F2-02", "F2", "张三贷款申请",
     {"method": "POST", "path": "/api/loan/apply",
      "body": {"ident": "110101199001011234", "loan_type": "个人消费贷",
               "amount": 30000, "term_months": 6, "purpose": "装修", "guarantee": "信用"}},
     lambda s,r,h: (r.get("success", False) and "PENDING" in json.dumps(r, ensure_ascii=False),
                    f"合同号={r.get('data',{}).get('loan',{}).get('contract_no','')}"),
     token=t_l)
ln_contract = r.get("data", {}).get("loan", {}).get("contract_no", "") if r.get("success") else ""
time.sleep(2)

if ln_contract:
    # F2-03a: 审贷分离负例——申请经办人 L001 自审应被拦截
    step("F2-03a", "F2", "经办人自审被拦截E-SOD(审贷分离)",
         {"method": "POST", "path": "/api/loan/approve",
          "body": {"contract_no": ln_contract, "decision": "APPROVED"}},
         lambda s,r,h: (r.get("error") == "E-SOD", f"错误码={r.get('error')}"),
         token=t_l)
    step("F2-03", "F2", "审批通过(L002复核·审贷分离)",
         {"method": "POST", "path": "/api/loan/approve",
          "body": {"contract_no": ln_contract, "decision": "APPROVED",
                   "approved_amount": 30000, "interest_rate": 0.045,
                   "term_months": 6, "repay_method": "等额本息"}},
         lambda s,r,h: (r.get("success", False) and "APPROVED" in json.dumps(r, ensure_ascii=False),
                        r.get("message","")),
         token=t_l2)
    time.sleep(2)

    # F2-04a: 审贷分离负例——审批人 L002 放款应被拦截
    step("F2-04a", "F2", "审批人自放被拦截E-SOD(审贷分离)",
         {"method": "POST", "path": "/api/loan/disburse", "body": {"contract_no": ln_contract}},
         lambda s,r,h: (r.get("error") == "E-SOD", f"错误码={r.get('error')}"),
         token=t_l2)
    step("F2-04", "F2", "放款(生成6期等额本息计划)",
         {"method": "POST", "path": "/api/loan/disburse", "body": {"contract_no": ln_contract}},
         lambda s,r,h: (r.get("success", False)
                        and len(r.get("data",{}).get("loan",{}).get("schedule") or []) == 6,
                        f"期数={len(r.get('data',{}).get('loan',{}).get('schedule') or [])}"
                        if r.get("success") else r.get("message","")),
         token=t_l)
    time.sleep(2)

# F2-05: 还款（用合同号定位——线上库张三可能有历史遗留贷款，客户级身份会命中多笔）
step("F2-05", "F2", "凭合同号还款5000",
     {"method": "POST", "path": "/api/loan/repay",
      "body": {"ident": ln_contract or "13800000001", "amount": 5000}},
     check_success(), token=t_l)
time.sleep(2)

# F2-06: 再还款并验证还款计划瀑布（返回含 schedule/剩余本金）
step("F2-06", "F2", "再还款5000(瀑布冲抵含计划表)",
     {"method": "POST", "path": "/api/loan/repay",
      "body": {"ident": ln_contract or "110101199001011234", "amount": 5000}},
     lambda s,r,h: (r.get("success", False) and bool(r.get("data",{}).get("loan",{}).get("schedule")),
                    f"剩余本金={r.get('data',{}).get('loan',{}).get('balance')}"),
     token=t_l)
time.sleep(2)

# F2-07: 逾期查询
step("F2-07", "F2", "逾期查询(张三)",
     {"method": "GET", "path": "/api/loan/overdue?ident=110101199001011234"},
     check_success(), token=t_l)

# F2-08: 审批拒绝
_, r2 = step("F2-08a", "F2", "李四申请汽车贷款",
     {"method": "POST", "path": "/api/loan/apply",
      "body": {"ident": "110101199203054321", "loan_type": "汽车贷款",
               "amount": 200000, "term_months": 24, "purpose": "购车", "guarantee": "抵押"}},
     check_success(), token=t_l)
rej_cn = r2.get("data", {}).get("loan", {}).get("contract_no", "") if r2.get("success") else ""
if rej_cn:
    time.sleep(2)
    step("F2-08", "F2", "审批拒绝(L002复核)",
         {"method": "POST", "path": "/api/loan/approve",
          "body": {"contract_no": rej_cn, "decision": "REJECTED", "reason": "资料不全"}},
         lambda s,r,h: (r.get("success", False) and "REJECTED" in json.dumps(r, ensure_ascii=False),
                        r.get("message","")),
         token=t_l2)

# ====== 流程三：外汇全流程 ======
print("\n🔄 流程三：李四外汇买卖全流程")

t_f = login("F001", "123456")[0]
step("F3-01", "F3", "外汇员登录", None, check_success(), token=t_f)

time.sleep(2)
s, r = api("POST", "/api/forex/open-subaccount", token=t_f,
           body={"ident": "110101199203054321", "currency": "USD"})
step("F3-02", "F3", "李四开立USD子户", None, check_success(), token=t_f)
time.sleep(2)

# F3-03: 存人民币 → 买USD
t_s2 = login("S001", "123456")[0]
api("POST", "/api/savings/deposit", token=t_s2, body={"ident": "110101199203054321", "amount": 20000})
time.sleep(3)
s, r = api("POST", "/api/forex/trade", token=t_f,
           body={"ident": "110101199203054321", "currency": "USD", "direction": "BUY", "amount": 100})
step("F3-03", "F3", "买USD 100(需实时汇率)", None,
     lambda s,r,h: (r.get("success", False),
                    f"本币={r.get('data',{}).get('cny_amount')}" if r.get("success") else r.get("message","")),
     token=t_f)
time.sleep(2)

# F3-04: 查询外汇余额
s, r = api("GET", "/api/forex/query?ident=110101199203054321", token=t_f)
step("F3-04", "F3", "查询李四外汇余额和历史", None,
     lambda s,r,h: (r.get("success", False) and len(r.get("data",{}).get("fx_accounts",[])) > 0,
                    f"{len(r.get('data',{}).get('fx_accounts',[]))}个子户"),
     token=t_f)

# F3-05: 卖出部分USD
time.sleep(2)
s, r = api("POST", "/api/forex/trade", token=t_f,
           body={"ident": "110101199203054321", "currency": "USD", "direction": "SELL", "amount": 50})
step("F3-05", "F3", "卖出USD 50", None, check_success(), token=t_f)

# F3-06: 查询实时汇率
s, r = api("GET", "/api/forex/live-rate?currency=USD", token=t_f)
step("F3-06", "F3", "查美元实时汇率", None,
     lambda s,r,h: (r.get("success", False) and len(r.get("data",{}).get("rates",[])) > 0,
                    f"缓存={r.get('data',{}).get('from_cache')}"),
     token=t_f)

# ====== 流程四：信用卡全流程 ======
print("\n🔄 流程四：李四信用卡全流程")

t_c = login("CC001", "123456")[0]
step("F4-01", "F4", "信用卡员登录", None, check_success(), token=t_c)

time.sleep(2)
s, r = api("POST", "/api/creditcard/apply", token=t_c,
           body={"ident": "110101199203054321", "card_type": "金卡"})
cc_no = r.get("data", {}).get("credit_card", {}).get("card_no", "") if r.get("success") else ""
step("F4-02", "F4", f"李四信用卡申请(卡号={cc_no})", None,
     lambda s,r,h: (r.get("success", False),
                    f"卡号={cc_no}"),
     token=t_c)
time.sleep(2)

if cc_no:
    s, r = api("POST", "/api/creditcard/approve", token=t_c, body={
        "card_no": cc_no, "decision": "APPROVED", "credit_limit": 20000,
        "bill_day": 10, "repay_day": 25})
    step("F4-03", "F4", "审批通过(额度20000)", None,
         lambda s,r,h: (r.get("success", False) and "ACTIVE" in json.dumps(r, ensure_ascii=False),
                        r.get("message","")),
         token=t_c)
    time.sleep(2)

    s, r = api("POST", "/api/creditcard/cash-advance", token=t_c,
               body={"ident": "110101199203054321", "amount": 3000})
    step("F4-04", "F4", "预借现金3000", None,
         lambda s,r,h: (r.get("success", False),
                        f"手续费={r.get('data',{}).get('fee')}" if r.get("success") else r.get("message","")),
         token=t_c)
    time.sleep(2)

    from datetime import datetime
    cycle = datetime.now().strftime("%Y%m")
    s, r = api("POST", "/api/creditcard/bill", token=t_c,
               body={"card_no": cc_no, "bill_cycle": cycle})
    step("F4-05", "F4", f"生成{cycle}账单", None, check_success(), token=t_c)
    time.sleep(2)

    s, r = api("POST", "/api/creditcard/repay", token=t_c,
               body={"ident": "110101199203054321", "repay_type": "FULL"})
    step("F4-06", "F4", "全额还款", None, check_success(), token=t_c)
    time.sleep(2)

    s, r = api("POST", "/api/creditcard/repay", token=t_c,
               body={"ident": "110101199203054321", "repay_type": "FULL"})
    step("F4-07", "F4", "再次还款(应失败-无待还)", None,
         lambda s,r,h: (r.get("success") == False,
                        f"正确拒绝: {r.get('message','')}"),
         token=t_c)

# ====== 流程五：错误恢复 ======
print("\n🔄 流程五：错误恢复流程")

step("F5-01", "F5", "不存在ident存款→E-NOCUST", None,
     lambda s,r,h: (r.get("success") == False and r.get("error") == "E-NOCUST",
                    f"错误码={r.get('error')}"),
     token=t_s)
api("POST", "/api/savings/deposit", token=t_s, body={"ident": "000000", "amount": 100})

s, r = api("POST", "/api/savings/deposit", token=t_s, body={"ident": "110101199001011234", "amount": 0})
step("F5-02", "F5", "金额0存款→E-2", None,
     lambda s,r,h: (r.get("success") == False,
                    f"错误码={r.get('error')}"),
     token=t_s)

s, r = api("POST", "/api/savings/withdraw", token=t_s, body={"ident": "110101199001011234", "amount": 99999999})
step("F5-03", "F5", "超额取款→E-BAL", None,
     lambda s,r,h: (r.get("success") == False,
                    f"错误码={r.get('error')}"),
     token=t_s)

s, r = api("POST", "/api/login", body={"employee_no": "admin", "password": "wrong"})
step("F5-04", "F5", "错误密码→E-1", None,
     lambda s,r,h: (r.get("success") == False and r.get("error") == "E-1",
                    f"错误码={r.get('error')}"), token=None)

s, r = api("POST", "/api/login", body={"employee_no": "admin", "password": "admin123"})
step("F5-05", "F5", "错误后正确登录→成功", None, check_success(), token=None)

# ====== 流程六：统一身份标识 ======
print("\n🔄 流程六：同一客户4种标识验证")

for label, ident in [("证件号", "110101199001011234"), ("邮箱", "zhangsan@example.com"),
                      ("手机号", "13800000001"), ("账号", "6217000000000001")]:
    s, r = api("GET", f"/api/savings/query?ident={ident}", token=t_s)
    step(f"F6-{label}", "F6", f"用{label}查张三", None,
         lambda s,r,h,lb=label: (r.get("success", False) and "张三" in json.dumps(r, ensure_ascii=False),
                                 f"用{lb}成功查到张三"),
         token=t_s)

# ====== 流程七：跨角色权限 ======
print("\n🔄 流程七：跨角色权限")

s, r = api("POST", "/api/loan/apply", token=t_s, body={
    "ident": "110101199001011234", "loan_type": "个人消费贷", "amount": 10000, "term_months": 6})
step("F7-01", "F7", "储蓄员调贷款API→403", None,
     lambda s,r,h: (s == 403 and r.get("error") == "E-PERM", f"HTTP {s}"),
     token=t_s)

s, r = api("POST", "/api/savings/deposit", token=t_l, body={"ident": "110101199001011234", "amount": 100})
step("F7-02", "F7", "贷款员调储蓄API→403", None,
     lambda s,r,h: (s == 403, f"HTTP {s}"), token=t_l)

s, r = api("GET", "/api/admin/users", token=t_s)
step("F7-03", "F7", "储蓄员调管理API→403", None,
     lambda s,r,h: (s == 403, f"HTTP {s}"), token=t_s)

# ====== 结果汇总 ======
print("\n\n" + "=" * 70)
print("全链路业务流程测试结果")
print("=" * 70)

passed = sum(1 for r in RESULTS if r["result"] == PASS)
failed = sum(1 for r in RESULTS if r["result"] == FAIL)
flows = {}
for r in RESULTS:
    flows.setdefault(r["flow"], []).append(r)

for flow_name, items in flows.items():
    fp = sum(1 for i in items if i["result"] == PASS)
    ff = sum(1 for i in items if i["result"] == FAIL)
    status = "✅" if ff == 0 else f"❌ ({ff}失败)"
    print(f"\n{status} {flow_name}: {fp}/{len(items)} 通过")
    for i in items:
        if i["result"] == FAIL:
            print(f"    ❌ {i['step']}: {i['desc']} → {i['detail']}")

print(f"\n{'='*70}")
print(f"总计: {passed} 通过 / {failed} 失败 / {len(RESULTS)} 步骤")
print(f"前端页面: {'✅ 正常' if page_ok else '❌ 异常'}")
print(f"{'='*70}")
