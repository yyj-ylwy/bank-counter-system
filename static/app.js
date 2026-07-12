// 前端引擎：登录、按角色菜单、通用表单渲染、提交与结果展示。
// 依赖 operations.js 里的 OPERATIONS 声明表。

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
// 每个用例的菜单图标（纯展示，按用例编号取；未命中用默认点）
const ICONS = {
  'UC-101': '🆕', 'UC-102': '💰', 'UC-103': '💸', 'UC-104': '🔄', 'UC-105': '🔍', 'UC-106': '🪪', 'UC-107': '🗑️', 'UC-108': '✏️',
  'UC-201': '📋', 'UC-202': '✅', 'UC-203': '🏦', 'UC-204': '💵', 'UC-205': '⏰', 'UC-205b': '📞', 'UC-206': '📊',
  'UC-301': '🌐', 'UC-302': '💱', 'UC-303': '🔁', 'UC-304': '⚙️', 'UC-305': '🔍',
  'UC-401': '💳', 'UC-402': '✅', 'UC-403': '🧾', 'UC-404': '💵', 'UC-405': '🏧', 'UC-406': '🔒', 'UC-4Q': '🔍',
  'UC-501': '👥', 'UC-501b': '➕', 'UC-501c': '✏️', 'UC-502': '⚙️', 'UC-502b': '🛠️', 'UC-503': '📜', 'UC-504': '💾', 'UC-504b': '♻️',
};
const icon = code => ICONS[code] || '▪️';
// 每个用例的提交按钮文案：说清"按下会发生什么"（查询类统一"查询"，写入类"确认XX/保存XX"）。
const SUBMIT = {
  'UC-101': '确认开户', 'UC-102': '确认存款', 'UC-103': '确认取款', 'UC-104': '确认转账',
  'UC-106': '确认办理', 'UC-107': '确认销户', 'UC-108': '保存变更',
  'UC-201': '提交申请', 'UC-202': '提交审批结论', 'UC-203': '确认放款', 'UC-204': '确认还款', 'UC-205b': '保存催收记录',
  'UC-301': '确认开立', 'UC-303': '确认买卖', 'UC-304': '确认变更',
  'UC-401': '提交申请', 'UC-402': '提交审批结论', 'UC-403': '生成账单', 'UC-404': '确认还款', 'UC-405': '确认取现', 'UC-406': '确认办理',
  'UC-501b': '确认新建用户', 'UC-501c': '保存修改', 'UC-502b': '保存参数', 'UC-504b': '确认恢复数据',
};
function submitLabel(op) {
  if (op.type === 'download') return '下载备份';
  if (SUBMIT[op.code]) return SUBMIT[op.code];
  return op.method === 'GET' ? '查询' : '提交';  // GET 查询类统一"查询"，其余兜底"提交"
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
  return `<table class="grid">${head}${body}</table>`;
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
  const ops = OPERATIONS[currentUser.role] || [];
  $('menu').innerHTML = ops.map((op, i) =>
    `<li data-i="${i}"><span class="ic">${icon(op.code)}</span><span class="mtext"><span class="code">${op.code}</span>${esc(op.name)}</span></li>`).join('');
  [...$('menu').children].forEach(li => li.onclick = () => selectOp(ops[+li.dataset.i], li));
  if (ops.length) selectOp(ops[0], $('menu').firstElementChild);
}

// ---------- 渲染某个用例的表单 ----------
function selectOp(op, li) {
  [...$('menu').children].forEach(x => x.classList.remove('active'));
  if (li) li.classList.add('active');
  const fieldsHtml = op.fields.map(f => renderField(f)).join('');
  const uploadHtml = op.type === 'upload' ? `<div class="field full"><label>备份文件</label><input type="file" id="f_file" accept=".json"></div>` : '';
  $('content').innerHTML = `
    <div class="panel">
      <h2><span class="ic-lg">${icon(op.code)}</span><span class="code">${op.code}</span>${esc(op.name)}</h2>
      ${op.hint ? `<p class="hint">${esc(op.hint)}</p>` : ''}
      <form id="opform">${fieldsHtml}${uploadHtml}
        <button type="submit" class="btn">${esc(submitLabel(op))}</button>
      </form>
      <div id="banner"></div>
      <div id="result"></div>
    </div>`;
  $('opform').onsubmit = e => { e.preventDefault(); submitOp(op); };
  const first = $('opform').querySelector('input:not([type=checkbox]):not([type=file]), select');
  if (first) first.focus();
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
    const step = f.type === 'number' ? ' step="0.01"' : '';
    input = `<input type="${t}" id="${id}"${step} autocomplete="off">`;
  }
  const req = f.required ? '<span class="req">*</span>' : '';
  return `<div class="field ${f.type === 'checkbox' || f.type === 'checkboxVal' ? 'inline' : ''}">
      <label for="${id}">${esc(f.label)}${req}</label>${input}
      ${f.hint ? `<small>${esc(f.hint)}</small>` : ''}</div>`;
}

// ---------- 收集表单值 ----------
function gather(op) {
  const out = {};
  for (const f of op.fields) {
    const el = $('f_' + f.n);
    if (!el) continue;
    if (f.type === 'checkbox') { out[f.n] = el.checked; continue; }
    if (f.type === 'checkboxVal') { if (el.checked) out[f.n] = f.value; continue; }
    const v = el.value.trim();
    if (f.required && v === '') { throw new Error(`请填写「${f.label}」`); }
    if (v !== '') out[f.n] = v;
  }
  return out;
}

// ---------- 提交 ----------
async function submitOp(op) {
  banner('', '');
  $('result').innerHTML = '';
  let values;
  try { values = gather(op); } catch (err) { return banner('err', err.message); }

  const btn = $('opform').querySelector('button[type=submit]');
  const label = btn.textContent;
  btn.disabled = true; btn.classList.add('loading'); btn.textContent = '处理中…';
  try {
    if (op.type === 'download') return await doDownload(op);
    if (op.type === 'upload') return await doUpload(op, values);
    const req = op.method === 'GET' ? { query: values } : { body: values };
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
    banner('ok', data.message || '操作成功');
    if (op.result && data.data) {
      try { $('result').innerHTML = op.result(data.data); }
      catch (e) { $('result').innerHTML = defaultResult(data.data); }
    } else if (data.data) {
      $('result').innerHTML = defaultResult(data.data);
    }
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
