// 声明式定义所有 29 个用例的表单与结果展示，按角色分组。
// 引擎(app.js)读取这里的配置自动渲染菜单、表单、提交与结果。改需求只需改这张表。

const ID_TYPES = ['身份证', '护照', '港澳通行证', '军官证'];
const CURRENCIES = ['USD', 'EUR', 'JPY'];

// 结果渲染小工具（由 app.js 注入到全局：money / tbl / kv）
function txnTable(list) {
  return tbl(list, [
    { k: 'txn_no', label: '流水号' },
    { k: 'business_label', label: '类型' },
    { k: 'amount', label: '金额', fmt: money },
    { k: 'status_label', label: '状态' },
    { k: 'txn_time', label: '时间' },
  ]);
}

const OPERATIONS = {
  // ================= 储蓄业务员 =================
  SAVINGS_CLERK: [
    {
      code: 'UC-101', name: '开户注册', method: 'POST', path: '/api/savings/open-account',
      fields: [
        { n: 'name', label: '客户姓名', required: true },
        { n: 'id_type', label: '证件类型', type: 'select', options: ID_TYPES },
        { n: 'id_no', label: '证件号', required: true },
        { n: 'phone', label: '手机号' },
        { n: 'initial_balance', label: '初始存款', type: 'number', hint: '可留空，默认0' },
      ],
      result: d => kv({ '客户号': d.customer.customer_no, '姓名': d.customer.name, '账号': d.account.account_no, '卡号': d.account.card_no, '余额': money(d.account.balance) }),
    },
    {
      code: 'UC-102', name: '柜台存款', method: 'POST', path: '/api/savings/deposit',
      fields: [{ n: 'account_no', label: '账号', required: true }, { n: 'amount', label: '存款金额', type: 'number', required: true }],
      result: d => kv({ '当前余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-103', name: '柜台取款', method: 'POST', path: '/api/savings/withdraw',
      fields: [{ n: 'account_no', label: '账号', required: true }, { n: 'amount', label: '取款金额', type: 'number', required: true }],
      result: d => kv({ '当前余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-104', name: '转账汇款', method: 'POST', path: '/api/savings/transfer',
      fields: [
        { n: 'transfer_type', label: '转账类型', type: 'select', options: [{ value: 'INTRA', label: '行内/同户转账' }, { value: 'INTER', label: '跨行转账' }] },
        { n: 'from_account_no', label: '转出账号', required: true },
        { n: 'to_account_no', label: '收款账号', required: true },
        { n: 'to_bank', label: '收款银行', hint: '跨行必填' },
        { n: 'amount', label: '转账金额', type: 'number', required: true },
      ],
      result: d => kv({ '说明': d.sub, '手续费': money(d.fee), '转出后余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-105', name: '账户/明细查询', method: 'GET', path: '/api/savings/query',
      fields: [
        { n: 'account_no', label: '账号' }, { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' },
        { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      hint: '账号 / 客户号 / 证件号 任填其一',
      result: d => kv({ '客户': d.customer.name + ' (' + d.customer.customer_no + ')', '账号': d.account.account_no, '余额': money(d.account.balance), '账户状态': d.account.status_label, '卡状态': d.account.card_status_label })
        + (d.account.note ? `<p class="hint">${d.account.note}</p>` : '')
        + '<h4>交易明细</h4>' + (d.transactions.length ? txnTable(d.transactions) : `<p class="hint">${d.empty_hint || '无明细'}</p>`),
    },
    {
      code: 'UC-106', name: '挂失/解挂/补卡', method: 'POST', path: '/api/savings/card',
      fields: [
        { n: 'account_no', label: '账号或卡号', required: true }, { n: 'id_no', label: '证件号', required: true },
        { n: 'op', label: '操作', type: 'select', options: [{ value: 'LOSS', label: '挂失' }, { value: 'UNLOSS', label: '解挂' }, { value: 'REISSUE', label: '补卡' }] },
      ],
      result: d => kv({ '账号': d.account_no, '当前卡号': d.card_no }),
    },
    {
      code: 'UC-107', name: '销户处理', method: 'POST', path: '/api/savings/close-account',
      fields: [{ n: 'account_no', label: '账号', required: true }, { n: 'id_no', label: '证件号', required: true }],
    },
    {
      code: 'UC-108', name: '客户信息更新', method: 'POST', path: '/api/savings/update-customer',
      fields: [
        { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号', required: true, hint: '用于身份核验' },
        { n: 'phone', label: '新手机号' }, { n: 'address', label: '新联系地址' }, { n: 'occupation', label: '职业' },
        { n: 'name', label: '变更姓名(关键)', hint: '需勾选二次确认' }, { n: 'new_id_no', label: '变更证件号(关键)' },
        { n: 'confirm', label: '二次确认关键信息变更', type: 'checkbox' }, { n: 'reason', label: '变更原因' },
      ],
      result: d => kv({ '客户号': d.customer.customer_no, '姓名': d.customer.name, '手机号': d.customer.phone, '地址': d.customer.address || '-' }),
    },
  ],

  // ================= 贷款业务员 =================
  LOAN_CLERK: [
    {
      code: 'UC-201', name: '贷款申请办理', method: 'POST', path: '/api/loan/apply',
      fields: [
        { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号', hint: '客户号/证件号任一' },
        { n: 'loan_type', label: '贷款类型', type: 'select', options: ['个人消费贷', '住房贷款', '经营贷款', '汽车贷款'] },
        { n: 'amount', label: '申请金额', type: 'number', required: true },
        { n: 'term_months', label: '期限(月)', type: 'number', required: true },
        { n: 'account_no', label: '放款/还款账号', hint: '留空取客户默认账户' },
        { n: 'purpose', label: '用途' }, { n: 'guarantee', label: '担保方式' },
      ],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '金额': money(d.loan.amount) }),
    },
    {
      code: 'UC-202', name: '审核与审批', method: 'POST', path: '/api/loan/approve',
      fields: [
        { n: 'contract_no', label: '合同号', required: true },
        { n: 'decision', label: '审批结论', type: 'select', options: [{ value: 'APPROVED', label: '通过' }, { value: 'REJECTED', label: '拒绝' }, { value: 'SUPPLEMENT', label: '待补件' }] },
        { n: 'approved_amount', label: '批准金额', type: 'number' }, { n: 'interest_rate', label: '年利率(如0.0435)', type: 'number' },
        { n: 'term_months', label: '期限(月)', type: 'number' }, { n: 'repay_method', label: '还款方式' }, { n: 'reason', label: '拒绝/补件原因' },
      ],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '批准金额': money(d.loan.amount), '年利率': d.loan.interest_rate }),
    },
    {
      code: 'UC-203', name: '放款处理', method: 'POST', path: '/api/loan/disburse',
      fields: [{ n: 'contract_no', label: '合同号', required: true }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '应还余额': money(d.loan.balance), '到期日': d.loan.due_date }),
    },
    {
      code: 'UC-204', name: '还款登记', method: 'POST', path: '/api/loan/repay',
      fields: [{ n: 'contract_no', label: '合同号', required: true }, { n: 'amount', label: '还款金额', type: 'number', required: true }, { n: 'account_no', label: '还款账号', hint: '留空取合同账户' }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '剩余应还': money(d.loan.balance) }),
    },
    {
      code: 'UC-205', name: '逾期查询', method: 'GET', path: '/api/loan/overdue',
      fields: [{ n: 'days', label: '最小逾期天数', type: 'number' }, { n: 'customer_no', label: '客户号' }, { n: 'contract_no', label: '合同号' }],
      result: d => d.loans.length ? tbl(d.loans, [
        { k: 'contract_no', label: '合同号' }, { k: 'balance', label: '剩余应还', fmt: money },
        { k: 'overdue_days', label: '逾期天数' }, { k: 'penalty', label: '罚息', fmt: money }, { k: 'status_label', label: '状态' },
      ]) : `<p class="hint">${d.hint || '无逾期贷款'}</p>`,
    },
    {
      code: 'UC-205b', name: '催收登记', method: 'POST', path: '/api/loan/overdue',
      fields: [{ n: 'contract_no', label: '合同号', required: true }, { n: 'method', label: '催收方式' }, { n: 'feedback', label: '客户反馈' }, { n: 'note', label: '处理意见' }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label }),
    },
    {
      code: 'UC-206', name: '贷款查询统计', method: 'GET', path: '/api/loan/query',
      fields: [
        { n: 'customer_no', label: '客户号' }, { n: 'contract_no', label: '合同号' },
        { n: 'status', label: '状态', type: 'select', options: [{ value: '', label: '全部' }, { value: 'PENDING', label: '待审核' }, { value: 'APPROVED', label: '已批复' }, { value: 'ACTIVE', label: '存续中' }, { value: 'OVERDUE', label: '逾期' }, { value: 'PAID_OFF', label: '已结清' }] },
        { n: 'loan_type', label: '贷款类型' }, { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      result: d => kv({ '笔数': d.stats.count, '申请总额': money(d.stats.total_amount), '剩余本金': money(d.stats.total_balance), '逾期笔数': d.stats.overdue_count, '已结清': d.stats.paid_count })
        + (d.loans.length ? tbl(d.loans, [{ k: 'contract_no', label: '合同号' }, { k: 'loan_type', label: '类型' }, { k: 'amount', label: '金额', fmt: money }, { k: 'balance', label: '余额', fmt: money }, { k: 'status_label', label: '状态' }]) : `<p class="hint">${d.hint || ''}</p>`),
    },
  ],

  // ================= 外汇业务员 =================
  FOREX_CLERK: [
    {
      code: 'UC-301', name: '外汇子户开立', method: 'POST', path: '/api/forex/open-subaccount',
      fields: [{ n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' }, { n: 'currency', label: '币种', type: 'select', options: CURRENCIES }],
      result: d => kv({ '子户号': d.fx_account.fx_account_no, '币种': d.fx_account.currency, '关联账户': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-302', name: '汇率查询与确认', method: 'GET', path: '/api/forex/rate',
      fields: [{ n: 'currency', label: '币种', type: 'select', options: CURRENCIES }, { n: 'direction', label: '交易方向', type: 'select', options: [{ value: 'BUY', label: '客户买入外币' }, { value: 'SELL', label: '客户卖出外币' }] }],
      result: d => kv({ '币种': d.currency, '买入价': d.buy_rate, '卖出价': d.sell_rate, '本次适用': d.apply_rate + ' (' + d.apply_rate_type + ')', '生效时间': d.effective_at, '说明': d.note }),
    },
    {
      code: 'UC-303', name: '外汇买卖确认', method: 'POST', path: '/api/forex/trade',
      fields: [
        { n: 'fx_account_no', label: '外汇子户号', required: true },
        { n: 'direction', label: '方向', type: 'select', options: [{ value: 'BUY', label: '客户买入外币' }, { value: 'SELL', label: '客户卖出外币' }] },
        { n: 'amount', label: '外币金额', type: 'number', required: true },
      ],
      result: d => kv({ '本币金额': money(d.cny_amount), '汇率': d.rate + ' (' + d.rate_type + ')', '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-304', name: '外汇账户变更', method: 'POST', path: '/api/forex/change',
      fields: [
        { n: 'fx_account_no', label: '外汇子户号', required: true },
        { n: 'change_type', label: '变更类型', type: 'select', options: [{ value: 'FREEZE', label: '冻结' }, { value: 'UNFREEZE', label: '解冻' }, { value: 'CLOSE', label: '关闭子户' }, { value: 'REBIND', label: '调整关联账户' }] },
        { n: 'new_base_account_no', label: '新关联账号', hint: '调整关联时填' }, { n: 'reason', label: '变更原因' },
      ],
      result: d => kv({ '子户号': d.fx_account.fx_account_no, '状态': d.fx_account.status_label, '关联账户': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-305', name: '余额与历史查询', method: 'GET', path: '/api/forex/query',
      fields: [{ n: 'fx_account_no', label: '外汇子户号' }, { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' }, { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' }],
      result: d => tbl(d.fx_accounts, [{ k: 'fx_account_no', label: '子户号' }, { k: 'currency', label: '币种' }, { k: 'balance', label: '余额', fmt: money }, { k: 'status_label', label: '状态' }])
        + '<h4>交易历史</h4>' + (d.history.length ? tbl(d.history, [{ k: 'txn_time', label: '时间' }, { k: 'business_label', label: '类型' }, { k: 'currency', label: '币种' }, { k: 'amount', label: '外币金额', fmt: money }, { k: 'fx_rate', label: '汇率' }, { k: 'cny_amount', label: '本币金额', fmt: money }]) : `<p class="hint">${d.hint || '无记录'}</p>`),
    },
  ],

  // ================= 信用卡业务员 =================
  CREDIT_CARD_CLERK: [
    {
      code: 'UC-401', name: '信用卡申请办理', method: 'POST', path: '/api/creditcard/apply',
      fields: [{ n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' }, { n: 'card_type', label: '卡种', type: 'select', options: ['普卡', '金卡', '白金卡'] }, { n: 'occupation', label: '职业' }, { n: 'monthly_income', label: '月收入', type: 'number' }],
      result: d => kv({ '卡号': d.credit_card.card_no, '状态': d.credit_card.status_label }),
    },
    {
      code: 'UC-402', name: '审核与额度设定', method: 'POST', path: '/api/creditcard/approve',
      fields: [
        { n: 'card_no', label: '信用卡号', required: true },
        { n: 'decision', label: '审批结论', type: 'select', options: [{ value: 'APPROVED', label: '通过' }, { value: 'REJECTED', label: '拒绝' }] },
        { n: 'credit_limit', label: '授信额度', type: 'number' }, { n: 'bill_day', label: '账单日(1-28)', type: 'number' }, { n: 'repay_day', label: '还款日(1-28)', type: 'number' }, { n: 'reason', label: '拒绝原因' },
      ],
      result: d => d.credit_card ? kv({ '卡号': d.credit_card.card_no, '状态': d.credit_card.status_label, '授信额度': money(d.credit_card.credit_limit), '账单日': d.credit_card.bill_day, '还款日': d.credit_card.repay_day }) : '',
    },
    {
      code: 'UC-403', name: '账单生成', method: 'POST', path: '/api/creditcard/bill',
      fields: [{ n: 'card_no', label: '信用卡号', required: true }, { n: 'bill_cycle', label: '账期(YYYYMM)', hint: '留空取当月' }],
      result: d => kv({ '账期': d.bill.bill_cycle, '应还总额': money(d.bill.total_amount), '最低还款': money(d.bill.min_repay), '还款截止': d.bill.due_date, '状态': d.bill.status_label }),
    },
    {
      code: 'UC-404', name: '还款处理', method: 'POST', path: '/api/creditcard/repay',
      fields: [
        { n: 'card_no', label: '信用卡号', required: true }, { n: 'account_no', label: '还款储蓄账号', required: true },
        { n: 'repay_type', label: '还款方式', type: 'select', options: [{ value: 'FULL', label: '全额还款' }, { value: 'MIN', label: '最低还款' }, { value: 'PARTIAL', label: '部分还款' }] },
        { n: 'amount', label: '还款金额(部分还款填)', type: 'number' },
      ],
      result: d => kv({ '账期': d.bill.bill_cycle, '账单状态': d.bill.status_label, '已还': money(d.bill.paid_amount), '剩余': money(d.bill.remaining), '可用额度': money(d.credit_card.available_limit) }),
    },
    {
      code: 'UC-405', name: '预借现金处理', method: 'POST', path: '/api/creditcard/cash-advance',
      fields: [{ n: 'card_no', label: '信用卡号', required: true }, { n: 'id_no', label: '证件号', required: true }, { n: 'amount', label: '取现金额', type: 'number', required: true }, { n: 'payout_account', label: '转入储蓄账号', hint: '留空为现金出款' }],
      result: d => kv({ '手续费': money(d.fee), '出款方式': d.payout, '剩余可用额度': money(d.available_limit), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-406', name: '挂失/补卡/异常', method: 'POST', path: '/api/creditcard/card',
      fields: [
        { n: 'card_no', label: '信用卡号', required: true }, { n: 'id_no', label: '证件号', required: true },
        { n: 'op', label: '操作', type: 'select', options: [{ value: 'LOSS', label: '挂失' }, { value: 'REISSUE', label: '补卡' }, { value: 'FREEZE', label: '冻结' }, { value: 'UNFREEZE', label: '解冻' }, { value: 'EXCEPTION', label: '异常登记' }] },
        { n: 'note', label: '异常说明' },
      ],
      result: d => d.card_no ? kv({ '当前卡号': d.card_no }) : '',
    },
    {
      code: 'UC-4Q', name: '信用卡查询', method: 'GET', path: '/api/creditcard/query',
      fields: [{ n: 'card_no', label: '信用卡号' }, { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' }],
      result: d => d.cards.map(c => kv({ '卡号': c.card_no, '状态': c.status_label, '授信额度': money(c.credit_limit), '可用额度': money(c.available_limit), '已用': money(c.used) })
        + (c.bills.length ? '<h4>账单</h4>' + tbl(c.bills, [{ k: 'bill_cycle', label: '账期' }, { k: 'total_amount', label: '应还', fmt: money }, { k: 'paid_amount', label: '已还', fmt: money }, { k: 'status_label', label: '状态' }]) : '')).join('<hr>'),
    },
  ],

  // ================= 系统管理员 =================
  ADMIN: [
    {
      code: 'UC-501', name: '用户列表', method: 'GET', path: '/api/admin/users',
      fields: [],
      result: d => tbl(d.users, [{ k: 'employee_no', label: '工号' }, { k: 'name', label: '姓名' }, { k: 'role_label', label: '角色' }, { k: 'status_label', label: '状态' }]),
    },
    {
      code: 'UC-501b', name: '新建用户', method: 'POST', path: '/api/admin/users',
      fields: [
        { n: 'employee_no', label: '工号', required: true }, { n: 'name', label: '姓名', required: true },
        { n: 'role', label: '角色', type: 'select', options: [{ value: 'SAVINGS_CLERK', label: '储蓄业务员' }, { value: 'LOAN_CLERK', label: '贷款业务员' }, { value: 'FOREX_CLERK', label: '外汇业务员' }, { value: 'CREDIT_CARD_CLERK', label: '信用卡业务员' }, { value: 'ADMIN', label: '系统管理员' }] },
        { n: 'password', label: '初始密码', type: 'password', required: true },
      ],
      result: d => kv({ '工号': d.user.employee_no, '姓名': d.user.name, '角色': d.user.role_label }),
    },
    {
      code: 'UC-501c', name: '修改/停用用户', method: 'POST', path: '/api/admin/users/update',
      fields: [
        { n: 'employee_no', label: '工号', required: true }, { n: 'name', label: '新姓名' },
        { n: 'role', label: '新角色', type: 'select', options: [{ value: '', label: '不变' }, { value: 'SAVINGS_CLERK', label: '储蓄业务员' }, { value: 'LOAN_CLERK', label: '贷款业务员' }, { value: 'FOREX_CLERK', label: '外汇业务员' }, { value: 'CREDIT_CARD_CLERK', label: '信用卡业务员' }, { value: 'ADMIN', label: '系统管理员' }] },
        { n: 'status', label: '状态', type: 'select', options: [{ value: '', label: '不变' }, { value: '1', label: '启用' }, { value: '0', label: '停用' }] },
        { n: 'password', label: '重置密码', type: 'password' },
      ],
      result: d => kv({ '工号': d.user.employee_no, '姓名': d.user.name, '角色': d.user.role_label, '状态': d.user.status_label }),
    },
    {
      code: 'UC-502', name: '参数列表', method: 'GET', path: '/api/admin/params',
      fields: [],
      result: d => tbl(d.params, [{ k: 'param_type', label: '类型' }, { k: 'param_key', label: '参数键' }, { k: 'param_value', label: '参数值' }, { k: 'changed_at', label: '修改时间' }]),
    },
    {
      code: 'UC-502b', name: '维护参数', method: 'POST', path: '/api/admin/params',
      fields: [
        { n: 'param_type', label: '参数类型', type: 'select', options: ['RATE', 'LIMIT', 'FX_RATE', 'OTHER'] },
        { n: 'param_key', label: '参数键', required: true, hint: '如 LOAN_RATE / FX_USD_BUY' },
        { n: 'param_value', label: '参数值', required: true },
      ],
    },
    {
      code: 'UC-503', name: '日志审计', method: 'GET', path: '/api/admin/audit',
      fields: [
        { n: 'employee_no', label: '操作人工号' }, { n: 'action', label: '操作类型' }, { n: 'object_type', label: '对象类型' },
        { n: 'result', label: '结果', type: 'select', options: [{ value: '', label: '全部' }, { value: 'SUCCESS', label: '成功' }, { value: 'FAILURE', label: '失败' }] },
        { n: 'only_failure', label: '仅看失败', type: 'checkboxVal', value: '1' },
        { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      result: d => d.logs.length ? tbl(d.logs, [{ k: 'created_at', label: '时间' }, { k: 'operator', label: '操作人' }, { k: 'action', label: '操作' }, { k: 'object_type', label: '对象' }, { k: 'object_id', label: '对象ID' }, { k: 'result', label: '结果' }]) : `<p class="hint">${d.hint || '无记录'}</p>`,
    },
    {
      code: 'UC-504', name: '数据备份(下载)', method: 'GET', path: '/api/admin/backup', type: 'download',
      fields: [], hint: '点击生成并下载数据库备份 JSON 文件',
    },
    {
      code: 'UC-504b', name: '数据恢复(上传)', method: 'POST', path: '/api/admin/restore', type: 'upload',
      fields: [{ n: 'confirm', label: '我已确认恢复风险', type: 'checkboxVal', value: 'true', required: true }],
      hint: '高风险操作：会用备份文件覆盖当前数据',
      result: d => kv(d.restored),
    },
  ],
};
