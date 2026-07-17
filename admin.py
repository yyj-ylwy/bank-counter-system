"""系统管理子系统 UC-501 ~ UC-504（参与者：系统管理员）。

【答辩讲解】管理员后台四块：用户权限(自锁保护、必须保留一名管理员)、参数维护、日志审计、数据备份恢复。
恢复做得最严——二次确认 + 全程事务 + 记录数和外键完整性校验 + 失败自动回滚。
"""
import hashlib
import math

from flask import Blueprint, request, g, Response
from bson import json_util
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

import constants as C
from db import get_db, run_in_transaction, txn_supported
from auth import require_role
from common import ok, fail, oid, now, dec, m, D, parse_date_range, as_int

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
admin = require_role(C.ROLE_ADMIN)

# 备份/恢复覆盖的集合。含 counters（自增序列），使恢复后业务编号与已恢复数据保持一致，
# 避免继续沿用当前较大的序列值而与旧数据中的编号重复。
BACKUP_COLLECTIONS = [
    "user_account", "customer", "account", "business_transaction", "loan",
    "fx_account", "credit_card", "credit_card_bill", "audit_log", "system_param", "counters",
]
BACKUP_VERSION = "1.0"


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


def user_view(u):
    return {
        "id": str(u["_id"]),
        "employee_no": u["employee_no"],
        "name": u["name"],
        "role": u["role"],
        "role_label": C.ROLE_LABELS.get(u["role"], u["role"]),
        "status": u["status"],
        "status_label": C.USER_STATUS_LABEL.get(u["status"], "未知"),
    }


# ---------- UC-501 用户与权限管理 ----------
@bp.get("/users")
@admin
def list_users():
    db = get_db()
    users = [user_view(u) for u in db.user_account.find().sort("employee_no", 1)]
    return ok({"users": users, "roles": [{"value": r, "label": C.ROLE_LABELS[r]} for r in C.ALL_ROLES]})


@bp.post("/users")
@admin
def create_user():
    d = _body()
    emp = (d.get("employee_no") or "").strip()
    name = (d.get("name") or "").strip()
    role = (d.get("role") or "").strip()
    pw = d.get("password") or ""
    if not emp or not name or not pw:
        return fail("E-REQ", "工号、姓名、密码为必填", 400)
    if len(emp) > 32 or len(name) > 64:  # 长度上限，防超长脏输入
        return fail("E-REQ", "工号或姓名过长", 400)
    if len(pw) < 6:  # 弱口令拦截
        return fail("E-REQ", "密码至少 6 位", 400)
    if role not in C.ALL_ROLES:  # E-2 角色不存在
        return fail("E-2", "角色不存在")
    db = get_db()
    if db.user_account.find_one({"employee_no": emp}):  # E-1 工号重复
        return fail("E-1", "工号已存在，拒绝创建")
    u = {"employee_no": emp, "name": name, "role": role,
         "password_hash": generate_password_hash(pw), "status": C.USER_ACTIVE, "created_at": now()}
    try:
        u["_id"] = db.user_account.insert_one(u).inserted_id
    except DuplicateKeyError:  # 唯一索引兜底并发重复
        return fail("E-1", "工号已存在，拒绝创建")
    _audit(db, "USER_CREATE", "user_account", emp, {"role": role})
    return ok({"user": user_view(u)}, "用户创建成功")


@bp.post("/users/update")
@admin
def update_user():
    d = _body()
    db = get_db()
    u = db.user_account.find_one({"_id": oid(d.get("id"))}) if d.get("id") else \
        db.user_account.find_one({"employee_no": (d.get("employee_no") or "").strip()})
    if not u:
        return fail("E-NOUSER", "用户不存在")
    updates = {}
    if d.get("name"):
        updates["name"] = d["name"].strip()
    if d.get("role"):
        if d["role"] not in C.ALL_ROLES:
            return fail("E-2", "角色不存在")
        updates["role"] = d["role"]
    if "status" in d and d["status"] is not None:
        st = as_int(d["status"])
        if st not in (0, C.USER_ACTIVE):  # 只允许 0停用 / 1启用，杜绝枚举外脏值
            return fail("E-REQ", "状态非法，应为 0（停用）或 1（启用）", 400)
        updates["status"] = st
    if d.get("password"):
        updates["password_hash"] = generate_password_hash(d["password"])
    if not updates:
        return fail("E-REQ", "没有需要修改的字段", 400)
    is_self = (u["_id"] == g.user["_id"])
    if is_self and (("status" in updates and updates["status"] != C.USER_ACTIVE)
                    or ("role" in updates and updates["role"] != C.ROLE_ADMIN)):
        return fail("E-SELF", "不能停用或降低当前登录管理员自己的权限")
    demoting = (u["role"] == C.ROLE_ADMIN and u["status"] == C.USER_ACTIVE
                and (("role" in updates and updates["role"] != C.ROLE_ADMIN)
                     or ("status" in updates and updates["status"] != C.USER_ACTIVE)))
    if demoting and db.user_account.count_documents({"role": C.ROLE_ADMIN, "status": C.USER_ACTIVE}) <= 1:
        return fail("E-LASTADMIN", "系统必须保留至少一名在用管理员，操作被拒绝")
    # 改密码或停用时自增 token_version，令该用户已签发的旧令牌立即失效
    op = {"$set": updates}
    if "password_hash" in updates or updates.get("status") == 0:
        op["$inc"] = {"token_version": 1}
    db.user_account.update_one({"_id": u["_id"]}, op)
    _audit(db, "USER_UPDATE", "user_account", u["employee_no"],
           {k: v for k, v in updates.items() if k != "password_hash"})
    return ok({"user": user_view(db.user_account.find_one({"_id": u["_id"]}))}, "用户更新成功")


# ---------- UC-502 基础参数维护 ----------
@bp.get("/params")
@admin
def list_params():
    db = get_db()
    params = [{"param_type": p.get("param_type"), "param_key": p["param_key"],
               "param_value": p["param_value"],
               "changed_at": p["changed_at"].strftime("%Y-%m-%d %H:%M:%S") if p.get("changed_at") else None}
              for p in db.system_param.find().sort("param_key", 1)]
    # 信用卡四种卡的初始授信额度：有效额度=管理员覆盖值(cc_card_limit)优先，否则卡种规格默认；审批激活时按此额度授信
    overrides = {o["card_type"]: o for o in db.cc_card_limit.find()}
    ct_to_key = {ct: k for k, ct in C.CC_CARD_LIMIT_KEYS.items()}  # 卡种 → 并入「维护参数」的伪键
    card_limits = []
    for ct, spec in C.CARD_SPECS.items():
        ov = overrides.get(ct)
        eff = float(dec(ov["credit_limit"])) if ov and ov.get("credit_limit") is not None \
            else float(dec(spec.get("default_limit", "0")))
        card_limits.append({"card_type": ct, "network": spec.get("network"), "currency": spec.get("currency"),
                            "default_limit": eff, "spec_default": float(dec(spec.get("default_limit", "0"))),
                            "param_key": ct_to_key.get(ct)})
    return ok({"params": params, "card_limits": card_limits})


@bp.post("/params")
@admin
def upsert_param():
    d = _body()
    key = (d.get("param_key") or "").strip()
    value = str(d.get("param_value") if d.get("param_value") is not None else "").strip()
    ptype = (d.get("param_type") or "OTHER").strip()
    if not key or value == "":
        return fail("E-REQ", "参数键和参数值为必填", 400)
    # 卡种初始额度维护（并入「维护参数」）：伪参数键路由到 cc_card_limit 集合，币种由卡种固定不可改
    if key in C.CC_CARD_LIMIT_KEYS:
        card_type = C.CC_CARD_LIMIT_KEYS[key]
        try:
            x = float(value)
        except ValueError:
            return fail("E-1", "初始额度必须是数字", 400)
        if not math.isfinite(x) or x <= 0:
            return fail("E-1", "初始额度必须是正数", 400)
        if x > 1e8:  # 防天文数字
            return fail("E-1", "初始额度过大（上限 1 亿）", 400)
        db = get_db()
        db.cc_card_limit.update_one(
            {"card_type": card_type},
            {"$set": {"card_type": card_type, "credit_limit": m(D(value)),
                      "currency": C.CARD_SPECS[card_type]["currency"],
                      "changed_by": g.user["_id"], "changed_at": now()}},
            upsert=True)
        _audit(db, "CARD_LIMIT_UPDATE", "credit_card", card_type, {"limit": value})
        return ok(message=f"{card_type} 初始额度已更新为 {value}")
    if key not in C.ALLOWED_PARAM_KEYS:  # E-1 只允许维护系统内置参数，杜绝写入无人读取的孤儿键
        return fail("E-1", "未知参数键，只能维护系统内置参数", 400)
    # 白名单内均为数字型参数：校验有限、非负；比例/费率额外限制 <=1（防最低还款>账单、手续费>本金等）
    try:
        x = float(value)
    except ValueError:
        return fail("E-1", "参数值必须是数字")
    if not math.isfinite(x):  # 拦截 nan/inf，避免污染下游金额运算
        return fail("E-1", "参数值必须是有限数字")
    if x < 0:
        return fail("E-1", "该参数不能为负数")
    if key in C.RATE_PARAM_KEYS and x > 1:
        return fail("E-1", "比例/费率应为 0~1 之间的小数")
    db = get_db()
    existing = db.system_param.find_one({"param_key": key})
    eff_type = existing["param_type"] if existing else ptype
    # 已无「每币种买入价/卖出价」参数：全行买卖价由实时中间价按 FX_SPREAD 统一推算（见 forex.quote_from_mid），
    # 故原先的“买入价≤卖出价”一致性校验一并移除——那组参数早已没有任何代码读取，改了也不生效。
    db.system_param.update_one(
        {"param_key": key},
        {"$set": {"param_type": eff_type, "param_value": value,  # 已存在的参数保留原类型，避免被前端覆盖成 OTHER
                  "changed_by": g.user["_id"], "changed_at": now()}},
        upsert=True)
    _audit(db, "PARAM_UPDATE", "system_param", key, {"value": value})
    return ok(message="参数保存成功")


# ---------- UC-503 日志审计与异常追踪（只读，不写自审计）----------
@bp.get("/audit")
@admin
def audit_query():
    db = get_db()
    q = {}
    emp = (request.args.get("employee_no") or "").strip()
    if emp:
        u = db.user_account.find_one({"employee_no": emp})
        if not u:  # 操作人不存在，直接空结果（避免 user_id=None 匹配到系统日志）
            return ok({"logs": [], "hint": "无审计记录"})
        q["user_id"] = u["_id"]
    if request.args.get("action"):
        q["action"] = request.args["action"].strip()
    if request.args.get("object_type"):
        q["object_type"] = request.args["object_type"].strip()
    if request.args.get("result"):
        q["result"] = request.args["result"].strip().upper()
    if request.args.get("only_failure") == "1":
        q["result"] = C.RESULT_FAILURE
    rng = parse_date_range(request.args.get("start"), request.args.get("end"))
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["created_at"] = rng

    total = db.audit_log.count_documents(q)
    logs = list(db.audit_log.find(q).sort("created_at", -1).limit(1000))
    # 附操作人工号/姓名
    uid_map = {u["_id"]: u for u in db.user_account.find()}
    rows = []
    for lg in logs:
        u = uid_map.get(lg.get("user_id"))
        rows.append({
            "created_at": lg["created_at"].strftime("%Y-%m-%d %H:%M:%S") if lg.get("created_at") else None,
            "operator": f"{u['name']}({u['employee_no']})" if u else "-",
            "action": lg.get("action"),
            "object_type": lg.get("object_type"),
            "object_id": lg.get("object_id"),
            "result": lg.get("result"),
            "detail": lg.get("detail"),
        })
    hint = None if rows else "无审计记录"
    if total > 1000:  # E-2 范围过大，提示缩小
        hint = f"匹配 {total} 条，仅显示最近 1000 条，建议缩小时间范围"
    return ok({"logs": rows, "hint": hint})


# ---------- UC-504 数据备份与恢复 ----------
@bp.get("/backup")
@admin
def backup():
    db = get_db()
    dump = {c: list(db[c].find()) for c in BACKUP_COLLECTIONS}
    counts = {c: len(v) for c, v in dump.items()}
    data_str = json_util.dumps(dump, ensure_ascii=False, sort_keys=True)
    checksum = hashlib.md5(data_str.encode("utf-8")).hexdigest()
    payload = {"_meta": {"created_at": now().strftime("%Y-%m-%d %H:%M:%S"),
                         "version": BACKUP_VERSION, "md5": checksum, "collections": counts},
               "data": dump}
    out = json_util.dumps(payload, ensure_ascii=False)
    _audit(db, "BACKUP", "database", "-", {"size": len(out), "md5": checksum, "counts": counts})
    fname = f"bank-backup-{now():%Y%m%d-%H%M%S}.json"
    return Response(out, mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={fname}",
                             "X-Checksum-MD5": checksum})


@bp.post("/restore")
@admin
def restore():
    """从备份 JSON 恢复。需 confirm=true。JSON 从上传文件或请求体读取。"""
    confirm = request.form.get("confirm") or (_body().get("confirm") if not request.files else None)
    raw = None
    if "file" in request.files:
        raw = request.files["file"].read().decode("utf-8")
    else:
        body = request.get_data(as_text=True)
        try:
            import json as _json
            parsed = _json.loads(body)
            confirm = parsed.get("confirm", confirm)
            raw = json_util.dumps(parsed.get("payload")) if parsed.get("payload") is not None else None
        except Exception:
            raw = None
    if not raw:
        return fail("E-REQ", "请上传备份文件（form-data 字段 file）或在 payload 中提交内容", 400)
    if not _truthy(confirm):
        return fail("E-CONFIRM", "恢复为高风险操作，请二次确认（confirm=true）", 400)
    if not txn_supported():  # 破坏性全量操作必须在事务下进行，否则半恢复不可回滚且会谎报回滚
        return fail("E-3", "当前数据库不支持事务（需副本集/Atlas），为避免恢复中途失败导致数据损坏，已拒绝恢复", 400)

    try:
        parsed = json_util.loads(raw)
        data = parsed.get("data")
        meta = parsed.get("_meta") or {}
        assert isinstance(data, dict)
    except Exception:  # E-2 文件损坏/不兼容
        return fail("E-2", "备份文件损坏或格式不兼容，拒绝恢复")
    ver = meta.get("version")
    if ver is not None and str(ver) != BACKUP_VERSION:
        return fail("E-2", f"备份版本 {ver} 与当前系统 {BACKUP_VERSION} 不兼容，拒绝恢复")
    # 先整体校验：任一集合数据非法就拒绝，避免删一半（E-2）
    for c in BACKUP_COLLECTIONS:
        if c in data and not isinstance(data[c], list):
            return fail("E-2", f"备份中集合 {c} 数据格式非法，拒绝恢复")
    for c, n in (meta.get("collections") or {}).items():
        got = 0 if c not in data else (len(data[c]) if isinstance(data[c], list) else None)
        if got is not None and got != n:
            return fail("E-2", f"备份记录数校验失败：集合 {c} 声明 {n} 条、实际 {got} 条，文件可能被截断")
    if meta.get("md5"):
        recomputed = hashlib.md5(json_util.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if recomputed != meta["md5"]:
            return fail("E-2", "备份文件校验和不匹配，可能已被篡改或损坏，拒绝恢复")
    db = get_db()

    def do_restore(s):
        restored = {}
        for c in BACKUP_COLLECTIONS:
            if c in data:
                db[c].delete_many({}, session=s)
                docs = data[c]
                if docs:
                    db[c].insert_many(docs, session=s)
                restored[c] = len(docs)
        # 恢复后必须仍有在用管理员、且当前操作管理员仍存在，否则回滚，避免无人可管理/自锁
        if "user_account" in data:
            if db.user_account.count_documents({"role": C.ROLE_ADMIN, "status": C.USER_ACTIVE}, session=s) == 0:
                raise ValueError("备份中无在用管理员，恢复将导致无人可登录管理，已拒绝")
            if db.user_account.count_documents({"_id": g.user["_id"]}, session=s) == 0:
                raise ValueError("恢复后当前管理员账号不存在，会立即失去管理权限，已拒绝")
        issues = _integrity_scan(db, s)
        if issues:
            raise ValueError("外键完整性校验未通过：" + "；".join(issues[:10]))
        # 恢复审计写在同一事务内
        db.audit_log.insert_one({
            "user_id": g.user["_id"], "action": "RESTORE", "object_type": "database",
            "object_id": "-", "result": C.RESULT_SUCCESS,
            "detail": {"restored": restored, "integrity": "OK"}, "created_at": now()}, session=s)
        return restored

    try:
        # 整个恢复在一个事务里：全部成功才提交，中途失败自动回滚到恢复前（对应 SRS UC-504 E-3）
        restored = run_in_transaction(do_restore)
    except Exception as e:  # noqa: BLE001
        return fail("E-3", f"恢复过程失败，已回滚到恢复前状态：{e}")
    return ok({"restored": restored}, "数据恢复完成（已通过记录数与外键完整性校验）")


def _audit(db, action, object_type, object_id, detail=None):
    db.audit_log.insert_one({
        "user_id": g.user["_id"], "action": action, "object_type": object_type,
        "object_id": str(object_id), "result": C.RESULT_SUCCESS, "detail": detail,
        "created_at": now(),
    })


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


def _integrity_scan(db, s):
    """恢复后核对核心集合的逻辑外键是否都指向存在的主键，返回问题描述列表。"""
    issues = []
    def id_set(coll):
        return {x["_id"] for x in db[coll].find({}, {"_id": 1}, session=s)}
    cust, acc, cc, usr = id_set("customer"), id_set("account"), id_set("credit_card"), id_set("user_account")
    def check(coll, field, ids, name):
        cnt = sum(1 for doc in db[coll].find({}, {field: 1}, session=s)
                  if doc.get(field) is not None and doc.get(field) not in ids)
        if cnt:
            issues.append(f"{coll}.{field} 有 {cnt} 条指向不存在的 {name}")
    check("account", "customer_id", cust, "customer")
    check("loan", "customer_id", cust, "customer")
    check("loan", "account_id", acc, "account")
    check("fx_account", "customer_id", cust, "customer")
    check("fx_account", "base_account_id", acc, "account")
    check("credit_card", "customer_id", cust, "customer")
    check("credit_card_bill", "credit_card_id", cc, "credit_card")
    check("system_param", "changed_by", usr, "user_account")
    # 对账核心表与审计表的外键也要扫，避免残留孤儿引用却报 integrity=OK
    check("business_transaction", "customer_id", cust, "customer")
    check("business_transaction", "account_id", acc, "account")
    check("business_transaction", "user_id", usr, "user_account")
    check("audit_log", "user_id", usr, "user_account")
    return issues
