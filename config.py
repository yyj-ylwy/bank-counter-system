"""全局配置：从环境变量读取，本地开发时用 .env 文件。"""
import os
from dotenv import load_dotenv

load_dotenv()  # 本地读取 .env；Render 上直接用环境变量，这行无副作用

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "bank_counter")

# 令牌签名密钥：绝不回退到公开已知的固定默认值（否则可被离线伪造任意用户令牌）。
# 未配置时随机生成一个（安全但令牌不跨重启存活）——生产务必在环境变量里配置固定值。
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    print("[config] 警告：未配置 SECRET_KEY，已随机生成（重启后登录令牌失效）。生产请在环境变量配置固定 SECRET_KEY。")

# 登录令牌有效期（秒），默认 8 小时（一个营业日）
TOKEN_MAX_AGE = int(os.environ.get("TOKEN_MAX_AGE", 8 * 3600))

# Alpha Vantage 实时汇率：默认内置一个可用 Key，开箱即用；如需替换在环境变量 ALPHAVANTAGE_API_KEY 覆盖即可。
# 注意：为免费额度 Key（约 25 次/天，无计费、无敏感权限），失效可在 alphavantage.co 免费重新申请。
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "QK4RDFO3T79612H3")
