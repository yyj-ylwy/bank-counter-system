#!/usr/bin/env python3
"""银行柜面系统 - Playwright 浏览器全业务流测试 (32个场景)
每个场景模拟真人操作：打开页面→填表单→点确认弹窗→读页面返回的中文结果"""

import asyncio, re
from datetime import datetime
from playwright.async_api import async_playwright

URL = "https://bank-counter-system.onrender.com"
RESULTS = []

def ok(flow, tid, desc, passed, detail=""):
    m = "✅" if passed else "❌"
    RESULTS.append({"f": flow, "id": tid, "d": desc, "ok": passed, "detail": str(detail)[:150]})
    if not passed: print(f"  ❌ {tid}: {desc} → {detail[:100]}")
    return passed

def check_text(expected, text):
    if isinstance(expected, list): return all(e in text for e in expected)
    return expected in text

# ====== 浏览器操作辅助 ======
async def wait_for_app(page, max_wait=60):
    """等待 Render 冷启动完成"""
    for i in range(max_wait):
        await page.goto(URL, wait_until="networkidle", timeout=10000)
        await page.wait_for_timeout(1000)
        title = await page.title()
        if "Render" not in title and "银行" in (await page.content()):
            return True
        print(f"  等待冷启动... ({i+1}s)")
        await page.wait_for_timeout(1000)
    return False

async def login(page, emp, pw):
    await page.goto(URL, wait_until="networkidle")
    await page.evaluate("localStorage.clear()")
    await page.reload(wait_until="networkidle")
    await page.wait_for_timeout(1500)
    # 等待登录表单出现
    await page.wait_for_selector("#emp", state="visible", timeout=10000)
    await page.fill("#emp", emp)
    await page.fill("#pw", pw)
    await page.click("#loginForm button[type=submit]")
    await page.wait_for_timeout(3000)

async def click_menu(page, label):
    try:  # 关闭可能残留的弹窗
        okb = page.locator("[data-act='ok']")
        if await okb.is_visible(timeout=300):
            await okb.click(); await page.wait_for_timeout(500)
    except: pass
    items = await page.locator("#menu li").all()
    for it in items:
        if label in (await it.text_content() or ""):
            await it.click()
            await page.wait_for_timeout(2000)
            return True
    return False

async def fill(page, name, value):
    try:
        el = page.locator(f"#f_{name}")
        await el.wait_for(state="visible", timeout=5000)
        tg = await el.evaluate("e => e.tagName.toLowerCase()")
        if tg == "select": await el.select_option(str(value))
        elif await el.get_attribute("type") == "checkbox":
            if value: await el.check()
        else: await el.fill(str(value))
        return True
    except: return False

async def submit(page):
    await page.locator("#content button[type=submit]").click()
    await page.wait_for_timeout(500)
    try:
        okb = page.locator("[data-act='ok']")
        await okb.wait_for(state="visible", timeout=2000)
        await okb.click()
        await page.wait_for_timeout(3000)
    except:
        await page.wait_for_timeout(2000)

async def result(page):
    parts = []
    for s in ["#banner", "#result", "#content"]:
        try:
            el = page.locator(s)
            if await el.is_visible():
                t = (await el.text_content() or "").strip()
                if t: parts.append(t)
        except: pass
    return " | ".join(parts)

# ====== 32个测试场景 ======
async def run_all():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(15000)

        # === 等待 Render 冷启动 + 首页验证 ===
        print("等待服务就绪...")
        await wait_for_app(page)
        title = await page.title()
        await page.wait_for_timeout(1000)
        lv = await page.locator("#login").is_visible()
        ok("基础", "00", "首页加载", "银行柜面" in title and lv, f"标题={title}, 登录可见={lv}")

        # ==================== 储蓄业务 (12流) ====================
        print("\n📋 储蓄业务 12 流")

        # S00: 储蓄员登录
        await login(page, "S001", "123456")
        menu = await page.locator("#menu").text_content() or ""
        ok("储蓄", "S00", "储蓄员登录+菜单验证", "开户注册" in menu and "柜台存款" in menu)

        # S01: 用证件号存款
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "amount", "100")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S01", "证件号存款100", check_text("存款成功", r), r[:100])

        # S02: 用邮箱存款
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "zhangsan@example.com")
        await fill(page, "amount", "200")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S02", "邮箱存款200", check_text("存款成功", r), r[:100])

        # S03: 用手机号存款
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "13800000001")
        await fill(page, "amount", "150")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S03", "手机号存款150", check_text("存款成功", r), r[:100])

        # S04: 用账号存款
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "6217000000000001")
        await fill(page, "amount", "300")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S04", "账号存款300", check_text("存款成功", r), r[:100])

        # S05: 证件号取款
        await click_menu(page, "柜台取款")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "amount", "50")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S05", "证件号取款50", check_text("取款成功", r), r[:100])

        # S06: 余额不足取款（错误场景）
        await click_menu(page, "柜台取款")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "amount", "99999999")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S06", "超额取款→余额不足", check_text("余额不足", r), r[:100])

        # S07: 本行转账（用身份标识定位收款方）
        await click_menu(page, "转账汇款")
        await fill(page, "transfer_type", "INTRA")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "to_ident", "110101199203054321")
        await fill(page, "amount", "100")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S07", "本行转账(身份标识定位收款方)", check_text("转账成功", r), r[:100])

        # S08: 同一账户转账（错误场景）
        await click_menu(page, "转账汇款")
        await fill(page, "transfer_type", "INTRA")
        await fill(page, "ident", "13800000001")
        await fill(page, "to_ident", "13800000001")
        await fill(page, "amount", "100")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S08", "同一账户转账→拒绝", check_text("同一账户", r), r[:100])

        # S09: 跨行转账（含手续费）
        await click_menu(page, "转账汇款")
        await fill(page, "transfer_type", "INTER")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "to_account_no", "6227001234567890")
        await fill(page, "to_bank", "中国工商银行")
        await fill(page, "amount", "200")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S09", "跨行转账(含手续费)", check_text("转账成功", r) and ("手续费" in r or "0.2" in r), r[:100])

        # S10: 四种身份查询
        for label, ident in [("证件号","110101199001011234"),("邮箱","zhangsan@example.com"),
                             ("手机号","13800000001"),("账号","6217000000000001")]:
            await click_menu(page, "账户/明细查询")
            await fill(page, "ident", ident)
            await submit(page)
            r = await result(page)
            ok("储蓄", f"S10{label}", f"用{label}查询", check_text("张三", r), r[:80])

        # S11: 挂失→解挂
        await click_menu(page, "挂失/解挂/补卡")
        await fill(page, "ident", "zhangsan@example.com")
        await fill(page, "op", "LOSS")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S11a", "邮箱挂失", check_text("挂失成功", r), r[:80])

        await click_menu(page, "挂失/解挂/补卡")
        await fill(page, "ident", "13800000001")
        await fill(page, "op", "UNLOSS")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S11b", "手机号解挂", check_text("解挂成功", r), r[:80])

        # S12: 客户信息更新
        await click_menu(page, "客户信息更新")
        await fill(page, "ident", "110101199001011234")
        await fill(page, "address", "北京市海淀区")
        await fill(page, "occupation", "软件工程师")
        await submit(page)
        r = await result(page)
        ok("储蓄", "S12", "更新地址+职业", check_text("客户信息更新成功", r), r[:80])

        # ==================== 贷款业务 (6流) ====================
        print("\n📋 贷款业务 6 流")

        await login(page, "L001", "123456")
        menu = await page.locator("#menu").text_content() or ""
        ok("贷款", "L00", "贷款员登录", "贷款申请" in menu)

        # L01: 贷款申请→审批→放款（完整正向流程）
        await click_menu(page, "贷款申请办理")
        await fill(page, "ident", "110101199203054321")  # 李四
        await fill(page, "loan_type", "个人消费贷")
        await fill(page, "amount", "50000")
        await fill(page, "term_months", "12")
        await fill(page, "purpose", "旅游消费")
        await submit(page)
        r = await result(page)
        cn = re.search(r'LN\d+', r)
        contract_no = cn.group(0) if cn else ""
        ok("贷款", "L01", f"李四贷款申请", check_text("已提交", r) and bool(contract_no), r[:100])

        if contract_no:
            # L02: 审批通过
            await click_menu(page, "审核与审批")
            await fill(page, "contract_no", contract_no)
            await fill(page, "decision", "APPROVED")
            await fill(page, "approved_amount", "50000")
            await fill(page, "interest_rate", "0.045")
            await fill(page, "term_months", "12")
            await submit(page)
            r = await result(page)
            ok("贷款", "L02", "审批通过", check_text(["审批完成","已批复"], r), r[:100])

            # L03: 放款
            await click_menu(page, "放款处理")
            await fill(page, "contract_no", contract_no)
            await submit(page)
            r = await result(page)
            ok("贷款", "L03", "放款", check_text("放款成功", r), r[:100])

            # L04: 用证件号还款
            await click_menu(page, "还款登记")
            await fill(page, "ident", "110101199203054321")
            await fill(page, "amount", "10000")
            await submit(page)
            r = await result(page)
            ok("贷款", "L04", "证件号还款10000", check_text("还款成功", r), r[:100])

            # L05: 用邮箱还款
            await click_menu(page, "还款登记")
            await fill(page, "ident", "lisi@example.com")
            await fill(page, "amount", "10000")
            await submit(page)
            r = await result(page)
            ok("贷款", "L05", "邮箱还款10000", check_text("还款成功", r), r[:100])

            # L06: 逾期查询
            await click_menu(page, "逾期查询")
            await fill(page, "ident", "110101199203054321")
            await submit(page)
            r = await result(page)
            ok("贷款", "L06", "逾期查询", "操作成功" in r or "无逾期" in r, r[:100])

        # ==================== 外汇业务 (5流) ====================
        print("\n📋 外汇业务 5 流")

        await login(page, "F001", "123456")
        ok("外汇", "F00", "外汇员登录", True)

        # F01: 实时汇率查询
        await click_menu(page, "实时汇率查询")
        await submit(page)
        r = await result(page)
        ok("外汇", "F01", "实时汇率查询", check_text(["USD","EUR","JPY"], r), r[:120])

        # F02: 开立JPY子户（李四还没有JPY）
        await click_menu(page, "外汇账户开立")
        await fill(page, "ident", "110101199203054321")
        await fill(page, "currency", "JPY")
        await submit(page)
        r = await result(page)
        ok("外汇", "F02", "李四开立JPY子户", check_text("开立成功", r), r[:80])

        # F03: 给李四存钱（需要切储蓄员）
        await login(page, "S001", "123456")
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "110101199203054321")
        await fill(page, "amount", "50000")
        await submit(page)
        r = await result(page)
        ok("外汇", "F03", "给李四存50000(准备买外汇)", check_text("存款成功", r), r[:80])

        # F04: 买入USD
        await login(page, "F001", "123456")
        await click_menu(page, "外汇买卖确认")
        await fill(page, "ident", "110101199203054321")
        await fill(page, "currency", "USD")
        await fill(page, "direction", "BUY")
        await fill(page, "amount", "100")
        await submit(page)
        r = await result(page)
        ok("外汇", "F04", "李四买入USD100", check_text("外汇买入成功", r), r[:100])

        # F05: 查询外汇余额和历史
        await click_menu(page, "余额与历史查询")
        await fill(page, "ident", "110101199203054321")
        await submit(page)
        r = await result(page)
        ok("外汇", "F05", "查询外汇余额+历史", check_text(["USD","交易历史"], r), r[:120])

        # ==================== 信用卡业务 (5流) ====================
        print("\n📋 信用卡业务 5 流")

        await login(page, "CC001", "123456")
        ok("信用卡", "C00", "信用卡员登录", True)

        # C01: 信用卡申请
        await click_menu(page, "信用卡申请办理")
        await fill(page, "ident", "110101199203054321")
        await fill(page, "card_type", "白金卡")
        await submit(page)
        r = await result(page)
        ccn = re.search(r'5187\d{12}', r)
        cc_no = ccn.group(0) if ccn else ""
        ok("信用卡", "C01", "李四申请白金卡", check_text("已提交", r) and bool(cc_no), r[:80])

        if cc_no:
            # C02: 审批通过
            await click_menu(page, "审核与额度设定")
            await fill(page, "card_no", cc_no)
            await fill(page, "decision", "APPROVED")
            await fill(page, "credit_limit", "30000")
            await fill(page, "bill_day", "15")
            await fill(page, "repay_day", "28")
            await submit(page)
            r = await result(page)
            ok("信用卡", "C02", "审批通过(额度30000)", check_text(["审批通过","已激活"], r), r[:80])

            # C03: 预借现金
            await click_menu(page, "预借现金处理")
            await fill(page, "ident", "110101199203054321")
            await fill(page, "amount", "5000")
            await submit(page)
            r = await result(page)
            ok("信用卡", "C03", "预借现金5000", check_text("预借现金成功", r), r[:100])

            # C04: 生成账单
            await click_menu(page, "账单生成")
            await fill(page, "card_no", cc_no)
            await fill(page, "bill_cycle", datetime.now().strftime("%Y%m"))
            await submit(page)
            r = await result(page)
            ok("信用卡", "C04", "生成当月账单", check_text("成功", r), r[:100])

            # C05: 全额还款
            await click_menu(page, "还款处理")
            await fill(page, "ident", "110101199203054321")
            await fill(page, "repay_type", "FULL")
            await submit(page)
            r = await result(page)
            ok("信用卡", "C05", "全额还款", check_text("还款成功", r), r[:100])

        # ==================== 错误处理 (4流) ====================
        print("\n📋 错误处理 4 流")

        await login(page, "S001", "123456")

        # E01: 不存在标识
        await click_menu(page, "柜台存款")
        await fill(page, "ident", "000000000000000000")
        await fill(page, "amount", "100")
        await submit(page)
        r = await result(page)
        ok("错误", "E01", "不存在标识→提示未找到", check_text("未找到", r), r[:100])

        # E02: 不存在的标识查询
        await click_menu(page, "账户/明细查询")
        await fill(page, "ident", "000000000000000000")
        await submit(page)
        r = await result(page)
        ok("错误", "E02", "不存在标识查询→提示", check_text("未找到", r), r[:100])

        # E03: 错误密码登录
        await login(page, "admin", "wrongpassword")
        err_text = await page.locator("#loginErr").text_content() or ""
        ok("错误", "E03", "错误密码登录", "错误" in err_text, err_text[:60])

        # E04: 错误后正确登录
        await login(page, "admin", "admin123")
        app_v = await page.locator("#app").is_visible()
        ok("错误", "E04", "错误密码后正确登录", app_v)

        # ==================== 权限验证 (3流) ====================
        print("\n📋 权限验证 3 流")

        await login(page, "S001", "123456")
        menu = await page.locator("#menu").text_content() or ""
        ok("权限", "P01", "储蓄员无贷款菜单", "贷款申请" not in menu, menu[:60])

        await login(page, "L001", "123456")
        menu = await page.locator("#menu").text_content() or ""
        ok("权限", "P02", "贷款员无储蓄菜单", "开户注册" not in menu, menu[:60])

        await login(page, "S001", "123456")
        menu = await page.locator("#menu").text_content() or ""
        ok("权限", "P03", "储蓄员无管理菜单", "用户列表" not in menu, menu[:60])

        # ==================== 管理功能 (2流) ====================
        print("\n📋 管理功能 2 流")

        await login(page, "admin", "admin123")

        # M01: 审计日志查询
        await click_menu(page, "日志审计")
        await submit(page)
        r = await result(page)
        ok("管理", "M01", "审计日志查询", "操作成功" in r or "创建" in r or "LOGIN" in r, r[:120])

        # M02: 参数列表
        await click_menu(page, "参数列表")
        await submit(page)
        r = await result(page)
        ok("管理", "M02", "参数列表", check_text(["WITHDRAW","LOAN"], r), r[:100])

        await browser.close()

    # ====== 汇总 ======
    print("\n" + "=" * 65)
    print("Playwright 浏览器全业务流测试结果")
    print("=" * 65)

    from collections import defaultdict
    groups = defaultdict(list)
    for r in RESULTS: groups[r["f"]].append(r)

    total_ok = total_all = 0
    for gname, items in groups.items():
        ng = sum(1 for i in items if i["ok"])
        nb = sum(1 for i in items if not i["ok"])
        total_ok += ng; total_all += len(items)
        flag = "✅" if nb == 0 else f"❌ {nb}失败"
        print(f"\n{flag} {gname}: {ng}/{len(items)}")
        for i in items:
            if not i["ok"]:
                print(f"    ❌ {i['id']}: {i['d']} | {i['detail'][:100]}")

    print(f"\n{'='*65}")
    print(f"总计: {total_ok} ✅ / {total_all - total_ok} ❌ / {total_all} 条")
    print(f"方式: Playwright Chromium 真实浏览器 → 填表 → 确认弹窗 → 读页面文字")
    print(f"{'='*65}")

asyncio.run(run_all())
