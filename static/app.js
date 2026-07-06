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
    let data; try { data = await res.json(); } catch { data = { success: false, message: '响应解析失败' }; }
    return { status: res.status, data };
  }
};

// ---------- 结果渲染工具（供 operations.js 使用）----------
function esc(v) { return String(v == null ? '' : v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function money(v) { return v == null || v === '' ? '' : Number(v).toFixed(2); }
function kv(obj) {
  return '<table class="kv">' + Object.entries(obj).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('') + '</table>';
}
function tbl(rows, cols) {
  if (!rows || !rows.length) return '<p class="hint">无数据</p>';
  const head = '<tr>' + cols.map(c => `<th>${esc(c.label)}</th>`).join('') + '</tr>';
  const body = rows.map(r => '<tr>' + cols.map(c => {
    const val = c.fmt ? c.fmt(r[c.k]) : r[c.k];
    return `<td>${esc(val)}</td>`;
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
    `<li data-i="${i}"><span class="code">${op.code}</span>${esc(op.name)}</li>`).join('');
  [...$('menu').children].forEach(li => li.onclick = () => selectOp(ops[+li.dataset.i], li));
  if (ops.length) selectOp(ops[0], $('menu').firstElementChild);
}

// ---------- 渲染某个用例的表单 ----------
function selectOp(op, li) {
  [...$('menu').children].forEach(x => x.classList.remove('active'));
  if (li) li.classList.add('active');
  const fieldsHtml = op.fields.map(f => renderField(f)).join('');
  const uploadHtml = op.type === 'upload' ? `<div class="field"><label>备份文件</label><input type="file" id="f_file" accept=".json"></div>` : '';
  $('content').innerHTML = `
    <div class="panel">
      <h2><span class="code">${op.code}</span>${esc(op.name)}</h2>
      ${op.hint ? `<p class="hint">${esc(op.hint)}</p>` : ''}
      <form id="opform">${fieldsHtml}${uploadHtml}
        <button type="submit" class="btn">${op.type === 'download' ? '生成并下载' : '提交'}</button>
      </form>
      <div id="banner"></div>
      <div id="result"></div>
    </div>`;
  $('opform').onsubmit = e => { e.preventDefault(); submitOp(op); };
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

  if (op.type === 'download') return doDownload(op);
  if (op.type === 'upload') return doUpload(op, values);

  const req = op.method === 'GET' ? { query: values } : { body: values };
  const { status, data } = await API.call(op.method, op.path, req);
  if (status === 401) { alert('登录已过期，请重新登录'); return logout(); }
  handleResult(op, data);
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
    banner('err', `[${data.error || 'E'}] ${data.message || '操作失败'}`);
  }
}

function defaultResult(d) {
  const scalar = {};
  for (const [k, v] of Object.entries(d)) if (v == null || typeof v !== 'object') scalar[k] = v;
  return Object.keys(scalar).length ? kv(scalar) : '';
}

function banner(kind, msg) {
  const b = $('banner');
  if (!msg) { b.innerHTML = ''; return; }
  b.innerHTML = `<div class="alert ${kind === 'ok' ? 'ok' : 'err'}">${esc(msg)}</div>`;
}

// ---------- 备份下载 ----------
async function doDownload(op) {
  const res = await fetch(op.path, { headers: API.headers() });
  if (res.status === 401) { alert('登录已过期'); return logout(); }
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
  if (res.status === 401) { alert('登录已过期'); return logout(); }
  const data = await res.json().catch(() => ({ success: false, message: '响应解析失败' }));
  handleResult(op, data);
}

// ---------- 启动 ----------
$('loginForm').onsubmit = doLogin;
$('logout').onclick = logout;
if (API.token && currentUser) showApp();
