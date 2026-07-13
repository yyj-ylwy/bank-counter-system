#!/usr/bin/env python3
"""银行柜面系统 - 全量测试执行器
针对 https://bank-counter-system.onrender.com 执行56条测试用例并记录结果"""
import json, urllib.request, urllib.error, sys, time

BASE = "https://bank-counter-system.onrender.com"
RESULTS = []
PASS, FAIL, SKIP = "✅", "❌", "⏭️"

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

def login(emp, pw):
    s, r = api("POST", "/api/login", body={"employee_no": emp, "password": pw})
    return r.get("data", {}).get("token") if r.get("success") else None

def check(test_id, desc, expected, actual_status, actual_body):
    ok = False
    detail = ""
    if isinstance(expected, dict):
        # expected is a dict of field checks
        ok = True
        for k, v in expected.items():
            if k == "status": ok = ok and (actual_status == v)
            elif k == "success": ok = ok and (actual_body.get("success") == v)
            elif k == "error": ok = ok and (actual_body.get("error") == v)
            elif k == "contains":
                body_str = json.dumps(actual_body, ensure_ascii=False)
                ok = ok and (v in body_str)
    elif isinstance(expected, str):
        ok = expected in str(actual_status) or expected in json.dumps(actual_body, ensure_ascii=False)
    status = PASS if ok else FAIL
    RESULTS.append({"id": test_id, "desc": desc, "expected": str(expected),
                    "status_code": actual_status, "body_summary": str(actual_body)[:200],
                    "result": status})
    return ok, actual_body

# ====== 阶段 0: 登录 ======
print("=" * 60)
print("阶段 0: 登录获取令牌")
print("=" * 60)

tokens = {}
for emp, pw, role in [("admin", "admin123", "ADMIN"), ("S001", "123456", "SAVINGS"),
                        ("L001", "123456", "LOAN"), ("F001", "123456", "FOREX"),
                        ("CC001", "123456", "CREDIT")]:
    t = login(emp, pw)
    tokens[role] = t
    print(f"  {role} ({emp}): {'OK' if t else 'FAIL'}")

saver_token = tokens["SAVINGS"]
loan_token = tokens["LOAN"]
forex_token = tokens["FOREX"]
cc_token = tokens["CREDIT"]
admin_token = tokens["ADMIN"]

# ====== 阶段 A: 登录与鉴权 ======
print("\n" + "=" * 60)
print("阶段 A: 登录与鉴权")
print("=" * 60)

s, r = api("POST", "/api/login", body={"employee_no": "admin", "password": "admin123"})
check("A01", "admin正确登录", {"success": True, "status": 200}, s, r)

s, r = api("POST", "/api/login", body={"employee_no": "S001", "password": "123456"})
check("A02", "S001正确登录", {"success": True, "status": 200}, s, r)

s, r = api("POST", "/api/login", body={"employee_no": "L001", "password": "123456"})
check("A03", "L001正确登录", {"success": True, "status": 200}, s, r)

s, r = api("POST", "/api/login", body={"employee_no": "admin", "password": "wrong"})
check("A04", "错误密码登录", {"success": False, "error": "E-1"}, s, r)

s, r = api("GET", "/api/me")
check("A05", "未登录访问 /api/me", {"status": 401}, s, r)

# ====== 阶段 0.5: 获取测试数据 ======
print("\n" + "=" * 60)
print("阶段 0.5: 获取测试数据(张三账号/李四账号)")
print("=" * 60)

s, r = api("GET", "/api/savings/query?ident=110101199001011234", token=saver_token)
zhangsan_account = r.get("data", {}).get("account", {}).get("account_no", "") if r.get("success") else ""
print(f"  张三账号: {zhangsan_account}")

s, r = api("GET", "/api/savings/query?ident=110101199203054321", token=saver_token)
lisi_account = r.get("data", {}).get("account", {}).get("account_no", "") if r.get("success") else ""
print(f"  李四账号: {lisi_account}")

# Get current balance for later tests
s, r = api("GET", f"/api/savings/query?ident={zhangsan_account}", token=saver_token)
zhangsan_bal = r.get("data", {}).get("account", {}).get("balance", 0) if r.get("success") else 0
print(f"  张三余额: {zhangsan_bal}")

# Get admin users count
s, r = api("GET", "/api/admin/users", token=admin_token)
user_count = len(r.get("data", {}).get("users", [])) if r.get("success") else 0
print(f"  用户数: {user_count}")

# Get current params
s, r = api("GET", "/api/admin/params", token=admin_token)
print(f"  参数数量: {len(r.get('data', {}).get('params', [])) if r.get('success') else 0}")

# ====== 阶段 B: 储蓄业务 ======
print("\n" + "=" * 60)
print("阶段 B: 储蓄业务 (UC-102~108)")
print("=" * 60)

# B01-B04: 存款 - 用不同身份标识
print("\n--- UC-102 存款 ---")
time.sleep(2)
s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "110101199001011234", "amount": 100})
check("B01", "存款-证件号", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "zhangsan@example.com", "amount": 200})
check("B02", "存款-邮箱", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "13800000001", "amount": 150})
check("B03", "存款-手机号", {"success": True}, s, r)

time.sleep(2)
if zhangsan_account:
    s, r = api("POST", "/api/savings/deposit", token=saver_token,
               body={"ident": zhangsan_account, "amount": 300})
    check("B04", "存款-账号", {"success": True}, s, r)
else:
    RESULTS.append({"id": "B04", "desc": "存款-账号", "expected": "success=true", "result": SKIP, "note": "无法获取测试账号"})

# B05-B08: 异常
time.sleep(2)
s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "999999999999999999", "amount": 100})
check("B05", "存款-ident不存在", {"success": False}, s, r)

s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "110101199001011234", "amount": 0})
check("B06", "存款-金额为0", {"success": False}, s, r)

s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "110101199001011234", "amount": -50})
check("B07", "存款-金额为负", {"success": False}, s, r)

s, r = api("POST", "/api/savings/deposit", token=saver_token,
           body={"ident": "", "amount": 100})
check("B08", "存款-ident为空", {"success": False}, s, r)

# B09-B11: 取款
print("\n--- UC-103 取款 ---")
time.sleep(2)
s, r = api("POST", "/api/savings/withdraw", token=saver_token,
           body={"ident": "110101199001011234", "amount": 50})
check("B09", "取款-正常", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/withdraw", token=saver_token,
           body={"ident": "110101199001011234", "amount": 999999})
check("B10", "取款-余额不足", {"success": False}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/withdraw", token=saver_token,
           body={"ident": "110101199001011234", "amount": 60000})
check("B11", "取款-超单日限额", {"success": False}, s, r)

# B12-B14: 转账
print("\n--- UC-104 转账 ---")
time.sleep(2)
if lisi_account:
    s, r = api("POST", "/api/savings/transfer", token=saver_token,
               body={"transfer_type": "INTRA", "ident": "110101199001011234",
                     "to_ident": "110101199203054321", "amount": 100})
    check("B12", "转账-本行(to_ident)", {"success": True}, s, r)
else:
    RESULTS.append({"id": "B12", "desc": "转账-本行", "expected": "success=true", "result": SKIP, "note": "无法获取李四账号"})

time.sleep(2)
s, r = api("POST", "/api/savings/transfer", token=saver_token,
           body={"transfer_type": "INTRA", "ident": "110101199001011234",
                 "to_ident": "110101199001011234", "amount": 100})
check("B13", "转账-同一账户", {"success": False}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/transfer", token=saver_token,
           body={"transfer_type": "INTER", "ident": "110101199001011234",
                 "to_account_no": "6227001234567890", "to_bank": "中国工商银行", "amount": 200})
check("B14", "转账-跨行(含手续费)", {"success": True, "contains": "fee"}, s, r)

# B15-B18: 查询
print("\n--- UC-105 查询 ---")
s, r = api("GET", "/api/savings/query?ident=110101199001011234", token=saver_token)
check("B15", "查询-证件号", {"success": True, "contains": "张三"}, s, r)

s, r = api("GET", "/api/savings/query?ident=13800000001", token=saver_token)
check("B16", "查询-手机号", {"success": True, "contains": "张三"}, s, r)

if zhangsan_account:
    s, r = api("GET", f"/api/savings/query?ident={zhangsan_account}", token=saver_token)
    check("B17", "查询-账号", {"success": True, "contains": "张三"}, s, r)
else:
    RESULTS.append({"id": "B17", "desc": "查询-账号", "expected": "success=true", "result": SKIP})

s, r = api("GET", "/api/savings/query?ident=000000000000000000", token=saver_token)
check("B18", "查询-不存在标识", {"success": False}, s, r)

# B19-B21: 卡操作
print("\n--- UC-106 卡操作 ---")
time.sleep(2)
s, r = api("POST", "/api/savings/card", token=saver_token,
           body={"ident": "110101199001011234", "op": "LOSS"})
check("B19", "卡操作-挂失", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/card", token=saver_token,
           body={"ident": "110101199001011234", "op": "UNLOSS"})
check("B20", "卡操作-解挂", {"success": True}, s, r)

time.sleep(2)
# 先挂失
api("POST", "/api/savings/card", token=saver_token,
    body={"ident": "110101199001011234", "op": "LOSS"})
time.sleep(2)
# 重复挂失
s, r = api("POST", "/api/savings/card", token=saver_token,
           body={"ident": "110101199001011234", "op": "LOSS"})
check("B21", "卡操作-重复挂失", {"success": False}, s, r)
# 还原
api("POST", "/api/savings/card", token=saver_token,
    body={"ident": "110101199001011234", "op": "UNLOSS"})

# B22-B23: 客户信息更新
print("\n--- UC-108 客户信息更新 ---")
time.sleep(2)
s, r = api("POST", "/api/savings/update-customer", token=saver_token,
           body={"ident": "110101199001011234", "phone": "13900000001"})
check("B22", "更新客户-正常(改手机号)", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/update-customer", token=saver_token,
           body={"ident": "110101199001011234", "email": "lisi@example.com"})
check("B23", "更新客户-邮箱被占用", {"success": False}, s, r)

# 改回原手机号
api("POST", "/api/savings/update-customer", token=saver_token,
    body={"ident": "110101199001011234", "phone": "13800000001"})

# ====== 阶段 C: 贷款业务 ======
print("\n" + "=" * 60)
print("阶段 C: 贷款业务 (UC-201~206)")
print("=" * 60)

time.sleep(2)
s, r = api("POST", "/api/loan/apply", token=loan_token,
           body={"ident": "110101199001011234", "loan_type": "个人消费贷",
                 "amount": 50000, "term_months": 12,
                 "purpose": "测试贷款", "guarantee": "信用"})
check("C01", "贷款申请", {"success": True}, s, r)
contract_no = r.get("data", {}).get("loan", {}).get("contract_no", "") if r.get("success") else ""
print(f"  合同号: {contract_no}")

# C03: 审批通过
if contract_no:
    time.sleep(2)
    s, r = api("POST", "/api/loan/approve", token=loan_token,
               body={"contract_no": contract_no, "decision": "APPROVED",
                     "approved_amount": 50000, "interest_rate": 0.05, "term_months": 12})
    check("C03", "贷款审批-通过", {"success": True}, s, r)

    # C05: 放款
    time.sleep(2)
    s, r = api("POST", "/api/loan/disburse", token=loan_token,
               body={"contract_no": contract_no})
    check("C05", "贷款放款", {"success": True}, s, r)

    # C06: 重复放款
    time.sleep(2)
    s, r = api("POST", "/api/loan/disburse", token=loan_token,
               body={"contract_no": contract_no})
    check("C06", "贷款-重复放款", {"success": False}, s, r)

    # C07: 还款
    time.sleep(2)
    s, r = api("POST", "/api/loan/repay", token=loan_token,
               body={"ident": "110101199001011234", "amount": 10000})
    check("C07", "贷款还款-正常", {"success": True}, s, r)

    # C08: 超额还款
    time.sleep(2)
    s, r = api("POST", "/api/loan/repay", token=loan_token,
               body={"ident": "110101199001011234", "amount": 99999999})
    check("C08", "贷款还款-超额", {"success": False}, s, r)
else:
    for tid in ["C03", "C05", "C06", "C07", "C08"]:
        RESULTS.append({"id": tid, "desc": f"贷款-{tid}", "expected": "success", "result": SKIP, "note": "C01未生成合同号"})

# C04: 审批拒绝 (需要新申请)
print("\n--- 贷款审批拒绝测试 ---")
time.sleep(2)
s, r = api("POST", "/api/loan/apply", token=loan_token,
           body={"ident": "110101199203054321", "loan_type": "住房贷款",
                 "amount": 300000, "term_months": 60,
                 "purpose": "购房", "guarantee": "抵押"})
reject_cn = r.get("data", {}).get("loan", {}).get("contract_no", "") if r.get("success") else ""
if reject_cn:
    time.sleep(2)
    s, r = api("POST", "/api/loan/approve", token=loan_token,
               body={"contract_no": reject_cn, "decision": "REJECTED", "reason": "资料不全"})
    check("C04", "贷款审批-拒绝", {"success": True}, s, r)

# C09: 逾期查询
time.sleep(2)
s, r = api("GET", "/api/loan/overdue?days=0", token=loan_token)
check("C09", "逾期查询", {"success": True}, s, r)

# ====== 阶段 D: 外汇业务 ======
print("\n" + "=" * 60)
print("阶段 D: 外汇业务 (UC-301~306)")
print("=" * 60)

time.sleep(2)
s, r = api("POST", "/api/forex/open-subaccount", token=forex_token,
           body={"ident": "110101199001011234", "currency": "USD"})
check("D01", "外汇开户-USD", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/forex/open-subaccount", token=forex_token,
           body={"ident": "110101199001011234", "currency": "USD"})
check("D02", "外汇开户-重复USD", {"success": False}, s, r)

time.sleep(2)
s, r = api("GET", "/api/forex/live-rate", token=forex_token)
check("D03", "实时汇率查询-全部", {"success": True, "contains": "USD"}, s, r)

# D04: 外汇买入 - 先确保账户有足够人民币余额
print("  先存入足够人民币...")
time.sleep(2)
api("POST", "/api/savings/deposit", token=saver_token,
    body={"ident": "110101199001011234", "amount": 50000})
time.sleep(3)
s, r = api("POST", "/api/forex/trade", token=forex_token,
           body={"ident": "110101199001011234", "currency": "USD",
                 "direction": "BUY", "amount": 100})
check("D04", "外汇买入-USD 100", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/forex/trade", token=forex_token,
           body={"ident": "110101199001011234", "currency": "JPY",
                 "direction": "SELL", "amount": 999999})
check("D05", "外汇卖出-余额不足(JPY)", {"success": False}, s, r)

# ====== 阶段 E: 信用卡业务 ======
print("\n" + "=" * 60)
print("阶段 E: 信用卡业务 (UC-401~406)")
print("=" * 60)

time.sleep(2)
s, r = api("POST", "/api/creditcard/apply", token=cc_token,
           body={"ident": "110101199001011234", "card_type": "普卡"})
check("E01", "信用卡申请", {"success": True}, s, r)
cc_card_no = r.get("data", {}).get("credit_card", {}).get("card_no", "") if r.get("success") else ""
print(f"  信用卡号: {cc_card_no}")

if cc_card_no:
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/approve", token=cc_token,
               body={"card_no": cc_card_no, "decision": "APPROVED",
                     "credit_limit": 10000, "bill_day": 5, "repay_day": 25})
    check("E02", "信用卡审批-通过", {"success": True}, s, r)

    # E03: 预借现金
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/cash-advance", token=cc_token,
               body={"ident": "110101199001011234", "amount": 2000})
    check("E03", "预借现金-正常2000", {"success": True}, s, r)

    # E04: 预借现金-额度不足
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/cash-advance", token=cc_token,
               body={"ident": "110101199001011234", "amount": 99999})
    check("E04", "预借现金-额度不足", {"success": False}, s, r)

    # E05: 账单生成
    from datetime import datetime
    cycle = datetime.now().strftime("%Y%m")
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/bill", token=cc_token,
               body={"card_no": cc_card_no, "bill_cycle": cycle})
    check("E05", "信用卡账单生成", {"success": True}, s, r)

    # E06: 还款
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/repay", token=cc_token,
               body={"ident": "110101199001011234", "repay_type": "FULL"})
    check("E06", "信用卡还款-全额", {"success": True}, s, r)

    # E07: 无账单还款(再次)
    time.sleep(2)
    s, r = api("POST", "/api/creditcard/repay", token=cc_token,
               body={"ident": "110101199001011234", "repay_type": "FULL"})
    check("E07", "信用卡还款-无待还账单", {"success": False}, s, r)
else:
    for tid in ["E02", "E03", "E04", "E05", "E06", "E07"]:
        RESULTS.append({"id": tid, "desc": f"信用卡-{tid}", "expected": "success", "result": SKIP, "note": "E01未返回卡号"})

# E08: 信用卡挂失
time.sleep(2)
s, r = api("POST", "/api/creditcard/card", token=cc_token,
           body={"ident": "110101199001011234", "op": "LOSS"})
check("E08", "信用卡挂失", {"success": True}, s, r)

# ====== 阶段 F: 系统管理 ======
print("\n" + "=" * 60)
print("阶段 F: 系统管理 (UC-501~504)")
print("=" * 60)

s, r = api("GET", "/api/admin/users", token=admin_token)
check("F01", "用户列表", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/admin/users", token=admin_token,
           body={"employee_no": "TEST01", "name": "测试员", "role": "SAVINGS_CLERK", "password": "123456"})
check("F02", "创建用户", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/admin/users", token=admin_token,
           body={"employee_no": "TEST01", "name": "测试员2", "role": "SAVINGS_CLERK", "password": "123456"})
check("F03", "创建用户-重复工号", {"success": False}, s, r)

s, r = api("GET", "/api/admin/params", token=admin_token)
check("F04", "参数列表", {"success": True}, s, r)

time.sleep(2)
s, r = api("POST", "/api/admin/params", token=admin_token,
           body={"param_key": "WITHDRAW_DAILY_LIMIT", "param_value": "50000"})
check("F05", "修改参数", {"success": True}, s, r)

s, r = api("GET", "/api/admin/audit", token=admin_token)
check("F06", "审计日志查询", {"success": True}, s, r)

# ====== 阶段 G: 跨角色权限 ======
print("\n" + "=" * 60)
print("阶段 G: 跨角色权限")
print("=" * 60)

time.sleep(2)
s, r = api("POST", "/api/loan/apply", token=saver_token,
           body={"ident": "110101199001011234", "loan_type": "个人消费贷",
                 "amount": 10000, "term_months": 6})
check("G01", "储蓄员访问贷款API", {"status": 403}, s, r)

time.sleep(2)
s, r = api("POST", "/api/savings/deposit", token=loan_token,
           body={"ident": "110101199001011234", "amount": 100})
check("G02", "贷款员访问储蓄API", {"status": 403}, s, r)

time.sleep(2)
s, r = api("GET", "/api/admin/users", token=saver_token)
check("G03", "储蓄员访问管理API", {"status": 403}, s, r)

# ====== 阶段 H: 销户 ======
print("\n" + "=" * 60)
print("阶段 H: 销户测试")
print("=" * 60)
time.sleep(2)
s, r = api("POST", "/api/savings/close-account", token=saver_token,
           body={"ident": "110101199001011234"})
# 预期失败：因为有未结清贷款
check("H01", "销户-有未结清贷款/余额", {"success": False}, s, r)

# ====== 输出结果 ======
print("\n\n" + "=" * 60)
print("测试结果汇总")
print("=" * 60)

passed = sum(1 for r in RESULTS if r["result"] == PASS)
failed = sum(1 for r in RESULTS if r["result"] == FAIL)
skipped = sum(1 for r in RESULTS if r["result"] == SKIP)

print(f"\n{'ID':<6} {'结果':<4} {'描述':<35} {'预期':<25} {'实际状态':<8} {'响应摘要'}")
print("-" * 120)
for r in RESULTS:
    note = r.get("note", "")
    body = r.get("body_summary", "")[:80]
    print(f"{r['id']:<6} {r['result']:<4} {r['desc']:<35} {r['expected']:<25} {r['status_code']:<8} {body}{' [' + note + ']' if note else ''}")

print(f"\n{'='*60}")
print(f"通过: {passed}  失败: {failed}  跳过: {skipped}  总计: {len(RESULTS)}")
print(f"{'='*60}")
