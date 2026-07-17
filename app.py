"""Flask 应用入口。

- 静态前端从 static/ 目录以同源方式提供（无需 CORS）。
- 自定义 JSON 编码器，让 Mongo 的 ObjectId / datetime / Decimal128 能直接序列化。
- 启动时建索引 + 幂等种子数据。

【答辩讲解】应用工厂：把 7 个业务蓝图拼装起来、启动时自动建索引和灌种子数据。关键点：自定义 JSON 编码器
让 MongoDB 的对象ID/时间/金额自动转成前端能读的格式；统一错误处理——用户乱输数字返回 400，其它意外异常返回 500 但不泄露堆栈。
"""
from datetime import datetime
from decimal import Decimal

from bson import ObjectId
from bson.decimal128 import Decimal128
from flask import Flask, send_from_directory
from flask.json.provider import DefaultJSONProvider

import db
from seed import run_seed

# 各业务子系统蓝图
from auth import bp as auth_bp
from savings import bp as savings_bp
from loan import bp as loan_bp
from forex import bp as forex_bp
from creditcard import bp as creditcard_bp
from invest import bp as invest_bp
from admin import bp as admin_bp


class MongoJSONProvider(DefaultJSONProvider):
    """让 jsonify 直接吃 Mongo 文档。"""
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, Decimal128):
            return float(o.to_decimal())
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.json = MongoJSONProvider(app)
    # 静态文件(operations.js/app.js/style.css)不做长期缓存：浏览器每次校验，
    # 避免每次更新前端后用户看到旧页面还要手动硬刷新（Cache-Control: max-age=0）。
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    for bp in (auth_bp, savings_bp, loan_bp, forex_bp, creditcard_bp, invest_bp, admin_bp):
        app.register_blueprint(bp)

    # 用户输入的金额/数字非法（如 "abc"）统一返回 400，而不是 500 崩溃
    from decimal import InvalidOperation
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(InvalidOperation)
    @app.errorhandler(ValueError)
    def _bad_number(e):
        print(f"[input] 非法数字输入: {e}")
        return {"success": False, "error": "E-NUM", "message": "金额或数字格式非法"}, 400

    # 兜底：任何未预料的异常都返回统一 500，不外泄堆栈/源码（防信息泄露）
    @app.errorhandler(Exception)
    def _internal(e):
        if isinstance(e, HTTPException):
            return e  # 404/405 等按原样返回
        import traceback
        traceback.print_exc()
        return {"success": False, "error": "E-SYS", "message": "系统繁忙，请稍后重试"}, 500

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        ok = db.ping()
        return ({"ok": ok}, 200 if ok else 503)  # DB 不可达返回 503，便于健康探针识别

    # 启动初始化：建索引 + 种子数据（幂等，重复键异常忽略以防多进程竞态）
    with app.app_context():
        try:
            db.ensure_indexes()
            run_seed()
        except Exception as e:  # noqa: BLE001 - 启动初始化失败不应阻止服务起来
            print(f"[startup] 初始化警告: {e}")

    return app


app = create_app()

if __name__ == "__main__":
    import os
    # 生产默认关闭 debug（避免交互式栈/源码泄露）；本地调试设 FLASK_DEBUG=1
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
