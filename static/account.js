(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const emptyKey = () => ({ configured: false, status: 'not_configured', masked_key: null, verified_at: null });
  let session = { enabled: false, user: null, csrf_token: null, registration_open: false };
  let initialized = false;
  let generation = 0;
  let mode = 'login';
  let authBusy = false;
  let keyBusy = false;
  let loadingSettings = false;
  let settingsRevision = 0;
  let sessionReadRevision = 0;
  let keyMetadata = emptyKey();
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('benefit-radar-account') : null;

  function message(id, text, error = false) {
    $(id).textContent = text; $(id).hidden = !text;
    $(id).dataset.state = error ? 'error' : 'ok';
  }
  function sessionError() {
    const error = new Error('登录状态已更新，请在当前账户下重试。');
    error.code = 'session_changed'; return error;
  }
  function renderSession() {
    const required = session.enabled && !session.user;
    document.body.classList.toggle('auth-required', required);
    document.body.classList.toggle('account-enabled', session.enabled && Boolean(session.user));
    $('auth-view').hidden = !required; $('account-button').hidden = !session.enabled || !session.user;
    $('account-name').textContent = session.user?.username || '';
    $('account-avatar').textContent = session.user?.username?.slice(0, 1).toUpperCase() || '我';
    $('account-button').setAttribute('aria-label', session.user ? `${session.user.username} 的账户与 API 设置` : '打开账户与 API 设置');
    $('auth-mode-toggle').hidden = !session.registration_open;
    $('auth-registration-note').hidden = session.registration_open;
    const registering = mode === 'register';
    $('auth-title').textContent = registering ? '创建你的工作区' : '欢迎回来';
    $('auth-copy').textContent = registering ? '一份独立的清单，一把只属于你的元宝搜索密钥。' : '登录后，继续你的申请清单与政策跟进。';
    $('auth-password').autocomplete = registering ? 'new-password' : 'current-password';
    $('auth-submit').textContent = authBusy ? '正在处理…' : registering ? '创建账户并进入 ↗' : '登录工作区 ↗';
    $('auth-mode-toggle').textContent = registering ? '已有账号？返回登录' : '还没有账号？创建账户';
  }
  function applySession(next, { notify = true } = {}) {
    const changed = initialized && (session.user?.id || null) !== (next.user?.id || null);
    session = { enabled: Boolean(next.enabled), user: next.user || null, csrf_token: next.csrf_token || null, registration_open: Boolean(next.registration_open) };
    initialized = true;
    if (changed) {
      generation += 1; settingsRevision += 1; loadingSettings = false;
      authBusy = false; $('auth-submit').disabled = false; $('auth-mode-toggle').disabled = false;
      keyMetadata = emptyKey(); setKeyBusy(false); $('account-api-key').value = ''; $('auth-password').value = '';
      if (!session.user) mode = 'login';
      if ($('account-dialog').open) $('account-dialog').close();
    }
    renderSession();
    if (changed && notify) document.dispatchEvent(new CustomEvent('policy-account-change'));
  }
  function expire() {
    if (!session.enabled) return;
    applySession({ ...session, user: null, csrf_token: null });
  }
  function headers() { return session.user && session.csrf_token ? { 'X-CSRF-Token': session.csrf_token } : {}; }
  function assertActor(response, expectedUser) {
    if (expectedUser && response.headers.get('X-Account-Id') !== expectedUser) {
      expire(); throw sessionError();
    }
  }
  async function request(path, method = 'GET', payload) {
    const version = generation; const actor = session.user?.id;
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 40000);
    try {
      const response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: { ...headers(), ...(payload !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}) });
      if (version !== generation) throw sessionError();
      if (response.status === 401 && actor) { expire(); throw sessionError(); }
      if (!path.startsWith('/api/auth/')) assertActor(response, actor);
      const data = await response.json().catch(() => null);
      if (version !== generation) throw sessionError();
      if (!path.startsWith('/api/auth/')) assertActor(response, actor);
      if (!response.ok) throw new Error(data?.error?.message || '操作未完成，请稍后重试。');
      if (!data || typeof data !== 'object') throw new Error('服务返回的数据暂时不可用，请重试。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('连接用时较长，请稍后重试。保存的配置仍会保留。');
      if (error instanceof TypeError) throw new Error('暂时连接不到服务，请检查网络后重试。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function initialize() {
    const version = generation; const revision = ++sessionReadRevision;
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch('/api/auth/session', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      const legacyLocal = response.status === 404 && ['127.0.0.1', 'localhost'].includes(location.hostname);
      const data = legacyLocal ? { enabled: false } : await response.json().catch(() => null);
      if (version !== generation || revision !== sessionReadRevision) return !session.enabled || Boolean(session.user);
      if ((!response.ok && !legacyLocal) || !data || typeof data.enabled !== 'boolean') throw new Error('暂时无法读取账户状态，请重试。');
      applySession(data);
      if (session.user) message('auth-error', '');
      return !session.enabled || Boolean(session.user);
    } finally { clearTimeout(timer); }
  }
  function keyChanged() { document.dispatchEvent(new CustomEvent('policy-key-change', { detail: { configured: Boolean(keyMetadata.configured) } })); }
  function renderKey() {
    const status = !keyMetadata.configured ? 'not_configured' : keyMetadata.status === 'verified' && keyMetadata.verified_at ? 'verified' : keyMetadata.status === 'error' ? 'error' : 'saved_unverified';
    const copy = {
      not_configured: ['尚未配置', '添加你自己的 API Key 后，可启用元宝联网检索。其他账户的密钥不会用于你的请求。'],
      saved_unverified: ['已保存，等待验证', '保存成功不代表可以调用。完成一次实际验证后，这里会显示结果。'],
      verified: ['API 已生效 · 上次验证可用', '最近一次元宝搜索请求已成功，后续检索会使用当前账户的 Key。'],
      error: ['验证未通过', '请检查密钥、元宝搜索服务权限与账户额度，再重新验证。你的清单和进度不受影响。'],
    };
    $('account-key-status').dataset.state = status;
    $('account-key-title').textContent = copy[status][0]; $('account-key-description').textContent = copy[status][1];
    const checked = keyMetadata.verified_at && !Number.isNaN(Date.parse(keyMetadata.verified_at));
    $('account-key-time').hidden = !checked;
    $('account-key-time').textContent = checked ? '上次验证：' + new Date(keyMetadata.verified_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '';
    $('account-key-mask').hidden = !keyMetadata.configured;
    const tail = typeof keyMetadata.masked_key === 'string' ? keyMetadata.masked_key.slice(-4) : '';
    $('account-key-mask').textContent = keyMetadata.configured ? '已保存当前账户密钥' + (tail ? ' · ••••' + tail : '') : '';
    $('account-delete-key').hidden = !keyMetadata.configured;
    $('account-save-verify').textContent = keyBusy ? '正在验证…' : '保存并验证 ↗';
  }
  function setKeyBusy(value) {
    keyBusy = value;
    for (const id of ['account-api-key', 'account-save-verify', 'account-delete-key', 'account-logout']) $(id).disabled = value;
    renderKey();
  }
  async function openSettings() {
    if (!session.user) return;
    $('account-api-key').value = ''; message('account-key-message', '');
    $('account-owner-name').textContent = session.user.username;
    $('account-role').textContent = session.user.is_admin ? '管理员账户' : '个人账户';
    renderKey(); $('account-dialog').showModal();
    if (keyBusy) return;
    const revision = ++settingsRevision; const version = generation;
    loadingSettings = true; setKeyBusy(true); $('account-save-verify').textContent = '正在读取配置…';
    try {
      const result = await request('/api/account/api-key');
      if (revision === settingsRevision && version === generation) { keyMetadata = { ...emptyKey(), ...result.api_key }; renderKey(); }
    } catch (error) { if ($('account-dialog').open && revision === settingsRevision && version === generation) message('account-key-message', error.message, true); }
    finally { if (revision === settingsRevision && version === generation) { loadingSettings = false; setKeyBusy(false); } }
  }
  async function saveAndVerify(event) {
    event.preventDefault(); if (keyBusy) return;
    const key = $('account-api-key').value.trim();
    if (!key && !keyMetadata.configured) { message('account-key-message', '请先输入你自己的元宝搜索 API Key。', true); return; }
    const version = generation; let changed = false;
    setKeyBusy(true); $('account-api-key').value = ''; message('account-key-message', key ? '正在保存并验证当前账户的 Key…' : '正在重新验证已保存的 Key…');
    try {
      if (key) {
        const saved = await request('/api/account/api-key', 'POST', { api_key: key });
        if (version !== generation) return;
        keyMetadata = { ...emptyKey(), ...saved.api_key }; changed = true; renderKey();
      }
      const checked = await request('/api/account/api-key/verify', 'POST', {});
      if (version !== generation) return;
      keyMetadata = { ...emptyKey(), ...checked.api_key }; changed = true; renderKey();
      message('account-key-message', keyMetadata.status === 'verified' ? '验证成功，当前账户的元宝搜索配置已生效。' : '密钥已保存，本次验证未通过。请检查配置后重试。', keyMetadata.status !== 'verified');
    } catch (error) { if (version === generation) message('account-key-message', error.message, true); }
    finally { if (version === generation) { setKeyBusy(false); if (changed) keyChanged(); } }
  }
  async function deleteKey() {
    if (keyBusy) return;
    const version = generation; setKeyBusy(true); $('account-api-key').value = ''; message('account-key-message', '');
    try {
      const result = await request('/api/account/api-key', 'DELETE', {});
      if (version !== generation) return;
      keyMetadata = { ...emptyKey(), ...result.api_key }; renderKey();
      message('account-key-message', '已删除当前账户的 Key。联网检索已停用，不会改用其他账户的密钥。'); keyChanged();
    } catch (error) { if (version === generation) message('account-key-message', error.message, true); }
    finally { if (version === generation) setKeyBusy(false); }
  }
  async function authenticate(event) {
    event.preventDefault(); if (authBusy) return;
    const version = generation;
    authBusy = true; $('auth-submit').disabled = true; $('auth-mode-toggle').disabled = true; message('auth-error', ''); renderSession();
    try {
      const data = await request('/api/auth/' + mode, 'POST', { username: $('auth-username').value.trim(), password: $('auth-password').value });
      if (version !== generation) return;
      applySession({ ...session, enabled: true, user: data.user, csrf_token: data.csrf_token });
      channel?.postMessage('changed');
    } catch (error) { if (version === generation) message('auth-error', error.message, true); }
    finally { if (version === generation) { authBusy = false; $('auth-password').value = ''; $('auth-submit').disabled = false; $('auth-mode-toggle').disabled = false; renderSession(); } }
  }
  async function logout() {
    if (keyBusy) return;
    const version = generation;
    $('account-logout').disabled = true;
    try {
      await request('/api/auth/logout', 'POST', {});
      if (version !== generation) return;
      expire(); channel?.postMessage('changed');
      message('auth-error', ''); $('auth-username').focus({ preventScroll: true });
    } catch (error) { if ($('account-dialog').open && version === generation) message('account-key-message', error.message, true); }
    finally { if (version === generation) $('account-logout').disabled = false; }
  }
  $('auth-form').addEventListener('submit', authenticate);
  $('auth-mode-toggle').addEventListener('click', () => { mode = mode === 'login' ? 'register' : 'login'; $('auth-password').value = ''; message('auth-error', ''); renderSession(); });
  $('account-button').addEventListener('click', openSettings);
  $('account-close').addEventListener('click', () => $('account-dialog').close());
  $('account-dialog').addEventListener('close', () => {
    $('account-api-key').value = '';
    if (loadingSettings) { settingsRevision += 1; loadingSettings = false; setKeyBusy(false); }
    if (session.user) $('account-button').focus({ preventScroll: true });
  });
  $('account-key-form').addEventListener('submit', saveAndVerify);
  $('account-delete-key').addEventListener('click', deleteKey);
  $('account-logout').addEventListener('click', logout);
  if (channel) channel.onmessage = () => {
    if (!session.enabled) return;
    const hadUser = Boolean(session.user); expire();
    if (!hadUser) initialize().catch(() => message('auth-error', '账户状态更新暂未完成，请重新登录。', true));
  };
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && session.enabled) initialize().catch(() => {}); });
  window.PolicyAccount = Object.freeze({ initialize, headers, assertActor, expire, generation: () => generation, enabled: () => session.enabled, user: () => session.user });
})();
