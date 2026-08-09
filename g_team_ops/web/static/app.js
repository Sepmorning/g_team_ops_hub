const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

async function api(url, options = {}) {
  const headers = { 'X-CSRF-Token': csrfToken(), ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { ...options, headers });
  let data;
  try { data = await response.json(); } catch { data = { ok: false, message: '服务器返回了无法解析的数据' }; }
  if (!response.ok || data.ok === false) {
    const error = new Error(data.message || data.detail || `请求失败（HTTP ${response.status}）`);
    error.data = data;
    throw error;
  }
  return data;
}

function showStatus(element, message, kind = 'info') {
  element.textContent = message;
  element.className = `status show ${kind}`;
}

function buttonBusy(button, busy, text = '处理中…') {
  if (busy) {
    if (!button.disabled) button.dataset.original = button.innerHTML;
    button.innerHTML = `<span class="spinner"></span>${text}`;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.original || button.innerHTML;
    button.disabled = false;
    delete button.dataset.original;
  }
}

window.GTeam = { api, showStatus, buttonBusy };
