"""系统常量：角色、状态枚举、业务类型、系统参数键。集中定义，便于阅读和修改。

字段命名与《需求规格说明书》3.3 的数据库表保持一致，方便对照。
"""

# ---- 角色（对应 5 类参与者 + 系统内部 self）----
ROLE_SAVINGS = "SAVINGS_CLERK"       # 储蓄业务员
ROLE_LOAN = "LOAN_CLERK"             # 贷款业务员
ROLE_FOREX = "FOREX_CLERK"           # 外汇业务员
ROLE_CREDIT = "CREDIT_CARD_CLERK"    # 信用卡业务员
ROLE_INVEST = "INVEST_CLERK"         # 理财业务员
ROLE_ADMIN = "ADMIN"                 # 系统管理员

ALL_ROLES = [ROLE_SAVINGS, ROLE_LOAN, ROLE_FOREX, ROLE_CREDIT, ROLE_INVEST, ROLE_ADMIN]

ROLE_LABELS = {
    ROLE_SAVINGS: "储蓄业务员",
    ROLE_LOAN: "贷款业务员",
    ROLE_FOREX: "外汇业务员",
    ROLE_CREDIT: "信用卡业务员",
    ROLE_INVEST: "理财业务员",
    ROLE_ADMIN: "系统管理员",
}

# ---- 用户状态 ----
USER_ACTIVE = 1
USER_DISABLED = 0

# ---- 客户状态 ----
CUSTOMER_NORMAL = 1
CUSTOMER_BLACKLIST = 0

# ---- 储蓄账户状态（account.status）----
ACCOUNT_NORMAL = 1
ACCOUNT_LOST = 2      # 挂失
ACCOUNT_FROZEN = 3    # 冻结
ACCOUNT_CLOSED = 4    # 销户

# ---- 卡状态（account.card_status）----
CARD_NORMAL = 1
CARD_LOST = 2         # 挂失
CARD_INVALID = 3      # 失效

# ---- 外汇子户状态（fx_account.status）----
FX_NORMAL = 1
FX_FROZEN = 2
FX_CLOSED = 3

# ---- 贷款状态机：PENDING->APPROVED->ACTIVE->PAID_OFF/OVERDUE ----
LOAN_PENDING = "PENDING"       # 待审核
LOAN_APPROVED = "APPROVED"     # 已批复待放款
LOAN_REJECTED = "REJECTED"     # 拒绝
LOAN_SUPPLEMENT = "SUPPLEMENT" # 待补件
LOAN_ACTIVE = "ACTIVE"         # 已放款存续
LOAN_PAID_OFF = "PAID_OFF"     # 已结清
LOAN_OVERDUE = "OVERDUE"       # 逾期

# ---- 信用卡状态：PENDING->ACTIVE/FROZEN/REJECTED ----
CC_PENDING = "PENDING"
CC_ACTIVE = "ACTIVE"
CC_FROZEN = "FROZEN"
CC_LOST = "LOST"
CC_REJECTED = "REJECTED"
CC_INVALID = "INVALID"   # 补卡后原卡失效
# 非终态卡集合：每人每种卡在这些状态下最多持有一张，据此可凭「身份+卡种」唯一定位一张卡
CC_NON_TERMINAL = [CC_PENDING, CC_ACTIVE, CC_FROZEN, CC_LOST]

# ---- 账单状态 ----
BILL_UNPAID = "UNPAID"
BILL_PARTIAL = "PARTIAL"
BILL_PAID = "PAID"

# ---- 业务流水类型（business_transaction.business_type）----
TXN_OPEN = "OPEN_ACCOUNT"               # 开户
TXN_DEPOSIT = "DEPOSIT"                 # 存款
TXN_WITHDRAW = "WITHDRAW"               # 取款
TXN_TRANSFER_OUT = "TRANSFER_OUT"       # 转账转出
TXN_TRANSFER_IN = "TRANSFER_IN"         # 转账转入
TXN_TRANSFER_FEE = "TRANSFER_FEE"       # 跨行手续费
TXN_CLOSE_ACCOUNT = "CLOSE_ACCOUNT"     # 销户
TXN_LOAN_DISBURSE = "LOAN_DISBURSE"     # 放款
TXN_LOAN_REPAY = "LOAN_REPAY"           # 贷款还款
TXN_FX_BUY = "FX_BUY"                   # 客户买入外币
TXN_FX_SELL = "FX_SELL"                 # 客户卖出外币
TXN_CC_REPAY = "CC_REPAY"               # 信用卡还款
TXN_CC_CASH = "CC_CASH_ADVANCE"         # 预借现金
TXN_CC_CASH_FEE = "CC_CASH_FEE"         # 预借现金手续费
TXN_CC_CASH_PAYOUT = "CC_CASH_PAYOUT"   # 预借现金转入储蓄账户（账户侧入账流水）
TXN_CC_CONSUME = "CC_CONSUME"           # 信用卡消费（扣可用额度）
TXN_CC_FX_FEE = "CC_FX_FEE"             # 外币交易手续费
TXN_CC_CASHBACK = "CC_CASHBACK"         # 消费返现（入人民币储蓄账户）
TXN_CC_INTEREST = "CC_INTEREST"         # 最低还款后剩余本金循环利息
TXN_INVEST_BUY = "INVEST_BUY"           # 理财申购（买入，扣储蓄账户）
TXN_INVEST_SELL = "INVEST_SELL"         # 理财赎回（卖出，入储蓄账户）

TXN_STATUS_SUCCESS = 1
TXN_STATUS_FAIL = 0

# ---- 审计操作结果 ----
RESULT_SUCCESS = "SUCCESS"
RESULT_FAILURE = "FAILURE"

# ---- 系统参数键（system_param.param_key）----
# 注：不再有「每币种买入价/卖出价」参数。全行买卖价一律由实时中间价按统一点差推算：
#     卖出价(银行卖/客户买)=中间价×(1+FX_SPREAD)、买入价(银行买/客户卖)=中间价×(1-FX_SPREAD)，
#     见 forex.quote_from_mid。点差只有 FX_SPREAD 这一个旋钮（默认 0.3%）。
SUPPORTED_CURRENCIES = ["USD", "EUR", "JPY", "GBP", "HKD", "AUD", "CAD", "CHF", "SGD"]

P_LOAN_RATE = "LOAN_RATE"                       # 贷款年利率
P_LOAN_OVERDUE_RATE = "LOAN_OVERDUE_RATE"       # 逾期罚息日利率
P_WITHDRAW_DAILY_LIMIT = "WITHDRAW_DAILY_LIMIT" # 单日取款限额
P_TRANSFER_FEE_RATE = "TRANSFER_FEE_RATE"       # 跨行转账手续费率
P_CC_LIMIT_MAX = "CC_CREDIT_LIMIT_MAX"          # 信用卡授信额度上限
P_CC_MIN_REPAY_RATE = "CC_MIN_REPAY_RATE"       # 最低还款比例
P_CC_CASH_FEE_RATE = "CC_CASH_ADVANCE_FEE_RATE" # 预借现金手续费率
P_CC_CASH_DAILY_LIMIT = "CC_CASH_DAILY_LIMIT"   # 预借现金单日限额
P_CC_MIN_INTEREST_RATE = "CC_MIN_INTEREST_RATE" # 最低还款后剩余本金月利率（每月累计）
P_CC_LIMIT_DEPOSIT_RATIO = "CC_LIMIT_DEPOSIT_RATIO"  # 提额上限占存款比例（新额度≤存款×该比例）
P_FX_SPREAD = "FX_SPREAD"                       # 外汇挂牌点差（买卖各偏离中间价的比例，如 0.003=0.3%）

# 证件类型
ID_TYPES = ["身份证", "护照", "港澳通行证", "军官证"]

# ---- 输入校验用的枚举白名单与上限 ----
LOAN_TYPES = ["个人消费贷", "住房贷款", "经营贷款", "汽车贷款"]

# ---- 信用卡卡种规格（模仿汇丰香港）----
# currency        计价/结算币种（人民币卡 CNY / 美元卡 USD）
# default_limit   默认授信额度（本卡币种）
# network         卡组织
# cashback_rate   消费返现比例（银联卡有，返现入人民币储蓄账户；Visa/万事达为 0）
# points_per_unit 每消费 1 单位本卡币种所得积分（Visa/万事达有；银联为 0）
# fx_fee_rate     外币交易手续费率（消费币种≠本卡币种时收取）
# waive_fx_fee    是否免收外币交易手续费（World Elite 免除）
CARD_UNIONPAY_PLATINUM = "银联白金卡"
CARD_UNIONPAY_DIAMOND = "银联钻石卡"
CARD_VISA_PLATINUM = "Visa Platinum"
CARD_MASTERCARD_ELITE = "MasterCard World Elite"
CARD_SPECS = {
    CARD_UNIONPAY_PLATINUM: {"currency": "CNY", "default_limit": "20000", "network": "银联",
                             "cashback_rate": "0.024", "points_per_unit": "0",
                             "fx_fee_rate": "0.01", "waive_fx_fee": False},
    CARD_UNIONPAY_DIAMOND: {"currency": "CNY", "default_limit": "100000", "network": "银联",
                            "cashback_rate": "0.044", "points_per_unit": "0",
                            "fx_fee_rate": "0.01", "waive_fx_fee": False},
    CARD_VISA_PLATINUM: {"currency": "USD", "default_limit": "20000", "network": "Visa",
                         "cashback_rate": "0", "points_per_unit": "7",
                         "fx_fee_rate": "0.0195", "waive_fx_fee": False},
    CARD_MASTERCARD_ELITE: {"currency": "USD", "default_limit": "50000", "network": "MasterCard",
                            "cashback_rate": "0", "points_per_unit": "10",
                            "fx_fee_rate": "0", "waive_fx_fee": True},
}
CARD_TYPES = list(CARD_SPECS.keys())

# ---- 积分商城奖品（信用卡模块内，积分兑换）----
CC_PRIZES = [
    {"id": "FLIGHT_INTL", "name": "国际航线机票兑换券", "points": 80000, "desc": "经济舱国际单程机票"},
    {"id": "FLIGHT_DOM", "name": "国内航线机票兑换券", "points": 40000, "desc": "经济舱国内单程机票"},
    {"id": "HOTEL_5S", "name": "五星级酒店住宿券", "points": 30000, "desc": "五星酒店标准间 1 晚"},
    {"id": "PICKUP_CAR", "name": "接机专车服务券", "points": 15000, "desc": "机场接机专车 1 次"},
    {"id": "LOUNGE", "name": "机场贵宾厅通行券", "points": 8000, "desc": "机场贵宾厅 1 次"},
]
CC_PRIZE_MAP = {p["id"]: p for p in CC_PRIZES}
LOAN_AMOUNT_MAX = 100_000_000   # 单笔贷款金额上限（1 亿），防误输天文数字
TXN_AMOUNT_MAX = 100_000_000    # 单笔存/取/转账金额上限（1 亿），防天文数字与大写溢出
LOAN_TERM_MAX = 360             # 贷款期限上限（月），防到期日计算溢出
TEXT_MAX = 200                  # 备注/原因等自由文本长度上限
# 合法系统参数键白名单（= seed 落库的键集），维护参数只允许改这些，杜绝写入孤儿键
ALLOWED_PARAM_KEYS = {
    P_LOAN_RATE, P_LOAN_OVERDUE_RATE, P_WITHDRAW_DAILY_LIMIT, P_TRANSFER_FEE_RATE,
    P_CC_MIN_REPAY_RATE, P_CC_CASH_FEE_RATE, P_CC_CASH_DAILY_LIMIT,
    P_CC_MIN_INTEREST_RATE, P_CC_LIMIT_DEPOSIT_RATIO, P_FX_SPREAD,
}
# 属于"比例/费率"的参数键（业务上应落在 0~1），维护时额外做上限校验
RATE_PARAM_KEYS = {P_LOAN_RATE, P_LOAN_OVERDUE_RATE, P_TRANSFER_FEE_RATE,
                   P_CC_MIN_REPAY_RATE, P_CC_CASH_FEE_RATE, P_CC_MIN_INTEREST_RATE,
                   P_CC_LIMIT_DEPOSIT_RATIO, P_FX_SPREAD}

# ---- 状态/类型的中文标签（前端展示用）----
ACCOUNT_STATUS_LABEL = {1: "正常", 2: "挂失", 3: "冻结", 4: "销户"}
CARD_STATUS_LABEL = {1: "正常", 2: "挂失", 3: "失效"}
CUSTOMER_STATUS_LABEL = {1: "正常", 0: "黑名单"}
FX_STATUS_LABEL = {1: "正常", 2: "冻结", 3: "关闭"}
USER_STATUS_LABEL = {1: "正常", 0: "停用"}
LOAN_STATUS_LABEL = {
    "PENDING": "待审核", "APPROVED": "已批复", "REJECTED": "已拒绝",
    "SUPPLEMENT": "待补件", "ACTIVE": "存续中", "PAID_OFF": "已结清", "OVERDUE": "逾期",
}
CC_STATUS_LABEL = {
    "PENDING": "待审核", "ACTIVE": "正常", "FROZEN": "冻结",
    "LOST": "挂失", "REJECTED": "已拒绝", "INVALID": "已失效",
}
BILL_STATUS_LABEL = {"UNPAID": "未还", "PARTIAL": "部分还款", "PAID": "已还清"}
TXN_TYPE_LABEL = {
    "OPEN_ACCOUNT": "开户", "DEPOSIT": "存款", "WITHDRAW": "取款", "TRANSFER_OUT": "转账转出",
    "TRANSFER_IN": "转账转入", "TRANSFER_FEE": "转账手续费", "CLOSE_ACCOUNT": "销户",
    "LOAN_DISBURSE": "放款", "LOAN_REPAY": "贷款还款", "FX_BUY": "买入外币",
    "FX_SELL": "卖出外币", "CC_REPAY": "信用卡还款", "CC_CASH_ADVANCE": "预借现金",
    "CC_CASH_FEE": "预借现金手续费", "CC_CASH_PAYOUT": "预借现金入账",
    "CC_CONSUME": "信用卡消费", "CC_FX_FEE": "外币交易手续费",
    "CC_CASHBACK": "消费返现", "CC_INTEREST": "循环利息",
    "INVEST_BUY": "理财申购", "INVEST_SELL": "理财赎回",
}

# ============ 投资理财 ============
INVEST_PRODUCT_ACTIVE = 1
INVEST_PRODUCT_OFF = 0
INVEST_PRODUCT_STATUS_LABEL = {1: "在售", 0: "停售"}
INVEST_PTYPE_LABEL = {"FUND": "基金", "STOCK": "股票"}      # 价格源：FUND=天天基金(CNY)，STOCK=美股(USD,折CNY)
INVEST_SOURCE_LABEL = {"ttjj": "天天基金", "av": "AlphaVantage"}
RISK_LEVEL_LABEL = {1: "低", 2: "中低", 3: "中", 4: "中高", 5: "高"}  # 产品风险等级 & 客户风险承受等级
INVEST_PRICE_STALE_MAX_DAYS = 7   # 成交价最多允许旧 7 天；再旧则拒绝买卖（不能用陈价成交）
# 手续费/税费（教学演示：股票按 A 股费率模型，文档注明）
INVEST_FUND_BUY_FEE = "0.0015"        # 基金申购费 0.15%（外扣法：净申购=金额/(1+费率)）
INVEST_FUND_REDEEM_TIERS = [(7, "0.015"), (30, "0.005")]  # 赎回费随持有天数递减：<7天1.5% / 7~30天0.5% / ≥30天0
INVEST_STOCK_COMMISSION = "0.00025"   # 股票佣金 万2.5（买卖双向）
INVEST_STOCK_COMMISSION_MIN = "5"     # 佣金单笔最低 5 元
INVEST_STOCK_STAMP = "0.0005"         # 印花税 0.05%（仅卖出单边）
INVEST_STOCK_TRANSFER = "0.00002"     # 过户费 0.002%（买卖双向）
INVEST_CONFIRM_DAYS = 1               # 申购 T+1 确认份额（演示）
INVEST_SETTLE_DAYS = 1                # 赎回/卖出 T+1 到账（演示）
# —— 更贴近真实系统的合规/交易/披露参数 ——
INVEST_ASSESS_VALID_DAYS = 365        # 风险测评有效期 12 个月：超期须重做才能申购（赎回不受限）
INVEST_MMF_FAST_REDEEM_MAX = "10000"  # 货币基金快速赎回单日限额 1 万元（2018 货基新规），超额走普通赎回
# 基金费用披露（每日计提、已反映在净值里，仅作展示，不参与任何金额计算）
INVEST_FUND_MGMT_FEE = "0.015"        # 管理费 1.5%/年（默认，产品可覆盖）
INVEST_FUND_CUSTODY_FEE = "0.0025"    # 托管费 0.25%/年（默认，产品可覆盖）
INVEST_SETTLE_STATUS = {"BUY_PENDING": "待确认(T+1)", "BUY_DONE": "份额已确认",
                        "SELL_PENDING": "待到账(T+1)", "SELL_DONE": "资金已到账",
                        "SELL_FAST_DONE": "快速赎回·已到账(T+0)"}
