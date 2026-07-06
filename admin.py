"""系统管理子系统 UC-501 ~ UC-504（参与者：系统管理员）。"""
import hashlib

from flask import Blueprint, request, g, Response
from bson import json_util
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import ok, fail, oid, now, dec, parse_date_range

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
admin = require_role(C.ROLE_ADMIN)

# 备份/恢复覆盖的业务集合（不含 counters，避免恢复后编号回退产生冲突时重建）
BACKUP_COLLECTIONS = [
    "user_account", "customer", "account", "business_transaction", "loan",
    "fx_account", "credit_card", "credit_card_bill", "audit_log", "system_param", "counters",
]


def _body():
    return request.get_json(force=True, silent=True) or {}


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
        updates["status"] = int(d["status"])  # 1启用 0停用
    if d.get("password"):
        updates["password_hash"] = generate_password_hash(d["password"])
    if not updates:
        return fail("E-REQ", "没有需要修改的字段", 400)
    db.user_account.update_one({"_id": u["_id"]}, {"$set": updates})
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
    return ok({"params": params})


@bp.post("/params")
@admin
def upsert_param():
    d = _body()
    key = (d.get("param_key") or "").strip()
    value = str(d.get("param_value") if d.get("param_value") is not None else "").strip()
    ptype = (d.get("param_type") or "OTHER").strip()
    if not key or value == "":
        return fail("E-REQ", "参数键和参数值为必填", 400)
    # E-1 非法值（利率/限额/汇率必须为非负数）
    if ptype in ("RATE", "LIMIT", "FX_RATE") or key.startswith("FX_"):
        try:
            if float(value) < 0:
                return fail("E-1", "该参数不能为负数")
        except ValueError:
            return fail("E-1", "参数值必须是数字")
    db = get_db()
    db.system_param.update_one(
        {"param_key": key},
        {"$set": {"param_type": ptype, "param_value": value,
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
    data = json_util.dumps({"_meta": {"created_at": now().strftime("%Y-%m-%d %H:%M:%S"),
                                      "collections": {c: len(v) for c, v in dump.items()}},
                            "data": dump}, ensure_ascii=False)
    checksum = hashlib.md5(data.encode("utf-8")).hexdigest()
    _audit(db, "BACKUP", "database", "-",
           {"size": len(data), "md5": checksum,
            "counts": {c: len(v) for c, v in dump.items()}})
    fname = f"bank-backup-{now():%Y%m%d-%H%M%S}.json"
    return Response(data, mimetype="application/json",
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
    if not confirm:
        return fail("E-CONFIRM", "恢复为高风险操作，请二次确认（confirm=true）", 400)

    try:
        parsed = json_util.loads(raw)
        data = parsed.get("data")
        assert isinstance(data, dict)
    except Exception:  # E-2 文件损坏/不兼容
        return fail("E-2", "备份文件损坏或格式不兼容，拒绝恢复")

    db = get_db()
    # 先整体校验：任一集合数据非法就拒绝，避免删一半（E-2）
    for c in BACKUP_COLLECTIONS:
        if c in data and not isinstance(data[c], list):
            return fail("E-2", f"备份中集合 {c} 数据格式非法，拒绝恢复")

    def do_restore(s):
        restored = {}
        for c in BACKUP_COLLECTIONS:
            if c in data:
                db[c].delete_many({}, session=s)
                docs = data[c]
                if docs:
                    db[c].insert_many(docs, session=s)
                restored[c] = len(docs)
        # 恢复审计写在同一事务内
        db.audit_log.insert_one({
            "user_id": g.user["_id"], "action": "RESTORE", "object_type": "database",
            "object_id": "-", "result": C.RESULT_SUCCESS, "detail": {"restored": restored},
            "created_at": now()}, session=s)
        return restored

    try:
        # 整个恢复在一个事务里：全部成功才提交，中途失败自动回滚到恢复前（对应 SRS UC-504 E-3）
        restored = run_in_transaction(do_restore)
    except Exception as e:  # noqa: BLE001
        return fail("E-3", f"恢复过程失败，已回滚到恢复前状态：{e}")
    return ok({"restored": restored}, "数据恢复完成")


def _audit(db, action, object_type, object_id, detail=None):
    db.audit_log.insert_one({
        "user_id": g.user["_id"], "action": action, "object_type": object_type,
        "object_id": str(object_id), "result": C.RESULT_SUCCESS, "detail": detail,
        "created_at": now(),
    })
