// 声明式定义所有 29 个用例的表单与结果展示，按角色分组。
// 引擎(app.js)读取这里的配置自动渲染菜单、表单、提交与结果。改需求只需改这张表。

const ID_TYPES = ['身份证', '护照', '港澳通行证', '军官证'];
// 信用卡卡种（每人每种卡最多一张，故「身份+卡种」即可唯一定位一张卡，无需记卡号）
const CARD_TYPES = ['银联白金卡', '银联钻石卡', 'Visa Platinum', 'MasterCard World Elite'];
// 卡种权益说明（展示用）。数值口径须与后端 constants.py 的 CARD_SPECS 一致——改优惠请两处同改。
const CARD_BENEFITS = [
  { type: '银联白金卡', network: '银联', currency: '人民币 CNY', limit: 20000, cashback: '2.4%（入人民币储蓄账户）', points: '—', fxfee: '1%' },
  { type: '银联钻石卡', network: '银联', currency: '人民币 CNY', limit: 100000, cashback: '4.4%（入人民币储蓄账户）', points: '—', fxfee: '1%' },
  { type: 'Visa Platinum', network: 'Visa', currency: '美元 USD', limit: 20000, cashback: '—', points: '每消费 1 美元得 7 分', fxfee: '1.95%' },
  { type: 'MasterCard World Elite', network: 'MasterCard', currency: '美元 USD', limit: 50000, cashback: '—', points: '每消费 1 美元得 10 分', fxfee: '免除' },
];
// 币种：显示中文，提交仍用 USD/EUR/JPY
const CURRENCIES = [{ value: 'USD', label: '美元(USD)' }, { value: 'EUR', label: '欧元(EUR)' }, { value: 'JPY', label: '日元(JPY)' }, { value: 'GBP', label: '英镑(GBP)' }, { value: 'HKD', label: '港元(HKD)' }, { value: 'AUD', label: '澳元(AUD)' }, { value: 'CAD', label: '加元(CAD)' }, { value: 'CHF', label: '瑞郎(CHF)' }, { value: 'SGD', label: '新元(SGD)' }];

// 汇率方向类型（后端返回英文 BUY/SELL）→ 中文
const FX_RATE_TYPE_LABEL = { BUY: '买入价', SELL: '卖出价' };
const fxRateType = t => FX_RATE_TYPE_LABEL[t] || t;
// 小数利率 → 百分比显示（0.0435 → 4.35%）
const pct = v => (v == null || v === '') ? '' : +(Number(v) * 100).toFixed(4) + '%';
// 账期 YYYYMM → 2026年01月
const billCycle = v => v == null ? '' : String(v).replace(/^(\d{4})(\d{2})$/, '$1年$2月');

// 日志审计：后端原始英文枚举 → 中文（与 constants.py 交易标签口径保持一致）
const ACTION_LABEL = {
  LOGIN: '登录',
  OPEN_ACCOUNT: '开户', DEPOSIT: '存款', WITHDRAW: '取款', TRANSFER: '转账',
  QUERY_ACCOUNT: '账户查询', CLOSE_ACCOUNT: '销户', UPDATE_CUSTOMER: '客户信息修改',
  CARD_LOSS: '银行卡挂失', CARD_UNLOSS: '银行卡解挂', CARD_REISSUE: '银行卡补卡', CARD_OP: '银行卡操作',
  LOAN_APPLY: '贷款申请', LOAN_APPROVE: '贷款审批', LOAN_DISBURSE: '放款',
  LOAN_REPAY: '贷款还款', LOAN_OVERDUE: '贷款逾期催收', LOAN_QUERY: '贷款查询',
  FX_OPEN: '外汇开户', FX_RATE_CONFIRM: '外汇牌价确认', FX_BUY: '买入外币', FX_SELL: '卖出外币',
  FX_FREEZE: '外汇账户冻结', FX_UNFREEZE: '外汇账户解冻', FX_CLOSE: '外汇账户关闭', FX_REBIND: '外汇改绑账户', FX_QUERY: '外汇查询', FX_RATE_QUERY: '外汇实时汇率查询', FX_RATE_SYNC: '外汇实时挂牌',
  CC_APPLY: '信用卡申请', CC_APPROVE: '信用卡审批通过', CC_REJECT: '信用卡审批拒绝',
  CC_REPAY: '信用卡还款', CC_CONSUME: '信用卡消费', CC_RECORDS: '消费记录查询',
  CC_LIMIT_REQUEST: '提额申请', CC_LIMIT_APPROVE: '提额审批通过', CC_LIMIT_REJECT: '提额审批拒绝',
  CC_REDEEM: '积分兑换',
  CC_LOSS: '信用卡挂失', CC_FREEZE: '信用卡冻结', CC_UNFREEZE: '信用卡解冻', CC_REISSUE: '信用卡补卡',
  CC_EXCEPTION: '信用卡异常登记', CC_CARD: '信用卡卡片操作',
  CC_BILL: '信用卡账单生成', CC_CASH_ADVANCE: '预借现金',
  USER_CREATE: '新建操作员', USER_UPDATE: '操作员信息变更', PARAM_UPDATE: '系统参数修改',
  BACKUP: '数据备份', RESTORE: '数据恢复', CHANGE_PASSWORD: '修改密码',
  INVEST_BUY: '理财申购', INVEST_SELL: '理财赎回', INVEST_REFRESH: '理财行情刷新',
  INVEST_ASSESS: '风险测评', INVEST_PRODUCT: '理财产品维护',
};
const OBJECT_LABEL = {
  account: '储蓄账户', customer: '客户', loan: '贷款', fx_account: '外汇账户',
  credit_card: '信用卡', credit_card_bill: '信用卡账单', user_account: '操作员账号',
  system_param: '系统参数', database: '数据库', audit_log: '审计日志',
  business_transaction: '业务流水', counters: '业务编号',
  invest_product: '理财产品', invest_holding: '理财持仓',
};
const RESULT_LABEL = { SUCCESS: '成功', FAILURE: '失败' };
const actionLabel = a => ACTION_LABEL[a] || a;
const objectLabel = o => OBJECT_LABEL[o] || o;
const resultLabel = r => RESULT_LABEL[r] || r;
// 审计筛选下拉选项（中文标签、值用原始英文代码）
const ACTION_OPTIONS = [{ value: '', label: '全部' }].concat(Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label })));
const OBJECT_OPTIONS = [{ value: '', label: '全部' }].concat(Object.entries(OBJECT_LABEL).map(([value, label]) => ({ value, label })));

// 系统参数：把数据库里的英文键/类型翻译成普通人看得懂的中文（面向柜员/管理员，非开发者）
const PARAM_TYPE_LABEL = { RATE: '利率', LIMIT: '限额', FX_RATE: '汇率', OTHER: '其他' };
const PARAM_NAME = {
  FX_USD_BUY: '美元买入价', FX_USD_SELL: '美元卖出价',
  FX_EUR_BUY: '欧元买入价', FX_EUR_SELL: '欧元卖出价',
  FX_JPY_BUY: '日元买入价', FX_JPY_SELL: '日元卖出价',
  LOAN_RATE: '贷款默认年利率', LOAN_OVERDUE_RATE: '逾期日罚息率',
  WITHDRAW_DAILY_LIMIT: '单日取款上限', TRANSFER_FEE_RATE: '转账手续费率',
  CC_CREDIT_LIMIT_MAX: '信用卡最高授信额度', CC_MIN_REPAY_RATE: '信用卡最低还款比例',
  CC_CASH_ADVANCE_FEE_RATE: '预借现金手续费率', CC_CASH_DAILY_LIMIT: '预借现金单日上限',
  CC_MIN_INTEREST_RATE: '最低还款剩余本金月利率', CC_LIMIT_DEPOSIT_RATIO: '提额上限占存款比例',
  FX_SPREAD: '外汇挂牌点差',
};
const PARAM_OPTIONS = Object.entries(PARAM_NAME).map(([value, label]) => ({ value, label }));
const paramTypeLabel = t => PARAM_TYPE_LABEL[t] || t || '其他';
const paramName = k => PARAM_NAME[k] || k;
// 值按类型带单位显示：利率→百分比，限额→元，汇率等原样
function paramValue(v, row) {
  if (v == null || v === '') return '';
  const t = row && row.param_type;
  if (t === 'RATE') return +(Number(v) * 100).toFixed(4) + '%';
  if (t === 'LIMIT') return money(v) + ' 元';
  return v;
}

// 结果渲染小工具（由 app.js 注入到全局：money / tbl / kv / esc）
// kvRows：与 kv() 同样式的键值行，但值允许受控 HTML（如标红提示）——数据须由调用方自行 esc
const kvRows = rows => '<table class="kv">' + rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('') + '</table>';

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
        { n: 'email', label: '邮箱', required: true, hint: '必填：交易时可用邮箱替代证件号核验身份' },
        { n: 'phone', label: '手机号', pattern: '1[3-9]\\d{9}', patternMsg: '手机号应为 11 位大陆手机号' },
        { n: 'initial_balance', label: '初始存款', type: 'number', hint: '可留空，默认0' },
      ],
      result: d => kv({ '客户号': d.customer.customer_no, '姓名': d.customer.name, '邮箱': d.customer.email, '账号': d.account.account_no, '卡号': d.account.card_no, '余额': money(d.account.balance) }),
    },
    {
      code: 'UC-102', name: '柜台存款', method: 'POST', path: '/api/savings/deposit',
      fields: [{ n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' }, { n: 'amount', label: '存款金额', type: 'number', required: true }],
      result: d => kv({ '当前余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-103', name: '柜台取款', method: 'POST', path: '/api/savings/withdraw',
      fields: [{ n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' }, { n: 'amount', label: '取款金额', type: 'number', required: true }],
      result: d => kv({ '当前余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-104', name: '转账汇款', method: 'POST', path: '/api/savings/transfer',
      fields: [
        { n: 'transfer_type', label: '转账类型', type: 'select', options: [{ value: 'INTRA', label: '本行转账（含本人账户互转）' }, { value: 'INTER', label: '跨行转账' }] },
        { n: 'ident', label: '转出方 身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' },
        { n: 'to_ident', label: '收款方 身份标识', hint: '本行转账：凭收款方任意身份标识定位其账户' },
        { n: 'to_account_no', label: '收款账号', hint: '跨行转账必填（行外账号）；本行转账可留空（用收款方身份定位）' },
        { n: 'to_bank', label: '收款方开户银行', hint: '仅跨行转账需填写' },
        { n: 'amount', label: '转账金额', type: 'number', required: true },
      ],
      result: d => kv({ '转账方式': d.sub, '手续费': money(d.fee), '转出后余额': money(d.balance), '流水号': d.txn.txn_no }),
      validate: v => v.transfer_type === 'INTER' ? (!v.to_account_no ? '跨行转账请填写收款账号' : (!v.to_bank ? '跨行转账请填写收款方开户银行' : null)) : ((!v.to_ident && !v.to_account_no) ? '本行转账请填写收款方 邮箱/证件号（或收款账号）' : null),
    },
    {
      code: 'UC-105', name: '账户/明细查询', method: 'GET', path: '/api/savings/query',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      hint: '按任意身份标识查询',
      result: d => kv({ '客户': d.customer.name + ' (' + d.customer.customer_no + ')', '账号': d.account.account_no, '余额': money(d.account.balance), '账户状态': d.account.status_label, '卡状态': d.account.card_status_label })
        + (d.account.note ? `<p class="hint">${d.account.note}</p>` : '')
        + '<h4>交易明细</h4>' + (d.transactions.length ? txnTable(d.transactions) : `<p class="hint">${d.empty_hint || '无明细'}</p>`),
    },
    {
      code: 'UC-106', name: '挂失/解挂/补卡', method: 'POST', path: '/api/savings/card',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'op', label: '操作', type: 'select', options: [{ value: 'LOSS', label: '挂失' }, { value: 'UNLOSS', label: '解挂' }, { value: 'REISSUE', label: '补卡' }] },
      ],
      result: d => kv({ '账号': d.account_no, '当前卡号': d.card_no }),
    },
    {
      code: 'UC-107', name: '销户处理', method: 'POST', path: '/api/savings/close-account',
      fields: [{ n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' }],
    },
    {
      code: 'UC-108', name: '客户信息更新', method: 'POST', path: '/api/savings/update-customer',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'phone', label: '新手机号', pattern: '1[3-9]\\d{9}', patternMsg: '手机号应为 11 位大陆手机号' }, { n: 'address', label: '新联系地址' }, { n: 'occupation', label: '职业' }, { n: 'email', label: '新邮箱' },
        { n: 'name', label: '变更姓名（重要信息）', hint: '修改姓名/证件号须勾选下方“二次确认”' }, { n: 'new_id_no', label: '变更证件号（重要信息）' },
        { n: 'confirm', label: '二次确认关键信息变更', type: 'checkbox' }, { n: 'reason', label: '变更原因' },
      ],
      result: d => '<h4>客户信息（更新后）</h4>' + kv({ '客户号': d.customer.customer_no, '姓名': d.customer.name, '证件类型': d.customer.id_type || '-', '证件号': d.customer.id_no, '邮箱': d.customer.email || '-', '手机号': d.customer.phone || '-', '联系地址': d.customer.address || '-', '职业': d.customer.occupation || '-', '客户状态': d.customer.status_label }),
    },
  ],

  // ================= 贷款业务员 =================
  LOAN_CLERK: [
    {
      code: 'UC-201', name: '贷款申请办理', method: 'POST', path: '/api/loan/apply',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'loan_type', label: '贷款类型', type: 'select', options: ['个人消费贷', '住房贷款', '经营贷款', '汽车贷款'] },
        { n: 'amount', label: '申请金额', type: 'number', required: true },
        { n: 'term_months', label: '期限(月)', type: 'number', required: true },
        { n: 'purpose', label: '借款用途' },
        { n: 'guarantee', label: '担保方式', type: 'select', options: [{ value: '', label: '（可不填）' }, '信用', '抵押', '质押', '保证'] },
      ],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '金额': money(d.loan.amount) }),
    },
    {
      code: 'UC-202', name: '审核与审批', method: 'POST', path: '/api/loan/approve',
      fields: [
        { n: 'contract_no', label: '合同号', required: true },
        { n: 'decision', label: '审批结论', type: 'select', options: [{ value: '', label: '请选择审批结论' }, { value: 'APPROVED', label: '通过' }, { value: 'REJECTED', label: '拒绝' }, { value: 'SUPPLEMENT', label: '待补件' }] },
        { n: 'approved_amount', label: '批准金额', type: 'number', hint: '仅通过时填写' },
        { n: 'interest_rate', label: '年利率', type: 'number', hint: '仅通过时填；小数如 0.0435=4.35%，留空用系统默认' },
        { n: 'term_months', label: '期限（月）', type: 'number', hint: '仅通过时填' },
        { n: 'repay_method', label: '还款方式', type: 'select', options: [{ value: '', label: '默认（等额本息）' }, '等额本息', '等额本金', '先息后本', '一次性还本付息'] },
        { n: 'reason', label: '拒绝/补件原因', hint: '仅拒绝或待补件时填写' },
      ],
      result: d => {
        const l = d.loan, base = { '合同号': l.contract_no, '状态': l.status_label };
        if (l.status === 'APPROVED') { base['批准金额'] = money(l.amount); base['年利率'] = pct(l.interest_rate); base['还款方式'] = l.repay_method || '-'; }
        else if (l.status === 'REJECTED') base['拒绝原因'] = l.reject_reason || '-';
        else if (l.status === 'SUPPLEMENT') base['补件说明'] = l.supplement_note || '-';
        return kv(base);
      },
    },
    {
      code: 'UC-203', name: '放款处理', method: 'POST', path: '/api/loan/disburse',
      fields: [{ n: 'contract_no', label: '合同号', required: true }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '应还余额': money(d.loan.balance), '到期日': d.loan.due_date }),
    },
    {
      code: 'UC-204', name: '还款登记', method: 'POST', path: '/api/loan/repay',
      fields: [{ n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号/合同号，任填其一' }, { n: 'amount', label: '还款金额', type: 'number', required: true }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '剩余本金': money(d.loan.balance), '应收罚息': money(d.loan.penalty_due) }),
    },
    {
      code: 'UC-205', name: '逾期查询', method: 'GET', path: '/api/loan/overdue',
      fields: [{ n: 'days', label: '逾期天数不少于', type: 'number', hint: '留空查全部逾期' }, { n: 'ident', label: '身份标识', hint: '证件号/邮箱/手机号/账号/合同号，留空查全部' }],
      result: d => d.loans.length ? tbl(d.loans, [
        { k: 'contract_no', label: '合同号' }, { k: 'balance', label: '剩余应还', fmt: money },
        { k: 'overdue_days', label: '逾期天数' }, { k: 'penalty', label: '罚息', fmt: money }, { k: 'status_label', label: '状态' },
      ]) : `<p class="hint">${d.hint || '无逾期贷款'}</p>`,
    },
    {
      code: 'UC-205b', name: '催收登记', method: 'POST', path: '/api/loan/overdue',
      fields: [{ n: 'contract_no', label: '合同号', required: true }, { n: 'method', label: '催收方式' }, { n: 'feedback', label: '客户反馈' }, { n: 'note', label: '处理意见' }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label })
        + (d.loan.collection_log && d.loan.collection_log.length ? '<h4>催收记录</h4>' + tbl(d.loan.collection_log, [{ k: 'time', label: '时间' }, { k: 'method', label: '方式' }, { k: 'feedback', label: '客户反馈' }, { k: 'note', label: '处理意见' }, { k: 'operator', label: '经办' }]) : ''),
    },
    {
      code: 'UC-206', name: '贷款查询统计', method: 'GET', path: '/api/loan/query',
      fields: [
        { n: 'ident', label: '身份标识', hint: '证件号/邮箱/手机号/账号/合同号，留空查全部' },
        { n: 'status', label: '状态', type: 'select', options: [{ value: '', label: '全部' }, { value: 'PENDING', label: '待审核' }, { value: 'APPROVED', label: '已批复' }, { value: 'ACTIVE', label: '存续中' }, { value: 'OVERDUE', label: '逾期' }, { value: 'PAID_OFF', label: '已结清' }, { value: 'REJECTED', label: '已拒绝' }, { value: 'SUPPLEMENT', label: '待补件' }] },
        { n: 'loan_type', label: '贷款类型' }, { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      result: d => kv({ '笔数': d.stats.count, '申请总额': money(d.stats.total_amount), '剩余本金': money(d.stats.total_balance), '逾期笔数': d.stats.overdue_count, '已结清': d.stats.paid_count })
        + (d.loans.length ? tbl(d.loans, [{ k: 'contract_no', label: '合同号' }, { k: 'loan_type', label: '类型' }, { k: 'amount', label: '金额', fmt: money }, { k: 'balance', label: '余额', fmt: money }, { k: 'status_label', label: '状态' }]) : `<p class="hint">${d.hint || ''}</p>`),
    },
  ],

  // ================= 外汇业务员 =================
  FOREX_CLERK: [
    {
      code: 'UC-301', name: '外汇账户开立', method: 'POST', path: '/api/forex/open-subaccount',
      fields: [{ n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' }, { n: 'currency', label: '外币币种', type: 'select', options: CURRENCIES }],
      result: d => kv({ '外汇账号': d.fx_account.fx_account_no, '币种': d.fx_account.currency, '关联储蓄账号': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-306', name: '实时汇率查询', method: 'GET', path: '/api/forex/live-rate',
      fields: [
        { n: 'currency', label: '币种', type: 'select', options: CURRENCIES, required: true },
      ],
      hint: '选择币种查询其实时行情（Alpha Vantage 实时；买入价/卖出价一并显示）。外汇买卖即按此牌价换算',
      result: d => tbl(d.rates, [
        { k: 'currency', label: '币种' }, { k: 'mid', label: '实时中间价' },
        { k: 'buy', label: '买入价(客户卖出)' }, { k: 'sell', label: '卖出价(客户买入)' },
        { k: 'as_of', label: '行情时间', fmt: (v, r) => r.error || v || '' },
      ]) + (d.note ? `<p class="hint">${esc(d.note)}</p>` : ''),
    },
    {
      code: 'UC-303', name: '外汇买卖确认', method: 'POST', path: '/api/forex/trade',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号/外汇子户号，任填其一' },
        { n: 'currency', label: '外币币种', type: 'select', options: CURRENCIES, required: true },
        { n: 'direction', label: '方向', type: 'select', options: [{ value: 'BUY', label: '客户买入外币' }, { value: 'SELL', label: '客户卖出外币' }] },
        { n: 'amount', label: '外币金额', type: 'number', required: true },
      ],
      result: d => kv({ '交易方向': d.direction === 'BUY' ? '客户买入外币' : '客户卖出外币', '外币金额': money(d.foreign) + ' ' + (d.currency || ''), '适用汇率': d.rate + '（' + fxRateType(d.rate_type) + '）', '人民币金额': money(d.cny_amount) + ' 元 (CNY)', '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-304', name: '外汇账户变更', method: 'POST', path: '/api/forex/change',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/外汇子户号，任填其一' },
        { n: 'currency', label: '外币币种', type: 'select', options: [{ value: '', label: '（单一外汇账户可不选）' }].concat(CURRENCIES), hint: '客户有多个外汇账户时选币种定位' },
        { n: 'change_type', label: '变更类型', type: 'select', options: [{ value: 'FREEZE', label: '冻结' }, { value: 'UNFREEZE', label: '解冻' }, { value: 'CLOSE', label: '注销外汇账户' }] },
        { n: 'reason', label: '变更原因' },
      ],
      result: d => kv({ '外汇账号': d.fx_account.fx_account_no, '状态': d.fx_account.status_label, '关联储蓄账号': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-305', name: '余额与历史查询', method: 'GET', path: '/api/forex/query',
      fields: [{ n: 'ident', label: '身份标识', hint: '证件号/邮箱/手机号/账号/卡号/外汇子户号，任填其一' }, { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' }],
      result: d => tbl(d.fx_accounts, [{ k: 'fx_account_no', label: '外汇账号' }, { k: 'customer_name', label: '客户' }, { k: 'currency', label: '币种' }, { k: 'balance', label: '余额', fmt: money }, { k: 'status_label', label: '状态' }, { k: 'base_account_no', label: '关联储蓄账号' }])
        + '<h4>交易历史</h4>' + (d.history.length ? tbl(d.history, [{ k: 'txn_time', label: '时间' }, { k: 'business_label', label: '类型' }, { k: 'currency', label: '币种' }, { k: 'amount', label: '外币金额', fmt: money }, { k: 'fx_rate', label: '汇率' }, { k: 'cny_amount', label: '本币金额', fmt: money }]) : `<p class="hint">${d.hint || '无记录'}</p>`),
    },
  ],

  // ================= 信用卡业务员（模仿汇丰香港：4 种卡 / 消费返现+积分 / 积分商城）=================
  CREDIT_CARD_CLERK: [
    {
      code: 'UC-401', name: '信用卡申请办理', method: 'POST', path: '/api/creditcard/apply',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '卡片类型', type: 'select', options: CARD_TYPES },
        { n: 'occupation', label: '职业' }, { n: 'monthly_income', label: '月收入', type: 'number' },
      ],
      hint: '每人每种卡最多一张。各卡种权益见下方对比表。',
      // 表单按钮下方：四种卡的卡组织/返现/积分等优惠对比，供柜员向客户介绍
      footer: () => '<h4>卡种权益对比</h4>' + tbl(CARD_BENEFITS, [
        { k: 'type', label: '卡种' },
        { k: 'network', label: '卡组织' },
        { k: 'currency', label: '计价币种' },
        { k: 'limit', label: '默认授信额度', fmt: money },
        { k: 'cashback', label: '消费返现' },
        { k: 'points', label: '消费积分' },
        { k: 'fxfee', label: '外币交易费' },
      ]) + '<p class="hint">返现按消费金额折本卡币种后计算，实时入客户人民币储蓄账户；积分归客户账户所有、该客户名下各卡消费累计共享，可在「UC-407 积分商城」兑换机票/酒店/接机专车等奖品。外币交易费在消费币种与本卡币种不一致时收取，MasterCard World Elite 免除。</p>',
      result: d => kv({ '卡号': d.credit_card.card_no, '卡种': d.credit_card.card_type, '卡组织': d.credit_card.network, '币种': d.credit_card.currency, '状态': d.credit_card.status_label }),
    },
    {
      code: 'UC-402', name: '审批（新卡 / 提额 同一处）', method: 'POST', path: '/api/creditcard/approve',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' },
        { n: 'card_type', label: '卡种', type: 'select', options: CARD_TYPES, required: true },
        { n: 'decision', label: '审批结论', type: 'select', options: [{ value: '', label: '请选择审批结论' }, { value: 'APPROVED', label: '通过' }, { value: 'REJECTED', label: '拒绝' }] },
        { n: 'bill_day', label: '账单日', type: 'number', hint: '新卡审批填，每月几号出账单 1-28' },
        { n: 'repay_day', label: '还款日', type: 'number', hint: '新卡审批填，每月几号前还款 1-28' },
        { n: 'reason', label: '拒绝原因', hint: '仅拒绝时填写' },
      ],
      // 第一步：填身份+选卡种 →「确认审批事项」，带出客户是要批卡还是提额、以及提额的审批依据与建议；
      // 第二步：给出审批结论 →「提交审批结论」
      lookup: {
        byField: 'card_type',
        btnLabel: '确认审批事项',
        path: '/api/creditcard/approve-quote',
        query: v => ({ ident: v.ident, card_type: v.card_type }),
        find: q => q,
        okMsg: '已带出审批事项，请核对后选择审批结论并「提交审批结论」',
        // 按行展示：审批事项 + 卡信息；提额另带 当前额度/申请后额度/对应账户余额/上限/建议(红绿)
        render: q => {
          const cur = esc(q.currency);
          const rows = [
            ['审批事项', q.purpose === 'NONE' ? `<span class="bad">${esc(q.purpose_label)}</span>` : `<strong>${esc(q.purpose_label)}</strong>`],
            ['卡号', esc(q.credit_card.card_no)],
            ['卡种', `${esc(q.credit_card.card_type)}（${cur}）`],
          ];
          if (q.purpose === 'NEW_CARD') {
            rows.push(['卡片状态', esc(q.credit_card.status_label)]);
            rows.push(['通过后授予额度', `${esc(money(q.grant_limit))} ${cur}　<span class="hint-inline">按卡种默认额度</span>`]);
          } else if (q.purpose === 'INCREASE') {
            rows.push(['当前额度', `${esc(money(q.current_limit))} ${cur}`]);
            rows.push(['申请后额度', `${esc(money(q.new_limit))} ${cur}`]);
            rows.push([q.currency === 'CNY' ? '人民币储蓄账户余额' : '美元外汇子户余额',
              q.acct_no ? `${esc(q.acct_no)}　${esc(money(q.acct_balance))} ${cur}`
                : `<span class="bad">未找到对应币种账户</span>`]);
            rows.push([`提额上限（存款×${esc((q.ratio * 100).toFixed(0))}%）`,
              `${esc(money(q.cap_cny))} CNY　<span class="hint-inline">存款合计折 ${esc(money(q.deposit_cny))} CNY；申请额度折 ${q.new_limit_cny == null ? '—' : esc(money(q.new_limit_cny))} CNY</span>`]);
            const cls = q.advise === 'APPROVE' ? 'good' : 'bad';
            rows.push(['审批建议', `<span class="${cls}">${esc(q.advise_label)}</span>　<span class="hint-inline">${esc(q.advise_reason)}</span>`]);
          } else {
            rows.push(['说明', `<span class="bad">${esc(q.note || '')}</span>`]);
          }
          return kvRows(rows);
        },
      },
      hint: '两步：① 填身份、选卡种后点「确认审批事项」，带出客户是申请新卡还是提额（提额会给出额度/余额对比与红绿建议）；② 再选审批结论点「提交审批结论」。新卡按卡种默认额度激活；提额新额度不得高于存款的 30%。',
      result: d => {
        if (!d.credit_card) return '';
        const c = d.credit_card, base = { '卡号': c.card_no, '卡种': c.card_type, '状态': c.status_label };
        if (c.reject_reason) base['拒绝原因'] = c.reject_reason;
        base['授信额度'] = money(c.credit_limit) + ' ' + c.currency;
        base['可用额度'] = money(c.available_limit) + ' ' + c.currency;
        if (c.bill_day) { base['账单日'] = c.bill_day; base['还款日'] = c.repay_day; }
        if (c.limit_req) base['提额申请'] = money(c.limit_req.new_limit) + ' / ' + c.limit_req.status;
        return kv(base);
      },
    },
    {
      code: 'UC-403', name: '提高信用额申请', method: 'POST', path: '/api/creditcard/increase-limit',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '卡种', type: 'select', options: CARD_TYPES, required: true },
        { n: 'new_limit', label: '新授信额度', type: 'number', required: true, hint: '须高于当前额度；审批时不得超过存款的 30%' },
        { n: 'reason', label: '申请理由' },
      ],
      result: d => kv({ '卡号': d.credit_card.card_no, '当前额度': money(d.credit_card.credit_limit) + ' ' + d.credit_card.currency, '申请额度': d.credit_card.limit_req ? money(d.credit_card.limit_req.new_limit) + ' ' + d.credit_card.currency : '-', '申请状态': d.credit_card.limit_req ? d.credit_card.limit_req.status : '-' }),
    },
    {
      code: 'UC-404', name: '模拟消费（自定义币种+金额）', method: 'POST', path: '/api/creditcard/consume',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '消费卡种', type: 'select', options: CARD_TYPES, required: true },
        { n: 'currency', label: '消费币种', type: 'select', options: [{ value: 'CNY', label: '人民币(CNY)' }].concat(CURRENCIES) },
        { n: 'amount', label: '消费金额', type: 'number', required: true },
        { n: 'merchant', label: '商户/备注' },
      ],
      hint: '消费金额按实时汇率折算为本卡币种扣减可用额度；跨币种收外币交易费（银联1%/Visa1.95%，World Elite 免除）。银联卡返现入人民币储蓄账户，Visa/万事达返积分。',
      result: d => kv({ '原始消费': d.orig, '折本卡': money(d.card_amount) + ' ' + d.card_currency, '外币交易费': money(d.fee) + ' ' + d.card_currency, '奖励': d.reward.type === 'CASHBACK' ? ('返现 ' + money(d.reward.cashback) + ' 元→' + d.reward.account_no) : (d.reward.type === 'POINTS' ? ('积分 +' + d.reward.points) : '无'), '剩余可用额度': money(d.available_limit) + ' ' + d.card_currency, '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-405', name: '还款处理（提前/按期最低额）', method: 'POST', path: '/api/creditcard/repay',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '还款卡种', type: 'select', options: CARD_TYPES, required: true },
        { n: 'repay_type', label: '还款方式', type: 'select', options: [{ value: 'FULL', label: '提前还款(全额结清)' }, { value: 'SCHEDULED', label: '提前还款(指定金额)' }, { value: 'MIN', label: '按期最低额还款' }] },
        { n: 'amount', label: '还款金额（提前还款(指定金额)时填）', type: 'number' },
      ],
      // 第一步：填身份+选卡种 →「确认应还金额」，各方式金额显示进还款方式下拉、明细按行展示；
      // 第二步：在下拉里看着金额选还款方式 →「确认还款」
      lookup: {
        byField: 'card_type',
        btnLabel: '确认应还金额',
        path: '/api/creditcard/repay-quote',
        query: v => ({ ident: v.ident, card_type: v.card_type }),
        find: q => q,
        fill: q => ({ amount: q.outstanding > 0 ? q.outstanding : '' }),  // 指定金额默认带出全额，可改
        okMsg: '应还金额已带出：请在「还款方式」下拉中查看各方式应还多少，选定后点「确认还款」',
        // 把应还金额写进还款方式下拉，选的时候就能看到具体数字
        options: q => ({
          repay_type: q.outstanding > 0 ? [
            { value: 'FULL', label: `提前还款(全额结清)　应还 ${money(q.outstanding)} ${q.currency}` },
            { value: 'SCHEDULED', label: `提前还款(指定金额)　不低于最低额 ${money(q.min_amount)} ${q.currency}` },
            { value: 'MIN', label: `按期最低额还款　应还 ${money(q.min_amount)} ${q.currency}（剩余本金本月计息约 ${money(q.min_interest)} ${q.currency}）` },
          ] : [
            { value: 'FULL', label: '提前还款(全额结清)　当前无欠款' },
            { value: 'SCHEDULED', label: '提前还款(指定金额)　当前无欠款' },
            { value: 'MIN', label: '按期最低额还款　当前无欠款' },
          ],
        }),
        // 按行展示：卡号/卡种/提前全额/按期最低/还款账户余额（不足标红）。数据一律 esc 后拼接。
        render: q => {
          const cur = esc(q.currency), has = q.outstanding > 0;
          const acctLabel = q.currency === 'CNY' ? '人民币储蓄账户余额' : '美元外汇子户余额';
          let bal;
          if (!q.fund_account) {
            bal = `<span class="bad">未找到可用${q.currency === 'CNY' ? '人民币储蓄账户' : '美元外汇子户'}，无法还款</span>`;
          } else {
            bal = `${esc(q.fund_account)}　${esc(money(q.fund_balance))} ${cur}`;
            if (has && q.fund_balance < q.min_amount) bal += ` <span class="bad">余额不足：连最低还款额 ${esc(money(q.min_amount))} ${cur} 都不够</span>`;
            else if (has && q.fund_balance < q.outstanding) bal += ` <span class="bad">余额不足以全额还款（可选按期最低额还款）</span>`;
          }
          return kvRows([
            ['卡号', esc(q.credit_card.card_no)],
            ['卡种', `${esc(q.credit_card.card_type)}（${cur}）`],
            ['提前还款(全额结清)', has ? `${esc(money(q.outstanding))} ${cur}` : '当前无欠款，无需还款'],
            ['按期最低额还款', has ? `${esc(money(q.min_amount))} ${cur}　<span class="hint-inline">剩余本金本月计息约 ${esc(money(q.min_interest))} ${cur}</span>` : '当前无欠款，无需还款'],
            [acctLabel, bal],
          ]);
        },
      },
      hint: '两步：① 填身份、选卡种后点「确认应还金额」，带出该卡各方式应还多少；② 再选还款方式点「确认还款」。人民币卡用人民币储蓄账户还、美元卡用美元外汇子户还；多还部分退回；最低额还款的剩余本金按月利率 5% 计息。',
      result: d => kv({ '卡号': d.credit_card.card_no, '本次还款': money(d.pay) + ' ' + d.currency, '还款来源': d.fund, '循环利息': money(d.interest) + ' ' + d.currency, '剩余欠款': money(d.outstanding) + ' ' + d.currency, '可用额度': money(d.available_limit) + ' ' + d.currency }),
    },
    {
      code: 'UC-406', name: '本月消费记录', method: 'GET', path: '/api/creditcard/records',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '卡种', type: 'select', options: CARD_TYPES, required: true },
      ],
      result: d => kv({ '卡号': d.credit_card.card_no, '卡种': d.credit_card.card_type, '账月': d.month, '本月消费合计': money(d.consume_total) + ' ' + d.credit_card.currency, '授信额度': money(d.credit_card.credit_limit) + ' ' + d.credit_card.currency, '剩余可用额度': money(d.credit_card.available_limit) + ' ' + d.credit_card.currency })
        + (d.records.length ? '<h4>本月明细</h4>' + tbl(d.records, [{ k: 'txn_time', label: '时间' }, { k: 'business_label', label: '类型' }, { k: 'amount', label: '金额', fmt: money }, { k: 'currency', label: '币种' }, { k: 'orig', label: '原始消费' }, { k: 'merchant', label: '商户' }]) : '<p>本月暂无记录</p>'),
    },
    {
      code: 'UC-407', name: '积分商城', method: 'GET', path: '/api/creditcard/mall',
      fields: [{ n: 'ident', label: '身份标识（查积分/兑换记录）', hint: '证件号/邮箱/手机号/账号/卡号，任填其一；留空只看奖品' }],
      result: d => (d.customer_name ? kv({ '客户': d.customer_name, '当前积分': d.points }) : '')
        + '<h4>奖品清单</h4>' + tbl(d.prizes, [{ k: 'name', label: '奖品' }, { k: 'points', label: '所需积分' }, { k: 'desc', label: '说明' }])
        + (d.redemptions && d.redemptions.length ? '<h4>兑换记录</h4>' + tbl(d.redemptions, [{ k: 'time', label: '时间' }, { k: 'prize_name', label: '奖品' }, { k: 'points', label: '积分' }]) : ''),
    },
    {
      code: 'UC-408', name: '积分兑换奖品', method: 'POST', path: '/api/creditcard/redeem',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'prize_id', label: '兑换奖品', type: 'select', options: [{ value: 'FLIGHT_INTL', label: '国际机票兑换券(80000)' }, { value: 'FLIGHT_DOM', label: '国内机票兑换券(40000)' }, { value: 'HOTEL_5S', label: '五星酒店住宿券(30000)' }, { value: 'PICKUP_CAR', label: '接机专车服务券(15000)' }, { value: 'LOUNGE', label: '机场贵宾厅通行券(8000)' }] },
      ],
      result: d => kv({ '兑换奖品': d.prize, '消耗积分': d.cost, '剩余积分': d.points_remain }),
    },
    {
      code: 'UC-409', name: '挂失/补卡/冻结/异常', method: 'POST', path: '/api/creditcard/card',
      fields: [
        { n: 'ident', label: '身份标识', required: true, hint: '证件号/邮箱/手机号/账号/卡号，任填其一' },
        { n: 'card_type', label: '卡种', type: 'select', options: CARD_TYPES, required: true },
        { n: 'op', label: '操作', type: 'select', options: [{ value: 'LOSS', label: '挂失' }, { value: 'REISSUE', label: '补卡' }, { value: 'FREEZE', label: '冻结' }, { value: 'UNFREEZE', label: '解冻' }, { value: 'EXCEPTION', label: '异常登记' }] },
        { n: 'note', label: '异常说明' },
      ],
      result: d => d.card_no ? kv({ '当前卡号': d.card_no }) : '',
    },
    {
      code: 'UC-4Q', name: '信用卡查询', method: 'GET', path: '/api/creditcard/query',
      fields: [{ n: 'ident', label: '身份标识', hint: '证件号/邮箱/手机号/账号/卡号，任填其一' }],
      result: d => d.cards.map(c => kv({ '卡号': c.card_no, '卡种': c.card_type, '卡组织': c.network, '币种': c.currency, '客户': c.customer_name || '-', '积分': c.points, '状态': c.status_label, '授信额度': money(c.credit_limit) + ' ' + c.currency, '可用额度': money(c.available_limit) + ' ' + c.currency, '已用额度': money(c.used) + ' ' + c.currency, '返现比例': pct(c.cashback_rate), '每单位积分': c.points_per_unit, '外币交易费': c.waive_fx_fee ? '免除' : pct(c.fx_fee_rate), '提额申请': c.limit_req ? (money(c.limit_req.new_limit) + ' / ' + c.limit_req.status) : '无', '可用还款账户': (c.repay_accounts && c.repay_accounts.length ? c.repay_accounts.join('、') : '（暂无）') })).join('<hr>'),
    },
  ],

  // ================= 理财业务员 =================
  INVEST_CLERK: [
    {
      code: 'UC-601', name: '理财产品列表', method: 'GET', path: '/api/invest/products',
      fields: [],
      result: d => d.products.length ? tbl(d.products, [
        { k: 'code', label: '代码' }, { k: 'name', label: '名称' }, { k: 'ptype_label', label: '类型' },
        { k: 'risk_label', label: '风险' }, { k: 'currency', label: '币种' },
        { k: 'price_cny', label: '最新净值/价(CNY)', fmt: v => v == null ? '-' : money(v) },
        { k: 'price_date', label: '价格日期', fmt: v => v || '待刷新' },
      ]) : `<p class="hint">${d.hint || '暂无产品'}</p>`,
    },
    {
      code: 'UC-602', name: '实时行情查询', method: 'GET', path: '/api/invest/quote',
      fields: [{ n: 'product_code', label: '产品代码', required: true, hint: '如 000001 / 110020 / 000198 / AAPL' }],
      result: d => kv({ '产品': d.name + '（' + d.code + '）', '类型': d.ptype_label, '计价币种': d.currency, '本币价': d.price_local, '折CNY价': money(d.price_cny), '汇率': d.fx_rate, '行情日期': d.date + (d.stale ? '（非当日·参考）' : ''), '数据源': d.source_label })
        + (d.stale ? '<p class="hint">当前为最近可得行情，非当日实时，可点「行情刷新」更新</p>' : ''),
    },
    {
      code: 'UC-603', name: '行情刷新(每日)', method: 'POST', path: '/api/invest/refresh-prices',
      fields: [],
      hint: '拉取全部在售产品的当日行情（每天更新一次即可；查询时也会懒加载补当日价）',
      result: d => `<p>${d.hint}</p>` + (d.updated.length ? tbl(d.updated, [{ k: 'code', label: '代码' }, { k: 'name', label: '名称' }, { k: 'price_cny', label: '当日价(CNY)', fmt: money }, { k: 'date', label: '日期' }]) : '')
        + (d.failed && d.failed.length ? `<p class="hint">失败：${d.failed.map(f => esc(f.code + ' ' + (f.reason || ''))).join('；')}</p>` : ''),
    },
    {
      code: 'UC-604', name: '风险测评', method: 'POST', path: '/api/invest/assess',
      fields: [
        { n: 'ident', label: '客户身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' },
        { n: 'q1', label: '1.投资年限', type: 'select', options: [{ value: '1', label: '1年内' }, { value: '2', label: '1-3年' }, { value: '3', label: '3-5年' }, { value: '4', label: '5-10年' }, { value: '5', label: '10年以上' }] },
        { n: 'q2', label: '2.可承受最大亏损', type: 'select', options: [{ value: '1', label: '几乎不能亏' }, { value: '2', label: '5%以内' }, { value: '3', label: '10%以内' }, { value: '4', label: '30%以内' }, { value: '5', label: '30%以上' }] },
        { n: 'q3', label: '3.投资经验', type: 'select', options: [{ value: '1', label: '无' }, { value: '2', label: '很少' }, { value: '3', label: '一般' }, { value: '4', label: '丰富' }, { value: '5', label: '专业' }] },
        { n: 'q4', label: '4.收入稳定性', type: 'select', options: [{ value: '1', label: '很不稳定' }, { value: '2', label: '较不稳定' }, { value: '3', label: '一般' }, { value: '4', label: '较稳定' }, { value: '5', label: '非常稳定' }] },
        { n: 'q5', label: '5.风险偏好', type: 'select', options: [{ value: '1', label: '保守' }, { value: '2', label: '稳健' }, { value: '3', label: '平衡' }, { value: '4', label: '进取' }, { value: '5', label: '激进' }] },
      ],
      result: d => kv({ '客户': d.name + '（' + d.customer_no + '）', '风险承受等级': d.risk_label + '（' + d.risk_level + '级）' }),
    },
    {
      code: 'UC-605', name: '基金/股票申购', method: 'POST', path: '/api/invest/buy',
      fields: [
        { n: 'ident', label: '客户身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' },
        { n: 'product_code', label: '产品代码', required: true, hint: '如 000001 / 110020 / AAPL' },
        { n: 'amount', label: '申购金额(元)', type: 'number', required: true, hint: '从客户储蓄账户扣款，按当日净值折算份额' },
      ],
      result: d => kv({ '产品': d.product + (d.ptype ? '（' + d.ptype + '）' : ''), '成交金额': money(d.amount),
        '手续费': money(d.fee) + '（' + feeStr(d.fee_detail) + '）', '实付合计': money(d.total_debit),
        '成交净值(CNY)': money(d.price_cny), '确认份额': d.units, '份额确认日(T+1)': d.confirm_date,
        '行情日期': d.price_date, '流水号': d.txn_no }),
    },
    {
      code: 'UC-606', name: '基金/股票赎回', method: 'POST', path: '/api/invest/sell',
      fields: [
        { n: 'ident', label: '客户身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' },
        { n: 'product_code', label: '产品代码', required: true },
        { n: 'units', label: '赎回份额', type: 'number', hint: '全部赎回可勾选下方，或填份额' },
        { n: 'all', label: '全部赎回', type: 'checkbox' },
      ],
      result: d => kv({ '产品': d.product + (d.ptype ? '（' + d.ptype + '）' : ''), '赎回份额': d.units,
        '成交净值(CNY)': money(d.price_cny), '成交金额': money(d.amount_gross),
        '手续费': money(d.fee) + '（' + feeStr(d.fee_detail) + '）', '实收到账': money(d.proceeds),
        '本次已实现盈亏': money(d.realized), '资金到账日(T+1)': d.settle_date, '流水号': d.txn_no }),
      validate: v => (!v.all && !(Number(v.units) > 0)) ? '请填写赎回份额，或勾选「全部赎回」' : null,
    },
    {
      code: 'UC-607', name: '持仓与盈亏查询', method: 'GET', path: '/api/invest/holdings',
      fields: [{ n: 'ident', label: '客户身份标识', required: true, hint: '证件号/邮箱/手机号/账号，任填其一' }],
      result: d => kv({ '客户': d.customer.name + '（' + d.customer.customer_no + '）', '风险等级': d.customer.risk_label, '总市值': money(d.summary.total_market_value), '总成本': money(d.summary.total_cost), '浮动盈亏': money(d.summary.total_unrealized), '已实现盈亏': money(d.summary.total_realized), '累计总盈亏': money(d.summary.total_pnl) })
        + (d.holdings.length ? '<h4>持仓明细</h4>' + tbl(d.holdings, [
          { k: 'name', label: '产品' }, { k: 'units', label: '份额' },
          { k: 'price_cny', label: '现价(CNY)', fmt: v => v == null ? '-' : money(v) },
          { k: 'market_value', label: '市值', fmt: money }, { k: 'cost', label: '成本', fmt: money },
          { k: 'unrealized', label: '浮动盈亏', fmt: money }, { k: 'unrealized_pct', label: '浮动%', fmt: v => v == null ? '-' : pct(v) },
          { k: 'cumulative', label: '累计盈亏', fmt: money },
          { k: 'day_pct', label: '日', fmt: v => v == null ? '-' : pct(v) }, { k: 'week_pct', label: '周', fmt: v => v == null ? '-' : pct(v) },
          { k: 'month_pct', label: '月', fmt: v => v == null ? '-' : pct(v) }, { k: 'year_pct', label: '年', fmt: v => v == null ? '-' : pct(v) },
        ]) + '<p class="hint">日/周/月/年为价格变动幅度（不含期间买卖现金流）；标「非当日」的价为最近可得行情</p>' : `<p class="hint">${d.hint || '暂无持仓'}</p>`),
    },
    {
      code: 'UC-608', name: '理财产品维护', method: 'POST', path: '/api/invest/product',
      fields: [
        { n: 'code', label: '产品代码', required: true, hint: '基金填基金代码(如000001)，股票填美股代码(如AAPL)' },
        { n: 'name', label: '产品名称', required: true },
        { n: 'ptype', label: '类型', type: 'select', options: [{ value: 'FUND', label: '基金(人民币,天天基金)' }, { value: 'STOCK', label: '股票(美股USD,自动折CNY)' }] },
        { n: 'market_symbol', label: '行情代码', required: true, hint: '一般同产品代码' },
        { n: 'risk_level', label: '风险等级', type: 'select', options: [{ value: '1', label: '1-低' }, { value: '2', label: '2-中低' }, { value: '3', label: '3-中' }, { value: '4', label: '4-中高' }, { value: '5', label: '5-高' }] },
        { n: 'status', label: '状态', type: 'select', options: [{ value: '1', label: '在售' }, { value: '0', label: '停售' }] },
      ],
      hint: '维护理财产品目录；股票默认按美股(USD)取价并折算人民币',
      result: d => kv({ '产品代码': d.code, '名称': d.name }),
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
        { n: 'role', label: '角色', type: 'select', options: [{ value: 'SAVINGS_CLERK', label: '储蓄业务员' }, { value: 'LOAN_CLERK', label: '贷款业务员' }, { value: 'FOREX_CLERK', label: '外汇业务员' }, { value: 'CREDIT_CARD_CLERK', label: '信用卡业务员' }, { value: 'INVEST_CLERK', label: '理财业务员' }, { value: 'ADMIN', label: '系统管理员' }] },
        { n: 'password', label: '初始密码', type: 'password', required: true },
      ],
      result: d => kv({ '工号': d.user.employee_no, '姓名': d.user.name, '角色': d.user.role_label }),
    },
    {
      code: 'UC-501c', name: '修改/停用用户', method: 'POST', path: '/api/admin/users/update',
      fields: [
        { n: 'employee_no', label: '工号', required: true }, { n: 'name', label: '新姓名' },
        { n: 'role', label: '新角色', type: 'select', options: [{ value: '', label: '不变' }, { value: 'SAVINGS_CLERK', label: '储蓄业务员' }, { value: 'LOAN_CLERK', label: '贷款业务员' }, { value: 'FOREX_CLERK', label: '外汇业务员' }, { value: 'CREDIT_CARD_CLERK', label: '信用卡业务员' }, { value: 'INVEST_CLERK', label: '理财业务员' }, { value: 'ADMIN', label: '系统管理员' }] },
        { n: 'status', label: '状态', type: 'select', options: [{ value: '', label: '不变' }, { value: '1', label: '启用' }, { value: '0', label: '停用' }] },
        { n: 'password', label: '重置密码', type: 'password' },
      ],
      lookup: {
        byField: 'employee_no',
        path: '/api/admin/users',
        find: (d, key) => (d.users || []).find(u => u.employee_no === key),
        fill: u => ({ name: u.name, role: u.role, status: String(u.status) }),
        show: u => `当前 → 姓名：${u.name}　角色：${u.role_label}　状态：${u.status_label}`,
      },
      hint: '先输工号点「查询并回填」带出当前姓名/角色/状态，再修改需要变更的项。不能停用/降权当前登录的自己；系统须保留至少一名在用管理员。柜员忘记密码可在此重置。',
      result: d => kv({ '工号': d.user.employee_no, '姓名': d.user.name, '角色': d.user.role_label, '状态': d.user.status_label }),
    },
    {
      code: 'UC-502', name: '参数列表', method: 'GET', path: '/api/admin/params',
      fields: [],
      result: d => tbl(d.params, [
        { k: 'param_key', label: '参数名称', fmt: paramName },
        { k: 'param_type', label: '类型', fmt: paramTypeLabel },
        { k: 'param_value', label: '当前值', fmt: paramValue },
        { k: 'changed_at', label: '最近修改' },
      ]),
    },
    {
      code: 'UC-502b', name: '维护参数', method: 'POST', path: '/api/admin/params',
      fields: [
        { n: 'param_key', label: '选择参数', type: 'select', options: PARAM_OPTIONS },
        { n: 'param_value', label: '新的值', required: true, hint: '利率填小数（0.0435 表示 4.35%）；限额、汇率直接填数字' },
      ],
      hint: '选择要调整的参数，填入新值即可；改后立即生效，无需重启',
      result: d => `<p class="hint">保存成功，可到「参数列表」查看最新值</p>`,
    },
    {
      code: 'UC-503', name: '日志审计', method: 'GET', path: '/api/admin/audit',
      fields: [
        { n: 'employee_no', label: '操作人工号' },
        { n: 'action', label: '操作类型', type: 'select', options: ACTION_OPTIONS },
        { n: 'object_type', label: '对象类型', type: 'select', options: OBJECT_OPTIONS },
        { n: 'result', label: '结果', type: 'select', options: [{ value: '', label: '全部' }, { value: 'SUCCESS', label: '成功' }, { value: 'FAILURE', label: '失败' }] },
        { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' },
      ],
      result: d => d.logs.length ? tbl(d.logs, [{ k: 'created_at', label: '时间' }, { k: 'operator', label: '操作人' }, { k: 'action', label: '操作', fmt: actionLabel }, { k: 'object_type', label: '对象', fmt: objectLabel }, { k: 'object_id', label: '对象编号' }, { k: 'result', label: '结果', fmt: resultLabel }, { k: 'detail', label: '详情/原因', fmt: v => !v ? '' : (typeof v === 'object' ? (v.reason || Object.entries(v).map(([k, val]) => `${k}=${val && typeof val === 'object' ? JSON.stringify(val) : val}`).join('，')) : v) }]) : `<p class="hint">${d.hint || '无记录'}</p>`,
    },
    {
      code: 'UC-504', name: '数据备份(下载)', method: 'GET', path: '/api/admin/backup', type: 'download',
      fields: [], hint: '点击生成并下载数据库备份 JSON 文件',
    },
    {
      code: 'UC-504b', name: '数据恢复(上传)', method: 'POST', path: '/api/admin/restore', type: 'upload',
      fields: [{ n: 'confirm', label: '我已确认恢复风险', type: 'checkboxVal', value: 'true', required: true }],
      hint: '高风险操作：会用备份文件覆盖当前数据',
      result: d => kv(Object.fromEntries(Object.entries(d.restored || {}).map(([k, v]) => [objectLabel(k), v]))),
    },
  ],
};

// 所有角色通用的操作（追加到每个角色菜单末尾）
const COMMON = [
  {
    code: 'UC-000', name: '修改密码', method: 'POST', path: '/api/change-password',
    fields: [
      { n: 'old_password', label: '原密码', type: 'password', required: true },
      { n: 'new_password', label: '新密码', type: 'password', required: true, hint: '至少 6 位，且不能与原密码相同' },
      { n: 'confirm_new', label: '确认新密码', type: 'password', required: true },
    ],
    hint: '修改本人登录密码，成功后下次登录请使用新密码',
    validate: v => v.new_password !== v.confirm_new ? '两次输入的新密码不一致' : null,
  },
  {
    code: 'UC-00A', name: '我的经办记录', method: 'GET', path: '/api/my-activity',
    fields: [],
    hint: '查看本人近期经办的业务与操作（最多 200 条）',
    result: d => d.logs.length ? tbl(d.logs, [
      { k: 'created_at', label: '时间' }, { k: 'action', label: '操作', fmt: actionLabel },
      { k: 'object_type', label: '对象', fmt: objectLabel }, { k: 'object_id', label: '对象编号' },
      { k: 'result', label: '结果', fmt: resultLabel },
    ]) : `<p class="hint">${d.hint || '暂无经办记录'}</p>`,
  },
];
