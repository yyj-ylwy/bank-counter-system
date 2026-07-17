// 前端引擎：登录、按角色菜单、通用表单渲染、提交与结果展示。
// 依赖 operations.js 里的 OPERATIONS 声明表。
// 【答辩讲解】前端用声明式：operations.js 是一张配置表描述每个功能长什么样；app.js 是引擎，读配置自动生成菜单/表单/提交/结果。
// 数据和逻辑分离，加功能只加一条配置。还做了资金操作二次确认弹窗、金额大写、防注入转义、查询回填。

// ---------- API 客户端 ----------
const API = {
  token: localStorage.getItem('token') || null,
  headers() { return this.token ? { 'Authorization': 'Bearer ' + this.token } : {}; },
  async call(method, path, { body, query } = {}) {
    let url = path;
    if (query) {
      const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== '' && v != null));
      const s = qs.toString(); if (s) url += '?' + s;
    }
    const opts = { method, headers: { ...this.headers() } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    let data; try { data = await res.json(); } catch { data = { success: false, message: '服务器返回异常，请稍后重试' }; }
    return { status: res.status, data };
  }
};

// ---------- 结果渲染工具（供 operations.js 使用）----------
function esc(v) { return String(v == null ? '' : v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function money(v) {
  if (v == null || v === '') return '';
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
// 费用明细 {中文:金额} → "佣金 5，过户费 0.03"（交割单展示）
function feeStr(detail) {
  if (!detail) return '';
  return Object.keys(detail).map(function (k) { return k + ' ' + detail[k]; }).join('，');
}
// 人民币金额大写（银行回单标配），如 1234.56 → 壹仟贰佰叁拾肆元伍角陆分
function rmbUpper(num) {
  num = Number(num);
  if (!isFinite(num)) return '';
  const neg = num < 0; num = Math.abs(num);
  const fen = Math.round(Number(money(num).replace(/,/g, '')) * 100);  // 复用 money() 的取整，保证大写与显示金额恒等
  if (fen === 0) return '零元整';
  const DIG = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'];
  const SMALL = ['', '拾', '佰', '仟'], BIG = ['', '万', '亿', '兆'];
  const yuan = Math.floor(fen / 100), jiao = Math.floor((fen % 100) / 10), fenn = fen % 10;
  let intStr = '';
  if (yuan !== 0) {
    let ys = String(yuan); const groups = [];
    while (ys.length) { groups.unshift(ys.slice(-4)); ys = ys.slice(0, -4); }
    for (let g = 0; g < groups.length; g++) {
      const gStr = groups[g], bigIdx = groups.length - 1 - g;
      let gPart = '', started = false, pendingZero = false;
      for (let i = 0; i < gStr.length; i++) {
        const d = +gStr[i], smallIdx = gStr.length - 1 - i;
        if (d === 0) { if (started) pendingZero = true; }
        else { if (pendingZero) { gPart += '零'; pendingZero = false; } gPart += DIG[d] + SMALL[smallIdx]; started = true; }
      }
      if (gPart !== '') { if (intStr !== '' && +gStr < 1000) intStr += '零'; intStr += gPart + BIG[bigIdx]; }
    }
    intStr += '元';
  }
  let fracStr = '';
  if (jiao === 0 && fenn === 0) fracStr = '整';
  else {
    if (yuan !== 0 && jiao === 0 && fenn !== 0) fracStr += '零';
    if (jiao !== 0) fracStr += DIG[jiao] + '角';
    if (fenn !== 0) fracStr += DIG[fenn] + '分';
  }
  return (neg ? '负' : '') + intStr + fracStr;
}
// 每个用例的菜单图标（纯展示，按用例编号取；未命中用默认点）
const ICONS = {
  'UC-000': '🔑', 'UC-00A': '📄',
  'UC-101': '🆕', 'UC-102': '💰', 'UC-103': '💸', 'UC-104': '🔄', 'UC-105': '🔍', 'UC-106': '🪪', 'UC-107': '🗑️', 'UC-108': '✏️',
  'UC-201': '📋', 'UC-202': '✅', 'UC-203': '🏦', 'UC-204': '💵', 'UC-205': '⏰', 'UC-205b': '📞', 'UC-206': '📊',
  'UC-301': '🌐', 'UC-302': '💱', 'UC-303': '🔁', 'UC-304': '⚙️', 'UC-305': '🔍', 'UC-306': '📈', 'UC-307': '📌',
  'UC-401': '💳', 'UC-402': '✅', 'UC-403': '📈', 'UC-404': '🛒', 'UC-405': '💵',
  'UC-406': '🧾', 'UC-407': '🎁', 'UC-408': '🎫', 'UC-409': '🔒', 'UC-4Q': '🔍',
  'UC-501': '👥', 'UC-501b': '➕', 'UC-501c': '✏️', 'UC-502': '⚙️', 'UC-502b': '🛠️', 'UC-503': '📜', 'UC-504': '💾', 'UC-504b': '♻️',
  'UC-601': '🧾', 'UC-602': '📈', 'UC-603': '🔃', 'UC-604': '📝', 'UC-605': '🛒', 'UC-606': '💸', 'UC-607': '📊', 'UC-608': '🧺', 'UC-609': '✅', 'UC-610': '📋',
};
const icon = code => ICONS[code] || '▪️';
// 每个用例的提交按钮文案：说清"按下会发生什么"（查询类统一"查询"，写入类"确认XX/保存XX"）。
const SUBMIT = {
  'UC-000': '确认修改',
  'UC-101': '确认开户', 'UC-102': '确认存款', 'UC-103': '确认取款', 'UC-104': '确认转账',
  'UC-106': '确认办理', 'UC-107': '确认销户', 'UC-108': '保存变更',
  'UC-201': '提交申请', 'UC-202': '提交审批结论', 'UC-203': '确认放款', 'UC-204': '确认还款', 'UC-205b': '保存催收记录',
  'UC-301': '确认开立', 'UC-303': '确认买卖', 'UC-304': '确认变更', 'UC-307': '确认挂牌',
  'UC-401': '提交申请', 'UC-402': '提交审批结论', 'UC-403': '提交申请',
  'UC-404': '确认消费', 'UC-405': '确认还款', 'UC-408': '确认兑换', 'UC-409': '确认办理',
  // UC-406 本月消费记录 / UC-407 积分商城 均为 GET，走兜底"查询"
  'UC-501b': '确认新建用户', 'UC-501c': '保存修改', 'UC-502b': '保存参数', 'UC-504b': '确认恢复数据',
  'UC-603': '刷新当日行情', 'UC-604': '提交测评', 'UC-605': '确认申购', 'UC-606': '确认赎回', 'UC-608': '保存产品', 'UC-609': '确认到账处理',
};
function submitLabel(op) {
  if (op.type === 'download') return '下载备份';
  if (SUBMIT[op.code]) return SUBMIT[op.code];
  return op.method === 'GET' ? '查询' : '提交';  // GET 查询类统一"查询"，其余兜底"提交"
}
// 涉及资金变动/不可逆的操作，提交前弹出复核确认（银行二次确认规范）
const CONFIRM_OPS = new Set([
  'UC-102', 'UC-103', 'UC-104', 'UC-106', 'UC-107',  // 存/取/转账/挂失补卡/销户
  'UC-203', 'UC-204',                            // 放款/贷款还款
  'UC-303', 'UC-304', 'UC-307',                  // 外汇买卖/账户变更/实时挂牌
  'UC-404', 'UC-405',                            // 信用卡消费(扣额度)/信用卡还款(扣款)
  'UC-605', 'UC-606',                            // 理财申购/赎回
  'UC-501c', 'UC-502b', 'UC-504b',              // 改用户/改参数/恢复数据
]);
// 把已填字段整理成"标签: 值"复核表，下拉显示中文标签、金额显示千分位
function buildSummary(op, values) {
  const rows = [];
  for (const f of op.fields) {
    if (!(f.n in values)) continue;
    let v = values[f.n];
    if (f.type === 'checkboxVal' || f.type === 'checkbox') v = '是';
    else if (f.type === 'select') {
      const opt = f.options.find(o => (typeof o === 'object' ? o.value : o) == v);
      v = opt ? (typeof opt === 'object' ? opt.label : opt) : v;
    } else if (f.type === 'password') v = '••••••';
    else if (f.type === 'number' && op.code !== 'UC-303' && /amount|balance|limit|income/i.test(f.n)) v = money(v);
    rows.push([f.label, v]);
  }
  // 人民币金额操作附大写（外汇金额为外币，不加）
  if (values.amount != null && values.amount !== '' && op.code !== 'UC-303')
    rows.push(['金额大写', rmbUpper(values.amount)]);
  return rows;
}
// 复核弹窗，返回 Promise<boolean>
function confirmDialog(op, values) {
  return new Promise(resolve => {
    const rows = buildSummary(op, values).map(([k, v]) =>
      `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('');
    const el = document.createElement('div');
    el.className = 'modal-overlay';
    el.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <h3>请核对信息后确认</h3>
        <p class="modal-op">${esc(submitLabel(op))} · <span class="code">${esc(op.code)}</span> ${esc(op.name)}</p>
        <table class="kv">${rows || '<tr><td>（无可展示的填写项）</td></tr>'}</table>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-act="cancel">取消</button>
          <button type="button" class="btn" data-act="ok">确认无误，提交</button>
        </div>
      </div>`;
    const close = ans => { el.remove(); document.removeEventListener('keydown', onKey); resolve(ans); };
    const onKey = e => { if (e.key === 'Escape') close(false); };
    el.addEventListener('click', e => {
      if (e.target === el) close(false);
      const act = e.target.dataset && e.target.dataset.act;
      if (act === 'ok') close(true);
      if (act === 'cancel') close(false);
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(el);
    const okBtn = el.querySelector('[data-act=ok]');
    if (okBtn) okBtn.focus();
  });
}
function kv(obj) {
  return '<table class="kv">' + Object.entries(obj).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('') + '</table>';
}
function tbl(rows, cols) {
  if (!rows || !rows.length) return '<p class="hint">暂无记录</p>';
  const head = '<tr>' + cols.map(c => `<th${c.fmt === money ? ' class="num"' : ''}>${esc(c.label)}</th>`).join('') + '</tr>';
  const body = rows.map(r => '<tr>' + cols.map(c => {
    const val = c.fmt ? c.fmt(r[c.k], r) : r[c.k];
    const cls = c.fmt === money ? ' class="num"' : '';
    return `<td${cls}>${esc(val)}</td>`;
  }).join('') + '</tr>').join('');
  return `<div class="grid-wrap"><table class="grid">${head}${body}</table></div>`;  // 包一层：宽表在面板内横向滚动，不撑破布局
}

// ---------- DOM 引用 ----------
const $ = id => document.getElementById(id);
let currentUser = JSON.parse(localStorage.getItem('user') || 'null');

// ---------- 登录 ----------
async function doLogin(e) {
  e.preventDefault();
  const employee_no = $('emp').value.trim();
  const password = $('pw').value;
  $('loginErr').textContent = '';
  const { status, data } = await API.call('POST', '/api/login', { body: { employee_no, password } });
  if (data.success) {
    API.token = data.data.token;
    currentUser = data.data.user;
    localStorage.setItem('token', API.token);
    localStorage.setItem('user', JSON.stringify(currentUser));
    showApp();
  } else {
    $('loginErr').textContent = data.message || '登录失败';
  }
}

function logout() {
  API.token = null; currentUser = null;
  localStorage.removeItem('token'); localStorage.removeItem('user');
  $('app').classList.add('hidden'); $('login').classList.remove('hidden');
}

// ---------- 主界面 ----------
function showApp() {
  $('login').classList.add('hidden');
  $('app').classList.remove('hidden');
  $('who').textContent = `${currentUser.name}（${currentUser.role_label}）`;
  const ops = (OPERATIONS[currentUser.role] || []).concat(typeof COMMON !== 'undefined' ? COMMON : []);
  $('menu').innerHTML = ops.map((op, i) =>
    `<li data-i="${i}"><span class="ic">${icon(op.code)}</span><span class="mtext"><span class="code">${op.code}</span>${esc(op.name)}</span></li>`).join('');
  [...$('menu').children].forEach(li => li.onclick = () => selectOp(ops[+li.dataset.i], li));
  if (ops.length) selectOp(ops[0], $('menu').firstElementChild);
}

// ---------- 渲染某个用例的表单 ----------
function selectOp(op, li) {
  [...$('menu').children].forEach(x => x.classList.remove('active'));
  if (li) li.classList.add('active');
  const fieldsHtml = op.fields.map(f => {
    let h = renderField(f);
    if (op.lookup && f.n === op.lookup.byField)  // 查询回填按钮紧跟在定位字段(如工号/卡种)之后
      h += `<div class="field full lookup-row"><button type="button" class="btn" id="lookupBtn">${esc(op.lookup.btnLabel || '🔍 查询并回填')}</button><div id="lookupInfo" class="lookup-info"></div></div>`;
    return h;
  }).join('');
  const uploadHtml = op.type === 'upload' ? `<div class="field full"><label>备份文件</label><input type="file" id="f_file" accept=".json"></div>` : '';
  $('content').innerHTML = `
    <div class="panel">
      <h2><span class="ic-lg">${icon(op.code)}</span><span class="code">${op.code}</span>${esc(op.name)}</h2>
      ${op.hint ? `<p class="hint" id="opHint">${esc(op.hint)}</p>` : ''}
      <form id="opform">${fieldsHtml}${uploadHtml}
        <div class="form-actions">
          <button type="submit" class="btn">${esc(submitLabel(op))}</button>
          <button type="reset" class="btn btn-ghost">重填</button>
        </div>
      </form>
      ${op.footer ? `<div class="op-footer">${op.footer()}</div>` : ''}
      ${op.footerLoad ? `<div class="op-footer" id="opFooter">加载中…</div>` : ''}
      <div id="banner"></div>
      <div id="result"></div>
    </div>`;
  $('opform').onsubmit = e => { e.preventDefault(); submitOp(op); };
  if (op.lookup) { const lb = $('lookupBtn'); if (lb) lb.onclick = () => runLookup(op); }
  if (op.footerLoad) loadFooter(op);  // 异步拉取 footer 数据（如卡种权益动态取有效额度）
  if (op.liveConfig) loadLiveConfig(op);  // 异步拉取当前系统参数，回填提示文字/下拉（管理员改后随之更新）
  const first = $('opform').querySelector('input:not([type=checkbox]):not([type=file]), select');
  if (first) first.focus();
}

// 异步加载 footer：从后端拉数据后渲染进 #opFooter（如 UC-401 卡种权益表按当前有效额度动态展示）
async function loadFooter(op) {
  const { status, data } = await API.call('GET', op.footerLoad.path);
  const el = $('opFooter');
  if (!el) return;  // 用户已切换到别的用例
  if (status === 401) return sessionExpired();
  if (!data.success || !data.data) { el.innerHTML = '<p class="hint">权益信息加载失败</p>'; return; }
  el.innerHTML = op.footerLoad.render(data.data);
}

// 拉取当前生效的系统参数，交给 op.liveConfig.apply 回填提示文字/下拉，使前端展示与管理员设置同步。
// apply(cfg) 内自行按 id 定位并 esc；表单先以静态文案渲染，拉到后再覆盖，故失败时保留静态兜底。
async function loadLiveConfig(op) {
  const { status, data } = await API.call('GET', op.liveConfig.path);
  if (status === 401) return sessionExpired();
  if (!$('opform') || !data.success || !data.data) return;  // 已切走或加载失败：保留静态提示
  try { op.liveConfig.apply(data.data); } catch (e) { /* 回填失败不影响表单本身可用 */ }
}

// 查询回填：凭定位字段(如工号/卡种)先查出当前记录，把其字段值填进表单、并把关键信息显示出来。
// lk.query(vals) 可选：需要按当前表单值带参查询时用（如还款先按 身份+卡种 试算应还金额）。
async function runLookup(op) {
  const lk = op.lookup;
  const keyField = op.fields.find(f => f.n === lk.byField) || {};
  const key = ($('f_' + lk.byField).value || '').trim();
  if (!key) return banner('err', `请先填写「${keyField.label || lk.byField}」`);
  banner('', '');
  let query = {};
  if (lk.query) {  // 带参查询：先收集并校验表单里的定位字段（如身份标识）
    // 宽松收集：不强制表单里与查询无关的 required 字段；查询自身所需字段由 lk.query 里各自校验
    try { query = lk.query(gather(op, { skipRequired: true })); } catch (err) { return banner('err', err.message); }
  }
  const { status, data } = await API.call('GET', lk.path, { query });
  if (status === 401) return sessionExpired();
  if (!data.success) return banner('err', data.message || '查询失败');
  const rec = lk.find(data.data, key);
  if (!rec) return banner('err', `未找到「${key}」，请核对`);
  for (const [name, val] of Object.entries(lk.fill ? lk.fill(rec) : {})) {
    const el = $('f_' + name);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = (val === true || ['1', 'true', 'yes', 'on', '是'].includes(String(val).toLowerCase()));  // 复选框按真假回填
    else el.value = (val == null ? '' : String(val));
  }
  // lk.options(rec) 可选：按查询结果重建下拉的选项文案（如把各方式应还金额显示进「还款方式」下拉）
  for (const [name, opts] of Object.entries(lk.options ? lk.options(rec) : {})) {
    const el = $('f_' + name);
    if (!el) continue;
    const cur = el.value;
    el.innerHTML = opts.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('');
    if (opts.some(o => String(o.value) === cur)) el.value = cur;  // 保留原选择
  }
  // lk.render(rec) 可选：返回受控 HTML 按行展示结果（数据须在回调里 esc）；否则用 lk.show 输出纯文本
  const info = $('lookupInfo');
  if (info) {
    if (lk.render) info.innerHTML = lk.render(rec);
    else info.innerHTML = lk.show ? `<p class="hint">${esc(lk.show(rec))}</p>` : '';  // 纯文本仍走 esc
  }
  banner('ok', lk.okMsg || '已带出当前信息，修改需要变更的项后点保存');
}

function renderField(f) {
  const id = 'f_' + f.n;
  let input;
  if (f.type === 'select') {
    const opts = f.options.map(o => {
      const val = typeof o === 'object' ? o.value : o;
      const lab = typeof o === 'object' ? o.label : o;
      return `<option value="${esc(val)}">${esc(lab)}</option>`;
    }).join('');
    input = `<select id="${id}">${opts}</select>`;
  } else if (f.type === 'checkbox' || f.type === 'checkboxVal') {
    input = `<input type="checkbox" id="${id}" class="cb">`;
  } else {
    const t = f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : f.type === 'password' ? 'password' : 'text';
    // step 默认 any：金额精度由后端统一收敛（DECIMAL(18,2)）。写死 step="0.01" 时浏览器原生
    // 校验会静默拦下 0.0435 这类多位小数（表单提交无任何反应），导致贷款审批的年利率填不进去
    const step = f.type === 'number' ? ` step="${f.step || 'any'}"` : '';
    const maxlen = (t === 'text' || t === 'password') ? ` maxlength="${f.max || 64}"` : '';
    input = `<input type="${t}" id="${id}"${step}${maxlen} autocomplete="off">`;
  }
  const req = f.required ? '<span class="req">*</span>' : '';
  return `<div class="field ${f.type === 'checkbox' || f.type === 'checkboxVal' ? 'inline' : ''}">
      <label for="${id}">${esc(f.label)}${req}</label>${input}
      ${f.hint ? `<small id="hint_${f.n}">${esc(f.hint)}</small>` : ''}</div>`;
}

// ---------- 收集表单值 ----------
// skipRequired：查询回填(runLookup)时用——此时只需查询所需的少数字段(如身份+卡种)，
// 不能因表单里其它 required 字段(如提额的「新授信额度」)未填就拦下查询（那本该先查后填）。
function gather(op, { skipRequired = false } = {}) {
  const out = {};
  for (const f of op.fields) {
    const el = $('f_' + f.n);
    if (!el) continue;
    if (f.type === 'checkbox') { if (f.required && !el.checked && !skipRequired) throw new Error(`请勾选「${f.label}」`); out[f.n] = el.checked; continue; }
    if (f.type === 'checkboxVal') { if (f.required && !el.checked && !skipRequired) throw new Error(`请勾选「${f.label}」`); if (el.checked) out[f.n] = f.value; continue; }
    const v = el.value.trim();
    if (f.required && v === '' && !skipRequired) { throw new Error(`请填写「${f.label}」`); }
    if (v !== '') {
      if (f.pattern && !new RegExp('^(?:' + f.pattern + ')$').test(v))
        throw new Error(f.patternMsg || `「${f.label}」格式不正确`);
      out[f.n] = v;
    }
  }
  return out;
}

// ---------- 提交 ----------
async function submitOp(op) {
  banner('', '');
  $('result').innerHTML = '';
  let values;
  try { values = gather(op); } catch (err) { return banner('err', err.message); }
  if (op.validate) { const msg = op.validate(values); if (msg) return banner('err', msg); }  // 跨字段校验（如两次密码一致）

  // 资金/不可逆操作：提交前复核确认
  if (CONFIRM_OPS.has(op.code)) {
    const okToGo = await confirmDialog(op, values);
    if (!okToGo) return banner('', '');
  }

  const btn = $('opform').querySelector('button[type=submit]');
  const label = btn.textContent;
  btn.disabled = true; btn.classList.add('loading'); btn.textContent = '处理中…';
  try {
    if (op.type === 'download') return await doDownload(op);
    if (op.type === 'upload') return await doUpload(op, values);
    const sendVals = op.transform ? op.transform(values) : values;  // 如利率百分数→小数（confirm 仍显示用户输入）
    const req = op.method === 'GET' ? { query: sendVals } : { body: sendVals };
    const { status, data } = await API.call(op.method, op.path, req);
    if (status === 401) return sessionExpired();
    handleResult(op, data);
  } catch (err) {
    banner('err', '网络异常，请检查连接后重试', err.message);
  } finally {
    btn.disabled = false; btn.classList.remove('loading'); btn.textContent = label;
  }
}

function handleResult(op, data) {
  if (data.success) {
    if (data.data && data.data.token) {  // 改密后服务端签发新令牌，刷新本地令牌防当前会话失效
      API.token = data.data.token; localStorage.setItem('token', API.token);
    }
    banner('ok', data.message || '操作成功');
    if (op.result && data.data) {
      try { $('result').innerHTML = op.result(data.data); }
      catch (e) { $('result').innerHTML = defaultResult(data.data); }
    } else if (data.data) {
      $('result').innerHTML = defaultResult(data.data);
    }
    if (op.footerLoad) loadFooter(op);  // 提交后刷新待办列表（审批/放款
  } else {
    // 只给用户看中文说明；内部错误码(E-1等)降级为悬浮提示，供技术排查
    banner('err', data.message || '操作失败', data.error);
  }
}

function defaultResult(d) {
  const scalar = {};
  for (const [k, v] of Object.entries(d)) if (v == null || typeof v !== 'object') scalar[k] = v;
  return Object.keys(scalar).length ? kv(scalar) : '';
}

function banner(kind, msg, code) {
  const b = $('banner');
  if (!msg) { b.innerHTML = ''; return; }
  const title = code ? ` title="${esc(code)}"` : '';  // 错误码悬浮显示，不占正文
  b.innerHTML = `<div class="alert ${kind === 'ok' ? 'ok' : 'err'}"${title}>${esc(msg)}</div>`;
  // 表单/待办列表较长时提交按钮在视口下方，结果渲染在顶部会看不见——自动滚回提示处
  b.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// 会话过期统一处理：退回登录页并在登录框提示
function sessionExpired() {
  logout();
  $('loginErr').textContent = '登录已过期，请重新登录';
}

// ---------- 备份下载 ----------
async function doDownload(op) {
  const res = await fetch(op.path, { headers: API.headers() });
  if (res.status === 401) return sessionExpired();
  if (!res.ok) return banner('err', '备份失败');
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename=([^;]+)/);
  const name = m ? m[1].trim() : 'bank-backup.json';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
  banner('ok', '备份已生成并开始下载：' + name);
}

// ---------- 数据恢复上传 ----------
async function doUpload(op, values) {
  const fileEl = $('f_file');
  if (!fileEl || !fileEl.files.length) return banner('err', '请先选择备份文件');
  const fd = new FormData();
  fd.append('file', fileEl.files[0]);
  for (const [k, v] of Object.entries(values)) fd.append(k, v);
  const res = await fetch(op.path, { method: 'POST', headers: API.headers(), body: fd });
  if (res.status === 401) return sessionExpired();
  const data = await res.json().catch(() => ({ success: false, message: '服务器返回异常，请稍后重试' }));
  handleResult(op, data);
}

// ---------- 启动 ----------
$('loginForm').onsubmit = doLogin;
$('logout').onclick = logout;
if (API.token && currentUser) showApp();
