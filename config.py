"""全局配置：从环境变量读取，本地开发时用 .env 文件。"""
import os
from dotenv import load_dotenv

load_dotenv()  # 本地读取 .env；Render 上直接用环境变量，这行无副作用

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "bank_counter")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# 登录令牌有效期（秒），默认 8 小时（一个营业日）
TOKEN_MAX_AGE = int(os.environ.get("TOKEN_MAX_AGE", 8 * 3600))
