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

(() => {
  'use strict';

  const root = document.documentElement;
  const themeKey = 'g_team_ops_theme';
  const motionKey = 'g_team_ops_motion';
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const safeGet = (key) => {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  };
  const safeSet = (key, value) => {
    try { localStorage.setItem(key, value); } catch (_) { /* 浏览器禁用存储时仅本次生效 */ }
  };

  function syncTheme(theme) {
    const next = theme === 'dark' ? 'dark' : 'light';
    root.dataset.theme = next;
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      const dark = next === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? '切换到浅色主题' : '切换到深色主题');
      button.title = dark ? '切换到浅色主题' : '切换到深色主题';
      const label = button.querySelector('[data-theme-label]');
      const use = button.querySelector('use');
      if (label) label.textContent = dark ? '浅色' : '深色';
      if (use) use.setAttribute('href', dark ? '#icon-sun' : '#icon-moon');
    });
  }

  function motionEnabled() {
    return safeGet(motionKey) !== 'off' && !reducedMotion.matches;
  }

  function syncMotion(announce = false) {
    const enabled = motionEnabled();
    root.dataset.motion = enabled ? 'on' : 'off';
    document.querySelectorAll('[data-motion-toggle]').forEach((button) => {
      button.setAttribute('aria-pressed', String(enabled));
      button.setAttribute('aria-label', enabled ? '关闭界面动态效果' : '开启界面动态效果');
      button.title = reducedMotion.matches ? '系统已启用减少动态效果' : (enabled ? '关闭界面动态效果' : '开启界面动态效果');
      const label = button.querySelector('[data-motion-label]');
      if (label) label.textContent = enabled ? '动态' : '静态';
    });
    if (announce) window.dispatchEvent(new CustomEvent('gteam:motion-change', { detail: { enabled } }));
  }

  syncTheme(root.dataset.theme || safeGet(themeKey) || 'light');
  syncMotion();

  document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      safeSet(themeKey, next);
      syncTheme(next);
    });
  });

  document.querySelectorAll('[data-motion-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      safeSet(motionKey, motionEnabled() ? 'off' : 'on');
      syncMotion(true);
    });
  });

  const handleReducedMotion = () => syncMotion(true);
  if (typeof reducedMotion.addEventListener === 'function') reducedMotion.addEventListener('change', handleReducedMotion);
  else if (typeof reducedMotion.addListener === 'function') reducedMotion.addListener(handleReducedMotion);

  const menuButton = document.querySelector('[data-sidebar-open]');
  const closeSidebar = () => {
    document.body.classList.remove('sidebar-open');
    if (menuButton) menuButton.setAttribute('aria-expanded', 'false');
  };
  if (menuButton) {
    menuButton.addEventListener('click', () => {
      const open = !document.body.classList.contains('sidebar-open');
      document.body.classList.toggle('sidebar-open', open);
      menuButton.setAttribute('aria-expanded', String(open));
    });
  }
  document.querySelector('[data-sidebar-close]')?.addEventListener('click', closeSidebar);
  document.querySelectorAll('.nav a').forEach((link) => link.addEventListener('click', closeSidebar));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSidebar();
  });

  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
  document.querySelectorAll('[data-greeting]').forEach((element) => { element.textContent = greeting; });
  document.querySelectorAll('[data-today]').forEach((element) => {
    element.textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now);
  });
})();

window.GTeam = { api, showStatus, buttonBusy };
