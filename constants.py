"""系统常量：角色、状态枚举、业务类型、系统参数键。集中定义，便于阅读和修改。

字段命名与《需求规格说明书》3.3 的数据库表保持一致，方便对照。
"""

# ---- 角色（对应 5 类参与者 + 系统内部 self）----
ROLE_SAVINGS = "SAVINGS_CLERK"       # 储蓄业务员
ROLE_LOAN = "LOAN_CLERK"             # 贷款业务员
ROLE_FOREX = "FOREX_CLERK"           # 外汇业务员
ROLE_CREDIT = "CREDIT_CARD_CLERK"    # 信用卡业务员
ROLE_ADMIN = "ADMIN"                 # 系统管理员

ALL_ROLES = [ROLE_SAVINGS, ROLE_LOAN, ROLE_FOREX, ROLE_CREDIT, ROLE_ADMIN]

ROLE_LABELS = {
    ROLE_SAVINGS: "储蓄业务员",
    ROLE_LOAN: "贷款业务员",
    ROLE_FOREX: "外汇业务员",
    ROLE_CREDIT: "信用卡业务员",
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

TXN_STATUS_SUCCESS = 1
TXN_STATUS_FAIL = 0

# ---- 审计操作结果 ----
RESULT_SUCCESS = "SUCCESS"
RESULT_FAILURE = "FAILURE"

# ---- 系统参数键（system_param.param_key）----
# 外汇牌价：买入价=银行向客户买外币的价（客户卖出用），卖出价=银行卖外币给客户的价（客户买入用）
PARAM_FX = {
    "USD": ("FX_USD_BUY", "FX_USD_SELL"),
    "EUR": ("FX_EUR_BUY", "FX_EUR_SELL"),
    "JPY": ("FX_JPY_BUY", "FX_JPY_SELL"),
}
SUPPORTED_CURRENCIES = ["USD", "EUR", "JPY"]

P_LOAN_RATE = "LOAN_RATE"                       # 贷款年利率
P_LOAN_OVERDUE_RATE = "LOAN_OVERDUE_RATE"       # 逾期罚息日利率
P_WITHDRAW_DAILY_LIMIT = "WITHDRAW_DAILY_LIMIT" # 单日取款限额
P_TRANSFER_FEE_RATE = "TRANSFER_FEE_RATE"       # 跨行转账手续费率
P_CC_LIMIT_MAX = "CC_CREDIT_LIMIT_MAX"          # 信用卡授信额度上限
P_CC_MIN_REPAY_RATE = "CC_MIN_REPAY_RATE"       # 最低还款比例
P_CC_CASH_FEE_RATE = "CC_CASH_ADVANCE_FEE_RATE" # 预借现金手续费率
P_CC_CASH_DAILY_LIMIT = "CC_CASH_DAILY_LIMIT"   # 预借现金单日限额

# 证件类型
ID_TYPES = ["身份证", "护照", "港澳通行证", "军官证"]

# ---- 输入校验用的枚举白名单与上限 ----
LOAN_TYPES = ["个人消费贷", "住房贷款", "经营贷款", "汽车贷款"]
CARD_TYPES = ["普卡", "金卡", "白金卡"]
LOAN_AMOUNT_MAX = 100_000_000   # 单笔贷款金额上限（1 亿），防误输天文数字
LOAN_TERM_MAX = 360             # 贷款期限上限（月），防到期日计算溢出
TEXT_MAX = 200                  # 备注/原因等自由文本长度上限
# 合法系统参数键白名单（= seed 落库的键集），维护参数只允许改这些，杜绝写入孤儿键
ALLOWED_PARAM_KEYS = (
    {k for pair in PARAM_FX.values() for k in pair}
    | {P_LOAN_RATE, P_LOAN_OVERDUE_RATE, P_WITHDRAW_DAILY_LIMIT, P_TRANSFER_FEE_RATE,
       P_CC_LIMIT_MAX, P_CC_MIN_REPAY_RATE, P_CC_CASH_FEE_RATE, P_CC_CASH_DAILY_LIMIT}
)
# 属于"比例/费率"的参数键（业务上应落在 0~1），维护时额外做上限校验
RATE_PARAM_KEYS = {P_LOAN_RATE, P_LOAN_OVERDUE_RATE, P_TRANSFER_FEE_RATE,
                   P_CC_MIN_REPAY_RATE, P_CC_CASH_FEE_RATE}

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
}
