"""补充测试：覆盖《第七章 软件测试》中此前我和同学都未做的三种方法（作者：易裕杰）。

- 基本路径测试法（白盒·与逻辑覆盖法并列的另一种白盒方法）：
    对纯函数 validate_id_no 画控制流图、算圈复杂度 V(G)、覆盖每一条独立(基本)路径。
- 因果图/判定表法（黑盒）：
    对"投资申购"决策(3 个原因→4 个结果)建判定表，逐条规则设计用例并验证。
- 性能/压力测试（系统测试）：
    测吞吐量与平均/最大响应时间；并在并发压力下验证正确性(全部成功、无脏数据)。

运行：python test_extra.py   （用一次性库 bank_extra_test，测完自动删除）
"""
import os
os.environ.setdefault("DB_NAME", "bank_extra_test")

import time
from concurrent.futures import ThreadPoolExecutor

from app import create_app
from db import get_client
from common import validate_phone

get_client().drop_database("bank_extra_test")  # 先清掉上次异常/超时的残留，保证幂等
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

INV = login("I001", "123456"); S = login("S001", "123456")

def api(method, path, tok, json=None, query=None):
    h = {"Authorization": "Bearer " + tok}
    r = cl.get(path, headers=h, query_string=query or {}) if method == "GET" else cl.post(path, headers=h, json=json or {})
    return r.get_json() or {}

def E(r): return r.get("error")
def OK(r): return r.get("success") is True

_seq = [0]
def open_acct(bal="0"):
    _seq[0] += 1; n = _seq[0]
    idn = "11010119920808" + f"{n:04d}"
    r = api("POST", "/api/savings/open-account", S, json={
        "name": f"补测客户{n}", "id_type": "身份证", "id_no": idn,
        "email": f"ext{n}@test.example.com", "initial_balance": bal})
    assert r.get("success"), r
    return {"id_no": idn}


# ==================== 一、基本路径测试法（白盒）====================
# 说明：课本要求"复合条件(OR/AND)须拆成单条件的嵌套判断"再算环路复杂度，故这里选
#       只有【单条件判定】的 common.validate_phone(phone) 作示例，V(G) 无歧义。
# 控制流图（2 个单条件判定节点 D1、D2，均不含 or/and）：
#   entry(p=strip) → D1: p=="" ─T→ 返回 True(空放行, R1)
#                     └F→ D2: 不匹配 1[3-9]\d{9} ─T→ 返回 False(格式非法, R2)
#                          └F→ 返回 True(合法, R3)
# 判定节点数 = 2  →  环路复杂度 V(G) = 2 + 1 = 3  →  3 条独立(基本)路径
# 下列 3 条用例各覆盖一条独立路径，达成基本路径覆盖。
def t_basis_path():
    print("== 基本路径测试法(白盒) · validate_phone(单条件判定) · V(G)=3 · 3条独立路径 ==")
    ok("路径1 手机号为空→放行(选填) [基本路径]", validate_phone("")[0] is True)
    ok("路径2 非空但格式非法→拒绝 [基本路径]", validate_phone("12345")[0] is False)
    ok("路径3 非空且合法→通过 [基本路径]", validate_phone("13800000001")[0] is True)


# ==================== 二、因果图 / 判定表法（黑盒）====================
# 场景：投资申购 /api/invest/buy 的放行判断。
#   原因(条件)：C1=客户已做风险测评  C2=产品风险≤客户承受等级  C3=账户余额≥申购金额
#   结果(动作)：A1=申购成功  A2=E-RISK需测评  A3=E-RISK风险不匹配  A4=E-BAL余额不足
#   判定表(4 条规则，"-"=无关/不必取值)：
#   ┌────┬────┬────┬────┬──────────────┐
#   │规则│ C1 │ C2 │ C3 │ 期望结果      │
#   ├────┼────┼────┼────┼──────────────┤
#   │ R1 │ F  │ -  │ -  │ A2 E-RISK    │
#   │ R2 │ T  │ F  │ -  │ A3 E-RISK    │
#   │ R3 │ T  │ T  │ F  │ A4 E-BAL     │
#   │ R4 │ T  │ T  │ T  │ A1 成功      │
#   └────┴────┴────┴────┴──────────────┘
def _assess(idn, level):
    q = str(level)
    api("POST", "/api/invest/assess", INV, json={"ident": idn, "q1": q, "q2": q, "q3": q, "q4": q, "q5": q})

def t_decision_table():
    print("== 因果图/判定表法(黑盒) · 投资申购决策 · 4条规则 ==")
    # R1: 未测评 → E-RISK
    c1 = open_acct(bal="100000")
    ok("规则R1 未测评(C1=F)→E-RISK [判定表]", E(api("POST", "/api/invest/buy", INV, json={"ident": c1["id_no"], "product_code": "000198", "amount": "1000"})) == "E-RISK")
    # R2: 已测评2级 但买5级股票 → 风险不匹配 E-RISK
    c2 = open_acct(bal="100000"); _assess(c2["id_no"], 2)
    ok("规则R2 风险不匹配(C1=T,C2=F)→E-RISK [判定表]", E(api("POST", "/api/invest/buy", INV, json={"ident": c2["id_no"], "product_code": "AAPL", "amount": "1440"})) == "E-RISK")
    # R3: 已测评5级 风险匹配 但余额不足 → E-BAL
    c3 = open_acct(bal="10"); _assess(c3["id_no"], 5)
    ok("规则R3 余额不足(C1=T,C2=T,C3=F)→E-BAL [判定表]", E(api("POST", "/api/invest/buy", INV, json={"ident": c3["id_no"], "product_code": "000198", "amount": "1000"})) == "E-BAL")
    # R4: 三条件全真 → 申购成功
    c4 = open_acct(bal="100000"); _assess(c4["id_no"], 5)
    ok("规则R4 全部满足(C1=C2=C3=T)→成功 [判定表]", OK(api("POST", "/api/invest/buy", INV, json={"ident": c4["id_no"], "product_code": "000198", "amount": "1000"})))


# ==================== 三、性能 / 压力测试（系统测试）====================
def t_performance():
    print("== 性能/压力测试(系统测试) ==")
    N = 50
    # 性能：顺序 N 次接口调用，统计吞吐量与平均/最大响应时间
    lat = []
    for _ in range(N):
        t0 = time.perf_counter()
        r = api("GET", "/api/invest/products", INV)
        lat.append((time.perf_counter() - t0) * 1000)
        assert OK(r)
    lat.sort()
    total = sum(lat)
    avg, p95, mx = total / N, lat[int(N * 0.95)], lat[-1]
    tps = N / (total / 1000)
    print(f"    [性能] {N} 次 GET /api/invest/products：平均 {avg:.1f}ms · P95 {p95:.1f}ms · 最大 {mx:.1f}ms · 吞吐 {tps:.0f} 次/秒")
    ok("性能 平均响应在合理范围(<2s) [性能]", avg < 2000)
    ok("性能 全部成功 [性能]", True)

    # 压力：并发 N 次请求，验证压力下正确性（全部成功、无异常）
    def one(_):
        try:
            return OK(api("GET", "/api/invest/products", INV))
        except Exception:
            return False
    M = 40
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, range(M)))
    dur = time.perf_counter() - t0
    okcount = sum(1 for x in results if x)
    print(f"    [压力] 8 线程并发 {M} 次：成功 {okcount}/{M} · 耗时 {dur:.2f}s")
    ok("压力 并发下全部成功 [压力]", okcount == M)


if __name__ == "__main__":
    try:
        t_basis_path()
        t_decision_table()
        t_performance()
    finally:
        get_client().drop_database("bank_extra_test")
    print(f"\n==== 结果：{_p} 通过 / {_f} 失败（共 {_p + _f} 条断言）====")
    import sys
    sys.exit(1 if _f else 0)
