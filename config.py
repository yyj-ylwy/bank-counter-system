"""全局配置：从环境变量读取，本地开发时用 .env 文件。"""
import os
from dotenv import load_dotenv

load_dotenv()  # 本地读取 .env；Render 上直接用环境变量，这行无副作用

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "bank_counter")

# 令牌签名密钥：用于给登录令牌签名/验签（见 auth.py，itsdangerous 用它签发 Bearer 令牌）。
# 谁掌握这把密钥，谁就能伪造出"看起来合法"的任意用户令牌，因此取值有两条铁律：
#   1) 绝不回退到写死在源码里的固定默认值——代码一旦开源，默认值人人可见，可被离线伪造 admin 令牌免密登录。
#   2) 环境变量没配时，就用 secrets 随机生成一把（安全，但只存在内存里、不落盘）。
# 代价：随机密钥"不跨重启存活"——进程一重启就换新密钥，旧令牌全部验签失败、所有人需重新登录。
# 所以生产务必在环境变量里配置一个固定的 SECRET_KEY：既保密（不进源码），又稳定
#   （重启不掉线、多实例部署时各实例用同一把密钥，令牌才能互相验签通过）。
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    print("[config] 警告：未配置 SECRET_KEY，已随机生成（重启后登录令牌失效）。生产请在环境变量配置固定 SECRET_KEY。")

# 登录令牌有效期（秒），默认 8 小时（一个营业日）
TOKEN_MAX_AGE = int(os.environ.get("TOKEN_MAX_AGE", 8 * 3600))

# Alpha Vantage 实时汇率：默认内置一个可用 Key，开箱即用；如需替换在环境变量 ALPHAVANTAGE_API_KEY 覆盖即可。
# 注意：为免费额度 Key（约 25 次/天，无计费、无敏感权限），失效可在 alphavantage.co 免费重新申请。
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "3INUTB9RNNBYG5VT")
