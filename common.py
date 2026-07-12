"""公共工具 + 4 个 include 公共用例（UC-INC-1 身份核验、UC-INC-2 账户校验、
UC-INC-3 流水登记、UC-INC-4 审计日志）。所有子系统复用这里的逻辑，避免重复。
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from bson import ObjectId
from bson.decimal128 import Decimal128
from flask import jsonify

from db import get_db, next_seq, gen_no
import constants as C


# ============ 时间 ============
def now():
    """北京时间（naive）。ponytail: Render 服务器为 UTC，这里 +8 让演示时间戳直接是本地时间。"""
    return datetime.utcnow() + timedelta(hours=8)


# ============ 金额（DECIMAL(18,2)）============
def D(x):
    """转成 2 位小数的 Decimal，做金额运算。"""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def D6(x):
    """汇率 6 位小数。"""
    return Decimal(str(x)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def m(x):
    """金额存库：Decimal128，保证精度。"""
    return Decimal128(D(x))


def dec(v):
    """从库读出的值转 Decimal（兼容 Decimal128 / int / float / None）。"""
    if isinstance(v, Decimal128):
        return v.to_decimal()
    return Decimal(str(v if v is not None else 0))


# ============ 统一响应 ============
def ok(data=None, message="操作成功", **extra):
    body = {"success": True, "message": message}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return jsonify(body)


def fail(code, message, http=200):
    """业务替代流(E-x)用 http=200 + success=False；鉴权/参数错误用 4xx。"""
    return jsonify({"success": False, "error": code, "message": message}), http


# ============ 业务编号生成 ============
def new_customer_no(session=None):
    return gen_no("C", "customer_no", 8, session)


def new_account_no(session=None):
    return gen_no("6217", "account_no", 12, session)


def new_debit_card_no(session=None):
    return gen_no("6227", "debit_card_no", 12, session)


def new_credit_card_no(session=None):
    return gen_no("5187", "credit_card_no", 12, session)


def new_contract_no(session=None):
    return gen_no("LN", "contract_no", 10, session)


def new_fx_account_no(session=None):
    return gen_no("FX", "fx_account_no", 10, session)


def new_txn_no(session=None):
    return f"T{now():%Y%m%d}{next_seq('txn_no', session):06d}"


def oid(v):
    """安全转 ObjectId，非法返回 None。"""
    try:
        return ObjectId(v)
    except Exception:
        return None


# ============ UC-INC-1 身份核验 ============
def norm_id(s):
    """证件号归一化：去首尾空格 + 转大写（身份证尾号 x→X）。存/查一律用它，避免大小写不一致匹配失败。"""
    return (s or "").strip().upper()


def validate_id_no(id_type, id_no):
    """校验证件号格式（简化）。返回 (ok, reason, 规范化后的证件号)。"""
    s = norm_id(id_no)
    if not s:
        return False, "证件号不能为空", ""
    if id_type not in C.ID_TYPES:  # 证件类型必须在受支持集合内
        return False, "不支持的证件类型", ""
    if id_type == "身份证":
        # 18 位：前 17 位为半角数字（isascii 排除全角数字），末位数字或 X
        if len(s) != 18 or not (s[:17].isdigit() and s[:17].isascii() and (s[17].isdigit() or s[17] == "X")):
            return False, "身份证号格式非法（应为 18 位数字，末位可为 X）", ""
    elif len(s) < 5 or len(s) > 30:
        return False, "证件号格式非法", ""
    return True, "", s


def validate_phone(phone):
    """校验手机号。留空放行（选填）；非空须为 11 位大陆手机号。返回 (ok, reason, 规范化值)。"""
    p = (phone or "").strip()
    if p == "":
        return True, "", ""
    if not re.fullmatch(r"1[3-9]\d{9}", p):
        return False, "手机号格式非法（应为 11 位大陆手机号）", ""
    return True, "", p


def find_customer(db, *, customer_no=None, id_no=None, phone=None, customer_id=None):
    """按客户号 / 证件号 / 手机号 / _id 定位客户。"""
    if customer_id:
        _id = oid(customer_id)
        return db.customer.find_one({"_id": _id}) if _id else None
    if customer_no:
        return db.customer.find_one({"customer_no": customer_no.strip()})
    if id_no:
        return db.customer.find_one({"id_no": norm_id(id_no)})  # 归一化后匹配，避免尾号 X 大小写不一致
    if phone:
        return db.customer.find_one({"phone": phone.strip()})
    return None


def verify_owner(customer, doc):
    """核对某账户/卡/子户是否属于该客户（归属一致性）。"""
    return customer and doc and doc.get("customer_id") == customer["_id"]


# ============ UC-INC-2 账户校验 ============
def check_account(db, account_no, need_amount=None, session=None):
    """校验储蓄账户状态与余额。返回 (account, error)；error 为 (code, msg) 或 None。"""
    if not account_no or not str(account_no).strip():  # 防止 None.strip() 崩溃
        return None, ("E-NOACC", "未找到账户")
    acc = db.account.find_one({"account_no": account_no.strip()}, session=session)
    if not acc:
        return None, ("E-NOACC", "未找到账户")
    if acc["status"] == C.ACCOUNT_CLOSED:
        return acc, ("E-CLOSED", "账户已销户")
    if acc["status"] == C.ACCOUNT_FROZEN:
        return acc, ("E-FROZEN", "账户已冻结")
    if acc["status"] == C.ACCOUNT_LOST or acc.get("card_status") == C.CARD_LOST:
        return acc, ("E-LOST", "账户/卡片已挂失")
    if need_amount is not None and dec(acc["balance"]) < D(need_amount):
        return acc, ("E-BAL", f"余额不足，当前余额 {dec(acc['balance'])}，缺口 {D(need_amount) - dec(acc['balance'])}")
    return acc, None


# ============ UC-INC-3 流水登记 ============
def write_txn(db, *, business_type, amount, user_id, customer_id=None, account_id=None,
              related_id=None, fx_rate=None, fx_rate_type=None,
              status=C.TXN_STATUS_SUCCESS, session=None):
    """写入 business_transaction，返回流水文档（含 _id、txn_no）。"""
    doc = {
        "txn_no": new_txn_no(session),
        "business_type": business_type,
        "customer_id": customer_id,
        "account_id": account_id,
        "user_id": user_id,
        "related_id": related_id,
        "fx_rate": Decimal128(D6(fx_rate)) if fx_rate is not None else None,
        "fx_rate_type": fx_rate_type,
        "amount": m(amount),
        "status": status,
        "txn_time": now(),
    }
    res = db.business_transaction.insert_one(doc, session=session)
    doc["_id"] = res.inserted_id
    return doc


# ============ UC-INC-4 审计日志 ============
def write_audit(db, *, user_id, action, object_type, object_id, result,
                detail=None, session=None):
    """写入 audit_log。detail 存 JSON/文本。"""
    db.audit_log.insert_one({
        "user_id": user_id,
        "action": action,
        "object_type": object_type,
        "object_id": str(object_id) if object_id is not None else None,
        "result": result,
        "detail": detail,
        "created_at": now(),
    }, session=session)


# ============ 系统参数读取 ============
def get_param(db, key, default=None, session=None):
    p = db.system_param.find_one({"param_key": key}, session=session)
    return p["param_value"] if p else default


def get_param_dec(db, key, default="0", session=None):
    return dec(get_param(db, key, default, session))


# ============ 文档序列化（附中文标签，供前端展示）============
def customer_view(cust):
    if not cust:
        return None
    return {
        "id": str(cust["_id"]),
        "customer_no": cust["customer_no"],
        "name": cust["name"],
        "id_type": cust.get("id_type"),
        "id_no": cust["id_no"],
        "phone": cust.get("phone"),
        "address": cust.get("address"),
        "occupation": cust.get("occupation"),
        "status": cust["status"],
        "status_label": C.CUSTOMER_STATUS_LABEL.get(cust["status"], "未知"),
    }


def account_view(acc, cust=None):
    if not acc:
        return None
    v = {
        "id": str(acc["_id"]),
        "account_no": acc["account_no"],
        "card_no": acc["card_no"],
        "currency": acc.get("currency", "CNY"),
        "balance": float(dec(acc["balance"])),
        "status": acc["status"],
        "status_label": C.ACCOUNT_STATUS_LABEL.get(acc["status"], "未知"),
        "card_status": acc.get("card_status"),
        "card_status_label": C.CARD_STATUS_LABEL.get(acc.get("card_status"), "未知"),
        "customer_id": str(acc["customer_id"]),
    }
    if cust:
        v["customer_name"] = cust["name"]
        v["customer_no"] = cust["customer_no"]
    return v


def txn_view(t):
    return {
        "txn_no": t["txn_no"],
        "business_type": t["business_type"],
        "business_label": C.TXN_TYPE_LABEL.get(t["business_type"], t["business_type"]),
        "amount": float(dec(t["amount"])),
        "fx_rate": float(dec(t["fx_rate"])) if t.get("fx_rate") is not None else None,
        "fx_rate_type": t.get("fx_rate_type"),
        "status": t["status"],
        "status_label": "成功" if t["status"] == C.TXN_STATUS_SUCCESS else "失败",
        "txn_time": t["txn_time"].strftime("%Y-%m-%d %H:%M:%S") if t.get("txn_time") else None,
    }


def parse_date_range(start, end):
    """把 'YYYY-MM-DD' 起止转成 datetime 范围（含当天）。返回 (dt_start, dt_end) 或 None。"""
    from datetime import datetime as _dt
    rng = {}
    try:
        if start:
            rng["$gte"] = _dt.strptime(start.strip(), "%Y-%m-%d")
        if end:
            e = _dt.strptime(end.strip(), "%Y-%m-%d")
            rng["$lte"] = e.replace(hour=23, minute=59, second=59)
    except ValueError:
        return None
    return rng
