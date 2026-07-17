"""登录鉴权：密码哈希校验 + itsdangerous 签名令牌 + 角色装饰器。

令牌用 itsdangerous（Flask 自带依赖）签发，无需额外库。前端登录后把 token
放在 Authorization: Bearer <token> 头里发送。

【答辩讲解】用签名令牌验证身份，令牌里带一个版本号——一改密码或停用账号，版本号自增、旧令牌立刻失效，
不用在服务端存黑名单。每个接口顶上加一行 @require_role(角色) 就完成登录+权限校验。
"""
from functools import wraps

from flask import Blueprint, request, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

import config
import constants as C
from db import get_db
from common import ok, fail, oid, write_audit

bp = Blueprint("auth", __name__)
_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="bank-auth")


def issue_token(user):
    # 令牌内含 token_version：改密/停用后自增使旧令牌失效（无状态吊销）
    return _serializer.dumps({"uid": str(user["_id"]), "tv": user.get("token_version", 0)})


def load_user_from_token(token):
    try:
        data = _serializer.loads(token, max_age=config.TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    _id = oid(data.get("uid"))
    if not _id:
        return None
    u = get_db().user_account.find_one({"_id": _id})
    if not u or u.get("token_version", 0) != data.get("tv", 0):  # 版本不符=已失效
        return None
    return u


def _json():
    """安全取 JSON 请求体：非对象(数组/字符串/数字)一律当空对象，避免 .get 崩 500。"""
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}


def _current_token():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:]
    return request.headers.get("X-Token")


def public_user(u):
    """去掉密码哈希，附带角色标签，返回给前端。"""
    return {
        "id": str(u["_id"]),
        "employee_no": u["employee_no"],
        "name": u["name"],
        "role": u["role"],
        "role_label": C.ROLE_LABELS.get(u["role"], u["role"]),
    }


def require_role(*roles):
    """路由装饰器：校验登录 + 角色。roles 为空表示任意登录用户皆可。"""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            token = _current_token()
            u = load_user_from_token(token) if token else None
            if not u:
                return fail("E-AUTH", "未登录或登录已过期", 401)
            if u["status"] != C.USER_ACTIVE:
                return fail("E-DISABLED", "账号已停用", 403)
            if roles and u["role"] not in roles:
                return fail("E-PERM", "无权限执行该操作", 403)
            g.user = u
            return fn(*a, **kw)
        return wrapper
    return deco


@bp.post("/api/login")
def login():
    data = _json()
    emp = (data.get("employee_no") or "").strip()
    pw = data.get("password") or ""
    u = get_db().user_account.find_one({"employee_no": emp})
    # 统一提示，避免工号枚举
    if not u or not check_password_hash(u["password_hash"], pw):
        return fail("E-1", "工号或密码错误")
    if u["status"] != C.USER_ACTIVE:
        return fail("E-2", "账号已停用，请联系管理员")
    write_audit(get_db(), user_id=u["_id"], action="LOGIN",
                object_type="user_account", object_id=u["_id"], result=C.RESULT_SUCCESS)
    return ok({"token": issue_token(u), "user": public_user(u)}, "登录成功")


@bp.get("/api/me")
@require_role()
def me():
    return ok({"user": public_user(g.user)})


@bp.post("/api/change-password")
@require_role()  # 任意登录用户可修改本人密码
def change_password():
    data = _json()
    old = data.get("old_password") or ""
    new = data.get("new_password") or ""
    u = g.user
    db = get_db()
    if not check_password_hash(u["password_hash"], old):  # E-1 原密码错误
        write_audit(db, user_id=u["_id"], action="CHANGE_PASSWORD", object_type="user_account",
                    object_id=u["employee_no"], result=C.RESULT_FAILURE, detail={"reason": "原密码错误"})
        return fail("E-1", "原密码不正确")
    if len(new) < 6:  # E-REQ 新密码太短
        return fail("E-REQ", "新密码至少 6 位", 400)
    if new == old:  # E-2 与原密码相同
        return fail("E-2", "新密码不能与原密码相同", 400)
    # 改密同时自增 token_version，使其它设备上的旧令牌立即失效；给当前会话签发新令牌
    db.user_account.update_one({"_id": u["_id"]},
                               {"$set": {"password_hash": generate_password_hash(new)},
                                "$inc": {"token_version": 1}})
    write_audit(db, user_id=u["_id"], action="CHANGE_PASSWORD", object_type="user_account",
                object_id=u["employee_no"], result=C.RESULT_SUCCESS)
    return ok({"token": issue_token(db.user_account.find_one({"_id": u["_id"]}))},
              "密码修改成功，其它设备需用新密码重新登录")


@bp.get("/api/my-activity")
@require_role()  # 任意登录用户查询本人经办记录（按 g.user 过滤审计日志）
def my_activity():
    db = get_db()
    logs = list(db.audit_log.find({"user_id": g.user["_id"]}).sort("created_at", -1).limit(200))
    rows = [{"created_at": lg["created_at"].strftime("%Y-%m-%d %H:%M:%S") if lg.get("created_at") else None,
             "action": lg.get("action"), "object_type": lg.get("object_type"),
             "object_id": lg.get("object_id"), "result": lg.get("result")} for lg in logs]
    return ok({"logs": rows, "hint": None if rows else "暂无经办记录"})
