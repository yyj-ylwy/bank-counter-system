"""登录鉴权：密码哈希校验 + itsdangerous 签名令牌 + 角色装饰器。

令牌用 itsdangerous（Flask 自带依赖）签发，无需额外库。前端登录后把 token
放在 Authorization: Bearer <token> 头里发送。
"""
from functools import wraps

from flask import Blueprint, request, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

import config
import constants as C
from db import get_db
from common import ok, fail, oid, write_audit

bp = Blueprint("auth", __name__)
_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="bank-auth")


def issue_token(user):
    return _serializer.dumps({"uid": str(user["_id"])})


def load_user_from_token(token):
    try:
        data = _serializer.loads(token, max_age=config.TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    _id = oid(data.get("uid"))
    return get_db().user_account.find_one({"_id": _id}) if _id else None


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
    data = request.get_json(force=True, silent=True) or {}
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
