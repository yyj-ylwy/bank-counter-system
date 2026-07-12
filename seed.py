"""幂等种子数据：首次启动自动创建管理员、各角色演示业务员、系统参数和少量演示客户。

设计为幂等：已存在则跳过，可安全地在每次启动时调用。
"""
from werkzeug.security import generate_password_hash

import constants as C
from db import get_db
from common import (m, D, now, write_txn, new_customer_no, new_account_no, new_debit_card_no)

# 演示账号（README 里公布）。生产环境请让管理员改密码。
DEMO_USERS = [
    ("admin", "系统管理员", C.ROLE_ADMIN, "admin123"),
    ("S001", "储蓄业务员小储", C.ROLE_SAVINGS, "123456"),
    ("L001", "贷款业务员小贷", C.ROLE_LOAN, "123456"),
    ("F001", "外汇业务员小汇", C.ROLE_FOREX, "123456"),
    ("CC001", "信用卡业务员小信", C.ROLE_CREDIT, "123456"),
]

# 系统参数默认值（管理员可在系统管理里修改，无需改代码）
DEFAULT_PARAMS = [
    # 外汇牌价不再落种子兜底：一律按需从 Alpha Vantage 实时拉取、缓存 30 分钟（见 forex.refresh_rates）。
    ("RATE", C.P_LOAN_RATE, "0.0435"),
    ("RATE", C.P_LOAN_OVERDUE_RATE, "0.0005"),
    ("LIMIT", C.P_WITHDRAW_DAILY_LIMIT, "50000"),
    ("RATE", C.P_TRANSFER_FEE_RATE, "0.001"),
    ("LIMIT", C.P_CC_LIMIT_MAX, "50000"),
    ("RATE", C.P_CC_MIN_REPAY_RATE, "0.10"),
    ("RATE", C.P_CC_CASH_FEE_RATE, "0.01"),
    ("LIMIT", C.P_CC_CASH_DAILY_LIMIT, "20000"),
    ("RATE", C.P_FX_SPREAD, "0.003"),   # 外汇挂牌点差 0.3%
]

DEMO_CUSTOMERS = [
    ("张三", "身份证", "110101199001011234", "13800000001", "zhangsan@example.com", "10000.00"),
    ("李四", "身份证", "110101199203054321", "13800000002", "lisi@example.com", "5000.00"),
]


def run_seed():
    db = get_db()

    # --- 用户 ---
    if db.user_account.count_documents({}) == 0:
        for emp, name, role, pw in DEMO_USERS:
            db.user_account.insert_one({
                "employee_no": emp,
                "name": name,
                "role": role,
                "password_hash": generate_password_hash(pw),
                "status": C.USER_ACTIVE,
                "created_at": now(),
            })
        print(f"[seed] 已创建 {len(DEMO_USERS)} 个演示用户")

    admin = db.user_account.find_one({"role": C.ROLE_ADMIN})
    admin_id = admin["_id"] if admin else None

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
