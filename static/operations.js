// 声明式定义所有 29 个用例的表单与结果展示，按角色分组。
// 引擎(app.js)读取这里的配置自动渲染菜单、表单、提交与结果。改需求只需改这张表。

const ID_TYPES = ['身份证', '护照', '港澳通行证', '军官证'];
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
  FX_FREEZE: '外汇账户冻结', FX_UNFREEZE: '外汇账户解冻', FX_CLOSE: '外汇账户关闭', FX_REBIND: '外汇改绑账户', FX_QUERY: '外汇查询', FX_RATE_SYNC: '外汇实时挂牌',
  CC_APPLY: '信用卡申请', CC_APPROVE: '信用卡审批通过', CC_REJECT: '信用卡审批拒绝', CC_BILL: '信用卡账单生成',
  CC_REPAY: '信用卡还款', CC_CASH_ADVANCE: '预借现金',
  CC_LOSS: '信用卡挂失', CC_FREEZE: '信用卡冻结', CC_UNFREEZE: '信用卡解冻', CC_REISSUE: '信用卡补卡',
  CC_EXCEPTION: '信用卡异常登记', CC_CARD: '信用卡卡片操作',
  USER_CREATE: '新建操作员', USER_UPDATE: '操作员信息变更', PARAM_UPDATE: '系统参数修改',
  BACKUP: '数据备份', RESTORE: '数据恢复', CHANGE_PASSWORD: '修改密码',
};
const OBJECT_LABEL = {
  account: '储蓄账户', customer: '客户', loan: '贷款', fx_account: '外汇账户',
  credit_card: '信用卡', credit_card_bill: '信用卡账单', user_account: '操作员账号',
  system_param: '系统参数', database: '数据库', audit_log: '审计日志',
  business_transaction: '业务流水', counters: '业务编号',
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
        { n: 'phone', label: '手机号', pattern: '1[3-9]\\d{9}', patternMsg: '手机号应为 11 位大陆手机号' },
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
      fields: [{ n: 'account_no', label: '账号', required: true }, { n: 'id_no', label: '证件号', required: true, hint: '核验持卡人身份' }, { n: 'amount', label: '取款金额', type: 'number', required: true }],
      result: d => kv({ '当前余额': money(d.balance), '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-104', name: '转账汇款', method: 'POST', path: '/api/savings/transfer',
      fields: [
        { n: 'transfer_type', label: '转账类型', type: 'select', options: [{ value: 'INTRA', label: '本行转账（含本人账户互转）' }, { value: 'INTER', label: '跨行转账' }] },
        { n: 'from_account_no', label: '转出账号', required: true },
        { n: 'id_no', label: '转出方证件号', required: true, hint: '核验转出账户持有人身份' },
        { n: 'to_account_no', label: '收款账号', required: true },
        { n: 'to_bank', label: '收款方开户银行', hint: '仅跨行转账需填写' },
        { n: 'amount', label: '转账金额', type: 'number', required: true },
      ],
      result: d => kv({ '转账方式': d.sub, '手续费': money(d.fee), '转出后余额': money(d.balance), '流水号': d.txn.txn_no }),
      validate: v => v.transfer_type === 'INTER' && !v.to_bank ? '跨行转账请填写收款方开户银行' : null,
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
        { n: 'phone', label: '新手机号', pattern: '1[3-9]\\d{9}', patternMsg: '手机号应为 11 位大陆手机号' }, { n: 'address', label: '新联系地址' }, { n: 'occupation', label: '职业' },
        { n: 'name', label: '变更姓名（重要信息）', hint: '修改姓名/证件号须勾选下方“二次确认”' }, { n: 'new_id_no', label: '变更证件号（重要信息）' },
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
      fields: [{ n: 'contract_no', label: '合同号', required: true }, { n: 'amount', label: '还款金额', type: 'number', required: true }, { n: 'account_no', label: '还款账号', hint: '留空取合同账户' }, { n: 'id_no', label: '证件号', required: true, hint: '核验扣款账户持有人身份' }],
      result: d => kv({ '合同号': d.loan.contract_no, '状态': d.loan.status_label, '剩余本金': money(d.loan.balance), '应收罚息': money(d.loan.penalty_due) }),
    },
    {
      code: 'UC-205', name: '逾期查询', method: 'GET', path: '/api/loan/overdue',
      fields: [{ n: 'days', label: '逾期天数不少于', type: 'number', hint: '留空查全部逾期' }, { n: 'customer_no', label: '客户号' }, { n: 'contract_no', label: '合同号' }],
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
        { n: 'customer_no', label: '客户号' }, { n: 'contract_no', label: '合同号' },
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
      fields: [{ n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号', hint: '客户号/证件号任填其一' }, { n: 'currency', label: '外币币种', type: 'select', options: CURRENCIES }],
      result: d => kv({ '外汇账号': d.fx_account.fx_account_no, '币种': d.fx_account.currency, '关联储蓄账号': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-302', name: '汇率查询与确认', method: 'GET', path: '/api/forex/rate',
      fields: [{ n: 'currency', label: '币种', type: 'select', options: CURRENCIES }, { n: 'direction', label: '交易方向', type: 'select', options: [{ value: 'BUY', label: '客户买入外币' }, { value: 'SELL', label: '客户卖出外币' }] }],
      result: d => kv({ '币种': d.currency, '买入价': d.buy_rate, '卖出价': d.sell_rate, '本次适用': d.apply_rate + '（' + fxRateType(d.apply_rate_type) + '）', '生效时间': d.effective_at, '说明': d.note }),
    },
    {
      code: 'UC-306', name: '实时汇率查询', method: 'GET', path: '/api/forex/live-rate',
      fields: [{ n: 'currency', label: '币种', type: 'select', options: [{ value: '', label: '全部币种' }].concat(CURRENCIES) }],
      hint: '调取 Alpha Vantage 实时行情，按点差(FX_SPREAD)算出挂牌买卖价；仅查看，不改牌价',
      result: d => tbl(d.rates, [
        { k: 'currency', label: '币种' }, { k: 'mid', label: '实时中间价' },
        { k: 'buy', label: '买入价(客户卖出)' }, { k: 'sell', label: '卖出价(客户买入)' },
        { k: 'source', label: '来源/说明', fmt: (v, r) => r.error || v || '' },
      ]) + (d.note ? `<p class="hint">${esc(d.note)}</p>` : ''),
    },
    {
      code: 'UC-307', name: '实时行情挂牌', method: 'POST', path: '/api/forex/sync-rate',
      fields: [{ n: 'currency', label: '币种', type: 'select', options: [{ value: '', label: '全部币种' }].concat(CURRENCIES) }],
      hint: '按实时行情更新牌价（写入系统参数），之后外汇买卖即按新牌价换算',
      result: d => tbl(d.updated, [
        { k: 'currency', label: '币种' }, { k: 'mid', label: '中间价' },
        { k: 'buy', label: '买入价' }, { k: 'sell', label: '卖出价' },
      ]) + (d.failed && d.failed.length ? `<p class="hint">未挂牌：${d.failed.map(f => esc(f.currency + ' ' + f.reason)).join('；')}</p>` : ''),
    },
    {
      code: 'UC-303', name: '外汇买卖确认', method: 'POST', path: '/api/forex/trade',
      fields: [
        { n: 'fx_account_no', label: '外汇账号', required: true },
        { n: 'id_no', label: '证件号', required: true, hint: '核验持卡人身份' },
        { n: 'direction', label: '方向', type: 'select', options: [{ value: 'BUY', label: '客户买入外币' }, { value: 'SELL', label: '客户卖出外币' }] },
        { n: 'amount', label: '外币金额', type: 'number', required: true },
      ],
      result: d => kv({ '本币金额': money(d.cny_amount), '汇率': d.rate + '（' + fxRateType(d.rate_type) + '）', '流水号': d.txn.txn_no }),
    },
    {
      code: 'UC-304', name: '外汇账户变更', method: 'POST', path: '/api/forex/change',
      fields: [
        { n: 'fx_account_no', label: '外汇账号', required: true },
        { n: 'change_type', label: '变更类型', type: 'select', options: [{ value: 'FREEZE', label: '冻结' }, { value: 'UNFREEZE', label: '解冻' }, { value: 'CLOSE', label: '注销外汇账户' }, { value: 'REBIND', label: '更换关联储蓄账号' }] },
        { n: 'new_base_account_no', label: '新的关联储蓄账号', hint: '更换关联时填' }, { n: 'reason', label: '变更原因' },
      ],
      result: d => kv({ '外汇账号': d.fx_account.fx_account_no, '状态': d.fx_account.status_label, '关联储蓄账号': d.fx_account.base_account_no }),
    },
    {
      code: 'UC-305', name: '余额与历史查询', method: 'GET', path: '/api/forex/query',
      fields: [{ n: 'fx_account_no', label: '外汇账号' }, { n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号' }, { n: 'start', label: '起始日期', type: 'date' }, { n: 'end', label: '结束日期', type: 'date' }],
      result: d => tbl(d.fx_accounts, [{ k: 'fx_account_no', label: '外汇账号' }, { k: 'customer_name', label: '客户' }, { k: 'currency', label: '币种' }, { k: 'balance', label: '余额', fmt: money }, { k: 'status_label', label: '状态' }, { k: 'base_account_no', label: '关联储蓄账号' }])
        + '<h4>交易历史</h4>' + (d.history.length ? tbl(d.history, [{ k: 'txn_time', label: '时间' }, { k: 'business_label', label: '类型' }, { k: 'currency', label: '币种' }, { k: 'amount', label: '外币金额', fmt: money }, { k: 'fx_rate', label: '汇率' }, { k: 'cny_amount', label: '本币金额', fmt: money }]) : `<p class="hint">${d.hint || '无记录'}</p>`),
    },
  ],

  // ================= 信用卡业务员 =================
  CREDIT_CARD_CLERK: [
    {
      code: 'UC-401', name: '信用卡申请办理', method: 'POST', path: '/api/creditcard/apply',
      fields: [{ n: 'customer_no', label: '客户号' }, { n: 'id_no', label: '证件号', hint: '客户号/证件号任填其一' }, { n: 'card_type', label: '卡片类型', type: 'select', options: ['普卡', '金卡', '白金卡'] }, { n: 'occupation', label: '职业' }, { n: 'monthly_income', label: '月收入', type: 'number' }],
      result: d => kv({ '卡号': d.credit_card.card_no, '状态': d.credit_card.status_label }),
    },
    {
      code: 'UC-402', name: '审核与额度设定', method: 'POST', path: '/api/creditcard/approve',
      fields: [
        { n: 'card_no', label: '信用卡号', required: true },
        { n: 'decision', label: '审批结论', type: 'select', options: [{ value: '', label: '请选择审批结论' }, { value: 'APPROVED', label: '通过' }, { value: 'REJECTED', label: '拒绝' }] },
        { n: 'credit_limit', label: '授信额度（可透支上限）', type: 'number', hint: '审批通过时必填' }, { n: 'bill_day', label: '账单日', type: 'number', hint: '每月几号出账单，填 1-28' }, { n: 'repay_day', label: '还款日', type: 'number', hint: '每月几号前还款，填 1-28' }, { n: 'reason', label: '拒绝原因', hint: '仅拒绝时填写' },
      ],
      result: d => {
        if (!d.credit_card) return '';
        const c = d.credit_card, base = { '卡号': c.card_no, '状态': c.status_label };
        if (c.status === 'REJECTED') base['拒绝原因'] = c.reject_reason || '-';
        else { base['授信额度'] = money(c.credit_limit); base['账单日'] = c.bill_day; base['还款日'] = c.repay_day; }
        return kv(base);
      },
    },
    {
      code: 'UC-403', name: '账单生成', method: 'POST', path: '/api/creditcard/bill',
      fields: [{ n: 'card_no', label: '信用卡号', required: true }, { n: 'bill_cycle', label: '账期', hint: '填年月，如 202607；留空取当月' }],
      result: d => kv({ '账期': billCycle(d.bill.bill_cycle), '应还总额': money(d.bill.total_amount), '最低还款': money(d.bill.min_repay), '还款截止': d.bill.due_date, '状态': d.bill.status_label }),
    },
    {
      code: 'UC-404', name: '还款处理', method: 'POST', path: '/api/creditcard/repay',
      fields: [
        { n: 'card_no', label: '信用卡号', required: true }, { n: 'account_no', label: '还款储蓄账号', required: true },
        { n: 'id_no', label: '证件号', required: true, hint: '核验持卡人身份' },
        { n: 'repay_type', label: '还款方式', type: 'select', options: [{ value: 'FULL', label: '全额还款' }, { value: 'MIN', label: '最低还款' }, { value: 'PARTIAL', label: '部分还款' }] },
        { n: 'amount', label: '还款金额(部分还款填)', type: 'number' },
      ],
      result: d => kv({ '账期': billCycle(d.bill.bill_cycle), '账单状态': d.bill.status_label, '已还': money(d.bill.paid_amount), '剩余': money(d.bill.remaining), '可用额度': money(d.credit_card.available_limit) }),
    },
    {
      code: 'UC-405', name: '预借现金处理', method: 'POST', path: '/api/creditcard/cash-advance',
      fields: [{ n: 'card_no', label: '信用卡号', required: true }, { n: 'id_no', label: '证件号', required: true }, { n: 'amount', label: '取现金额', type: 'number', required: true }, { n: 'payout_account', label: '转入储蓄账号', hint: '留空表示以现金支付' }],
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
      result: d => d.cards.map(c => kv({ '卡号': c.card_no, '客户': c.customer_name || '-', '状态': c.status_label, '授信额度': money(c.credit_limit), '可用额度': money(c.available_limit), '已用额度': money(c.used), '可用还款/出款账号': (c.repay_accounts && c.repay_accounts.length ? c.repay_accounts.join('、') : '（该客户暂无正常储蓄账户）') })
        + (c.bills.length ? '<h4>账单</h4>' + tbl(c.bills, [{ k: 'bill_cycle', label: '账期', fmt: billCycle }, { k: 'total_amount', label: '应还', fmt: money }, { k: 'paid_amount', label: '已还', fmt: money }, { k: 'status_label', label: '状态' }]) : '')).join('<hr>'),
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
      hint: '不能停用/降权当前登录的自己；系统须保留至少一名在用管理员。柜员忘记密码可在此重置。',
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
