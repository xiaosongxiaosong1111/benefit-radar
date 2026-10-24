/* global PolicyUI, PolicyAccount */
(() => {
  'use strict';
  const ui = PolicyUI;
  const account = PolicyAccount;
  const $ = id => document.getElementById(id);
  const profileKey = 'tianjin-rights-profile-v1';
  let state = { policies: [], follows: [], notifications: [], service: {}, profile: null, now: new Date().toISOString(), activeId: null, view: 'discover', step: 0, draft: {}, busy: false };
  let toastTimer;
  let syncing = false;
  let savingFollow = false;
  let savingConditions = false;
  let followRevision = 0;
  let knownNotifications = new Set();
  let conditionDrafts = {};
  let editingConditions = null;
  let bootstrapRevision = 0;
  const stageLabels = { student: '在校学生', graduate: '毕业 / 正在求职', employed: '已经就业', flexible: '灵活就业', other: '其他情况', unknown: '暂不确定' };
  const statusLabels = ui.stages;
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }
  function button(label, className, action) {
    const item = node('button', className, label);
    item.type = 'button';
    item.addEventListener('click', action);
    return item;
  }
  function link(label, value, className = 'source-link') {
    const url = ui.safeUrl(value);
    if (!url) return node('span', 'quiet', '官方入口请查看政策原文');
    const item = node('a', className, label);
    item.href = url; item.target = '_blank'; item.rel = 'noopener noreferrer';
    return item;
  }
  function notify(message) {
    clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3400);
  }
  function showError(message) {
    $('error-copy').textContent = message; $('global-error').hidden = !message;
  }
  async function api(path, method = 'GET', payload) {
    const generation = account.generation(); const actor = account.user()?.id;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path.includes('updates') ? 65000 : 40000);
    try {
      const response = await fetch(path, { method, cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
        headers: { ...account.headers(), ...(payload !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}) });
      if (generation !== account.generation()) throw new Error('登录状态已更新，请在当前账户下重试。');
      if (response.status === 401 && account.enabled()) { account.expire(); throw new Error('登录已过期，请重新登录。'); }
      account.assertActor(response, actor);
      const data = await response.json().catch(() => null);
      if (generation !== account.generation()) throw new Error('登录状态已更新，请在当前账户下重试。');
      account.assertActor(response, actor);
      if (!response.ok) throw new Error(data?.error?.message || '操作暂时未完成，请稍后重试。');
      if (!data || typeof data !== 'object') throw new Error('服务返回了无法识别的数据，请重试。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('查询用时较长，请稍后重试；已有清单仍保留。');
      if (error instanceof TypeError) throw new Error('暂时连接不到本地服务，请确认服务已启动。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function readProfile() {
    if (account.enabled()) return state.profile;
    try { const value = JSON.parse(localStorage.getItem(profileKey)); return value && typeof value === 'object' && !Array.isArray(value) ? value : null; }
    catch { return null; }
  }
  function storeProfile(profile) {
    if (account.enabled()) return;
    try { localStorage.setItem(profileKey, JSON.stringify(profile)); }
    catch { notify('浏览器未允许保存情况，本次查询仍可使用。'); }
  }
  function followFor(id) { return state.follows.find(item => item.policy_id === id); }
  function policyFor(id) { return state.policies.find(item => item.id === id); }
  function dateCopy(value) { return value ? String(value).slice(0, 10).replaceAll('-', '.') : '暂无记录'; }
  function timeCopy(value) {
    if (!value || Number.isNaN(Date.parse(value))) return '来源未返回检索时间';
    return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
  }
  function badge(policy) {
    const tag = node('span', 'status-badge', ui.matchLabel(policy));
    tag.dataset.state = policy.match?.status || 'preview'; return tag;
  }
  function deadlineCopy(policy) {
    const days = ui.daysUntil(policy.deadline, state.now);
    if (days === null) return policy.recurring_deadline_day ? `每月 ${policy.recurring_deadline_day} 日前按规定申报` : '办理时间见官方说明';
    return days < 0 ? `本期申请已截止 · ${dateCopy(policy.deadline)}` : days === 0 ? '今天截止，请以经办方受理时间为准' : `申请截止 ${dateCopy(policy.deadline)}${days <= 7 ? ` · 还有 ${days} 天` : ''}`;
  }
  function updateShell() {
    const count = state.follows.length;
    $('discover-view').classList.toggle('has-profile', Boolean(state.profile));
    $('follow-count').textContent = count; $('follow-count').hidden = !count;
    $('connection-status').textContent = state.service.search_configured ? '官方资料 · 元宝联网检索已配置' : '官方资料预览 · 联网检索未启用';
    $('hero-title').replaceChildren(...(state.profile ? [document.createTextNode('每一份支持，'), node('br'), node('span', 'accent-word', '都值得看见。')] : [document.createTextNode('与你有关的，'), node('br'), document.createTextNode('替你'), node('span', 'accent-word', '留意着。')]));
    $('hero-description').textContent = state.profile ? '结合你提供的情况，整理值得留意的政策。尚未确认的条件，也会一项项说明。' : '补贴、保障、办事机会，从官方政策里，找到值得你关注的那一份。';
    $('profile-button').replaceChildren(document.createTextNode(state.profile ? '调整我的情况' : '看看我能申请什么'), node('span', '', '↗'));
    $('profile-note').textContent = state.profile ? `${stageLabels[state.profile.stage] || '我的情况'} · ${account.enabled() ? '情况仅保存在你的账户' : '情况保存在当前浏览器'}` : '回答几个小问题 · 无需姓名、证件或联系方式';
    document.querySelector('.wizard-privacy').textContent = account.enabled() ? '你的情况仅保存在当前账户。无需填写姓名、证件或联系方式。' : '你的情况仅保存在当前浏览器。不会上传姓名、证件或联系方式。';
    $('policy-list-title').textContent = state.profile ? '结合你的情况，值得留意' : '值得留意的支持';
    const possible = state.policies.filter(policy => policy.match?.status === 'possible').length;
    const pending = state.policies.filter(policy => policy.match?.status === 'needs_info').length;
    $('catalog-caption').textContent = state.profile ? `${possible} 项可能适用 · ${pending} 项待补信息` : `${state.policies.length} 项官方资料 · 天津`;
    $('monitor-note').textContent = state.service.monitor_running ? `本地服务运行时，每 ${Math.round((state.service.check_interval_seconds || 21600) / 3600)} 小时核对关注政策。${state.service.last_checked_at ? '最近检查：' + dateCopy(state.service.last_checked_at) + '。' : '首次检查会建立对照记录。'} 页面关闭后，仍需保持本地服务运行。` : '可手动核对官方原文。首次检查建立对照记录，后续发现变化才会提醒。';
  }
  function renderPolicies() {
    const list = $('policy-list'); list.replaceChildren();
    if (!state.policies.length) { list.append(empty('暂时没有可展示的政策', '请稍后重试，或调整你的情况。')); return; }
    const policies = ui.sortPolicies(state.policies, state.profile);
    policies.forEach(policy => {
      const row = node('article', 'policy-row'); row.dataset.policyId = policy.id;
      const benefit = node('div', 'policy-benefit'); benefit.append(node('p', 'benefit-main', policy.benefit), node('p', 'benefit-label', '具体标准以政策及审核为准'));
      const info = node('div', 'policy-info'); const titleLine = node('div', 'policy-title-line'); titleLine.append(node('h3', '', policy.title), badge(policy));
      info.append(node('div', 'policy-category', policy.category || '天津生活支持'), titleLine,
        node('p', 'policy-summary', policy.match?.reasons?.[0] || policy.summary));
      const meta = node('div', 'policy-meta'); const days = ui.daysUntil(policy.deadline, state.now);
      meta.append(node('span', days !== null && days >= 0 && days <= 7 ? 'deadline-soon' : '', deadlineCopy(policy)), node('span', '', '↗ 官方依据可追溯'));
      info.append(meta);
      const action = button('↗', 'row-action', () => openPolicy(policy.id)); action.setAttribute('aria-label', `查看${policy.title}详情`);
      row.append(benefit, info, action); list.append(row);
    });
  }
  function empty(title, text, action) {
    const box = node('div', 'empty-state'); box.append(node('h3', '', title), node('p', '', text));
    if (action) box.append(button(action.label, 'text-button', action.run)); return box;
  }
  function renderFollows() {
    const list = $('follow-list'); list.replaceChildren();
    const completed = state.follows.filter(item => item.status === 'completed').length;
    $('follow-summary').textContent = state.follows.length ? `${state.follows.length} 项关注 · ${completed} 项已完成` : '从一项值得关注的支持开始';
    if (!state.follows.length) list.append(empty('先找到一份与你有关的支持', '关注后，材料清单、申请进度和重要变化会一起留在这里。', { label: '去发现权益 →', run: () => switchView('discover') }));
    state.follows.forEach(follow => {
      const policy = policyFor(follow.policy_id); if (!policy) return;
      const row = node('article', 'follow-item'); const body = node('div'); row.dataset.status = follow.status; row.dataset.policyId = policy.id;
      const heading = node('div', 'follow-item-heading'); const stage = node('span', 'follow-stage', statusLabels[follow.status] || '准备中'); stage.dataset.state = follow.status;
      heading.append(node('h3', '', policy.title), stage); body.append(heading);
      body.append(node('p', 'follow-deadline', follow.status === 'completed' ? '已记录完成，本事项不再产生新提醒。' : deadlineCopy(policy)));
      const progress = ui.progress(policy, follow, state.profile);
      const required = ui.materialGroups(policy, state.profile).required;
      const progressCopy = `基础材料 ${required.filter(item => follow.checked_materials.includes(item.id)).length} / ${required.length} 项已准备`;
      const track = node('div', 'progress-track'); const fill = node('div', 'progress-fill');
      track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', `${policy.title}：基础材料准备进度`);
      track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.setAttribute('aria-valuenow', String(progress)); track.setAttribute('aria-valuetext', progressCopy);
      fill.style.width = `${progress}%`; track.append(fill); body.append(track, node('p', 'material-progress-copy', progressCopy));
      row.append(body, button('继续跟进 →', 'follow-open', () => openPolicy(policy.id))); list.append(row);
    });
  }
  function renderNotifications() {
    const list = $('notifications'); list.replaceChildren();
    const items = state.notifications.filter(item => followFor(item.policy_id)?.status !== 'completed' && followFor(item.policy_id));
    if (!items.length) { list.append(empty('有重要变化，再提醒你', '关注政策的原文变化与临近截止提醒会显示在这里。')); return; }
    items.forEach(item => {
      const row = node('article', `notification${item.read ? ' read' : ''}`); const body = node('div');
      body.append(node('h3', '', item.title), node('p', '', item.summary), node('span', 'notification-time', dateCopy(item.created_at)));
      if (item.diff?.length) {
        const details = node('details', 'source-details'); details.append(node('summary', '', '查看原文变化'));
        const content = node('div', 'notification-diff'); item.diff.slice(0, 4).forEach(change => { if (change.before) content.append(node('p', '', `原记录：${change.before}`)); if (change.after) content.append(node('p', '', `现记录：${change.after}`)); });
        details.append(content); body.append(details);
      }
      if (item.digest) {
        const details = node('details', 'source-details'); details.append(node('summary', '', '新通知拆解 · 需进一步核对'));
        if (item.digest.summary) details.append(node('p', '', item.digest.summary));
        Object.entries({ audience: '涉及对象', requirements: '申请条件', materials: '材料要求', timing: '时间要求', actions: '办理事项' }).forEach(([key, title]) => {
          if (item.digest[key]?.length) { details.append(node('p', '', title)); item.digest[key].slice(0, 3).forEach(text => details.append(node('p', '', text))); }
        });
        details.append(link('阅读官方原文 ↗', item.source_url)); body.append(details);
      }
      row.append(node('span', 'notification-dot'), body);
      if (!item.read) row.append(button('标为已读', 'text-button', async event => {
        const trigger = event.currentTarget; trigger.disabled = true;
        const generation = account.generation();
        try { await api(`/api/notifications/${encodeURIComponent(item.id)}`, 'PATCH', { read: true }); if (generation !== account.generation()) return; followRevision += 1; state = { ...state, notifications: state.notifications.map(value => value.id === item.id ? { ...value, read: true } : value) }; renderNotifications(); }
        catch (error) { trigger.disabled = false; notify(error.message); }
      }));
      list.append(row);
    });
  }
  function renderAll() { updateShell(); renderPolicies(); renderFollows(); renderNotifications(); }
  function switchView(view) {
    state = { ...state, view }; $('discover-view').hidden = view !== 'discover'; $('follow-view').hidden = view !== 'follow';
    $('discover-tab').classList.toggle('active', view === 'discover'); $('follow-tab').classList.toggle('active', view === 'follow');
    $('discover-tab').removeAttribute('aria-current'); $('follow-tab').removeAttribute('aria-current');
    $(view === 'discover' ? 'discover-tab' : 'follow-tab').setAttribute('aria-current', 'page');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function wizardSteps() {
    const year = new Date(state.now).getFullYear();
    return [
      { key: 'stage', title: '你目前处于哪个阶段？', description: '先了解你的阶段，安排更相关的政策阅读顺序。', options: Object.entries(stageLabels).map(([value, label]) => ({ value, label })) },
      { key: 'graduation_year', title: '你的毕业年份是？', description: '部分政策有毕业年份要求，不确定也可以继续。', options: [year + 1, year, year - 1, year - 2].map(value => ({ value, label: `${value} 年` })).concat([{ value: null, label: '其他 / 暂不确定' }]) },
      { key: 'tianjin_college', title: '你就读于天津的院校吗？', description: '只用于初步区分政策适用范围。', options: [{ value: true, label: '是，天津院校' }, { value: false, label: '不是' }, { value: null, label: '暂不确定' }] },
    ];
  }
  function renderWizard() {
    const steps = wizardSteps(); const step = steps[state.step];
    $('wizard-title').textContent = step.title; $('wizard-description').textContent = step.description;
    $('step-count').textContent = `0${state.step + 1} / 03`; $('wizard-back').hidden = state.step === 0;
    $('wizard-next').textContent = state.step === 2 ? '查看适合我的支持 →' : '继续 →';
    $('wizard-next').disabled = state.busy; $('wizard-error').hidden = true;
    [...$('step-progress').children].forEach((item, index) => item.classList.toggle('done', index <= state.step));
    const options = $('wizard-options'); options.replaceChildren();
    step.options.forEach((option, index) => {
      const label = node('label', 'choice-label'); const input = document.createElement('input');
      input.type = 'radio'; input.name = 'wizard-choice'; input.value = String(index); input.checked = Object.hasOwn(state.draft, step.key) && state.draft[step.key] === option.value;
      input.addEventListener('change', () => { state = { ...state, draft: { ...state.draft, [step.key]: option.value } }; });
      label.append(input, node('span', '', option.label)); options.append(label);
    });
  }
  function openWizard() {
    state = { ...state, step: 0, draft: state.profile ? { ...state.profile } : {}, busy: false };
    renderWizard(); $('wizard-dialog').showModal();
  }
  function renderSearch(search) {
    const note = $('search-note'); note.hidden = false; note.replaceChildren();
    const mode = search.status === 'ok' || search.status === 'live' ? search.cached ? 'cached' : 'live' : search.status === 'not_configured' ? 'not_configured' : 'error';
    const label = node('p', 'search-status'); label.dataset.state = mode;
    const labels = {
      live: '元宝搜索 · 本次实时检索已完成',
      cached: `元宝搜索 · 复用 ${timeCopy(search.checked_at)} 的检索结果`,
      not_configured: '本地条件核对 · 元宝联网检索未启用',
      error: '本地条件核对已完成 · 元宝检索暂不可用',
    };
    label.textContent = labels[mode]; note.append(label);
    if (search.status !== 'ok' && search.status !== 'live') {
      note.append(node('p', '', mode === 'not_configured' ? '当前根据已整理的官方资料初步匹配，可逐项打开原文核对。' : (search.message || '当前保留已整理的官方资料，稍后可重新检索。')));
      return;
    }
    note.append(node('p', '', '申请条件按已整理的官方依据核对；检索发现的新线索需进一步确认。'));
    const sources = node('details', 'source-details search-evidence'); sources.append(node('summary', '', `本次检索依据（${search.results?.length || 0}条）`));
    const list = node('ul', 'search-results');
    (search.results || []).slice(0, 5).forEach(result => {
      const item = node('li'); item.append(link(result.title || '查看官方来源', result.url));
      if (result.date) item.append(node('span', 'quiet', ' · ' + result.date)); list.append(item);
    });
    if (!search.results?.length) sources.append(node('p', '', '本次未召回官方结果，不能据此判断不存在相关政策。'));
    sources.append(list);
    const record = node('details', 'search-record'); record.append(node('summary', '', '检索记录'));
    record.append(node('p', '', '检索词：' + (search.query || '未返回')), node('p', '', '检索时间：' + timeCopy(search.checked_at)));
    if (search.request_id) record.append(node('p', '', 'RequestId：' + search.request_id));
    sources.append(record); note.append(sources);
  }
  async function discover(profile) {
    const generation = account.generation();
    const result = await api('/api/discover', 'POST', { profile });
    if (generation !== account.generation()) throw new Error('登录状态已更新，请在当前账户下重试。');
    if (!Array.isArray(result.policies)) throw new Error('政策数据暂时不可用，请稍后重试。');
    state = { ...state, profile: { ...profile }, policies: result.policies };
    storeProfile(profile); renderAll();
    renderSearch(result.search || {});
    return result;
  }
  async function submitWizard(event) {
    event.preventDefault(); if (state.busy) return;
    const step = wizardSteps()[state.step];
    if (!Object.hasOwn(state.draft, step.key)) { $('wizard-error').textContent = '请选择一种情况，也可以选择“暂不确定”。'; $('wizard-error').hidden = false; return; }
    if (state.step < 2) { state = { ...state, step: state.step + 1 }; renderWizard(); $('wizard-options').querySelector('input')?.focus(); return; }
    state = { ...state, busy: true }; $('wizard-next').disabled = true; $('wizard-next').textContent = '正在核对政策…';
    try { await discover(state.draft); $('wizard-dialog').close(); switchView('discover'); $('policy-list-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    catch (error) { $('wizard-error').textContent = error.message; $('wizard-error').hidden = false; }
    finally { state = { ...state, busy: false }; $('wizard-next').disabled = false; $('wizard-next').textContent = '查看适合我的支持 →'; }
  }
  function section(title) { const element = node('section', 'detail-section'); element.append(node('h3', '', title)); return element; }
  function addList(parent, values, className = 'reason-list', ordered = false) {
    const list = node(ordered ? 'ol' : 'ul', className); (values || []).forEach(text => list.append(node('li', '', typeof text === 'string' ? text : text.label || '请核对原文'))); parent.append(list);
  }
  function openPolicy(id) {
    state = { ...state, activeId: id }; renderDetail(); $('policy-dialog').showModal(); $('detail-content').scrollTop = 0;
  }
  function detailError(message) {
    $('detail-content').querySelector('.detail-error')?.remove();
    const error = node('p', 'detail-error', message); error.setAttribute('role', 'alert'); $('detail-content').append(error);
  }
  function editableQuestions(policy) {
    const separateLabels = {
      tianjin_college: '你是否就读于或毕业于天津的相关院校？',
      tianjin_origin: '你是否属于天津生源、在外省市院校毕业？',
      residence_half_year: '你是否已在天津居住满半年？',
      beijing_hebei_permit: '你是否持有符合京津冀互认条件的北京或河北居住证？',
      support_condition: '你是否符合低保家庭、零就业家庭、残疾、获得国家助学贷款或特困人员中的任一支持条件？',
    };
    const known = new Map((policy.match?.questions || []).map(question => [question.key, question]));
    const keys = new Map();
    (policy.conditions || []).forEach(condition => {
      (condition.any_of || [condition.fact]).forEach(key => {
        if (!keys.has(key)) keys.set(key, { key, condition });
      });
    });
    return [...keys.values()].map(({ key, condition }) => {
      if (known.has(key)) return known.get(key);
      const year = key === 'graduation_year';
      const years = [...new Set([condition.expected_value, state.profile?.graduation_year, new Date(state.now).getFullYear(), new Date(state.now).getFullYear() + 1, 2025, 2024].filter(value => Number.isInteger(value)))].sort((a, b) => b - a);
      return { key, label: year ? '你的毕业届次是？' : separateLabels[key] || `请确认：${condition.label}。`,
        options: year ? years.map(value => ({ value, label: `${value}届` })) : [{ value: true, label: '是' }, { value: false, label: '否' }] };
    });
  }
  function renderQuestions(policy, parent) {
    if (!state.profile) {
      const block = section('先核对，是否与你有关');
      block.append(node('p', 'condition-summary', '直接核对这一项政策的条件，暂不确定的内容可以留空。'));
      const start = button('核对我是否适用 →', 'primary-button', async event => {
        if (savingConditions) return;
        const trigger = event.currentTarget; setConditionBusy(true); trigger.textContent = '正在整理核对条件…';
        try {
          await discover({ stage: 'unknown', graduation_year: null, tianjin_college: null });
          if (state.activeId === policy.id && $('policy-dialog').open) renderDetail();
        } catch (error) { trigger.textContent = '核对我是否适用 →'; detailError(error.message); }
        finally { setConditionBusy(false); }
      });
      start.id = 'start-policy-check'; block.append(start); parent.append(block); return;
    }
    const editing = editingConditions === policy.id;
    const questions = editing ? editableQuestions(policy) : policy.match?.questions || [];
    const block = section(editing ? '修改这一项的条件' : questions.length ? '再确认一点，判断更清楚' : '条件核对');
    if (!editing) {
      const edit = button('修改已填条件', 'text-button condition-edit-button', () => {
        editingConditions = policy.id;
        const scroll = $('detail-content').scrollTop; renderDetail(); $('detail-content').scrollTop = scroll;
      });
      edit.id = 'edit-conditions';
      block.append(node('p', 'condition-summary', questions.length ? '已有回答会保留；需要纠正时可以随时修改。' : '可以修改已填的回答，重新核对当前情况。'), edit);
    }
    if (!questions.length) { parent.append(block); return; }
    const form = node('form'); const answers = conditionDrafts[policy.id] || {};
    form.append(node('p', 'subtle-note', '只回答你确定的情况；不确定的内容可以留待经办机构确认。'));
    questions.forEach((question, index) => {
      const wrap = node('div', 'question-item'); wrap.dataset.fact = question.key;
      const fieldset = document.createElement('fieldset'); fieldset.append(node('legend', '', question.label));
      const options = node('div', 'answer-options');
      (question.options || [{ value: true, label: '是' }, { value: false, label: '否' }]).concat([{ value: null, label: '不确定' }]).forEach((option, optionIndex) => {
        const label = node('label'); const input = document.createElement('input'); input.type = 'radio'; input.name = `detail-question-${index}`; input.value = String(optionIndex);
        input.dataset.fact = question.key;
        const selected = Object.hasOwn(answers, question.key) ? answers : state.profile || {};
        input.checked = selected[question.key] === option.value && Object.hasOwn(selected, question.key);
        input.addEventListener('change', () => { conditionDrafts = { ...conditionDrafts, [policy.id]: { ...(conditionDrafts[policy.id] || {}), [question.key]: option.value } }; });
        label.append(input, node('span', '', option.label)); options.append(label);
      }); fieldset.append(options); wrap.append(fieldset); form.append(wrap);
    });
    const submit = node('button', 'primary-button question-submit', '更新条件核对 →'); submit.type = 'submit'; form.append(submit);
    if (editing) form.append(button('取消修改', 'text-button condition-edit-button', () => {
      editingConditions = null; conditionDrafts = { ...conditionDrafts, [policy.id]: {} }; renderDetail();
    }));
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (savingConditions) return;
      const pending = { ...(conditionDrafts[policy.id] || {}) };
      if (!Object.keys(pending).length) { notify('请先选择一项需要更新的情况。'); return; }
      setConditionBusy(true); submit.textContent = '正在核对…'; const scroll = $('detail-content').scrollTop;
      try {
        await discover({ ...(state.profile || { stage: 'unknown', graduation_year: null, tianjin_college: null }), ...pending });
        conditionDrafts = { ...conditionDrafts, [policy.id]: {} }; editingConditions = null;
        if (state.activeId === policy.id && $('policy-dialog').open) { renderDetail(); $('detail-content').scrollTop = scroll; }
        notify('已按你的补充更新初步判断');
      }
      catch (error) { submit.textContent = '更新条件核对 →'; detailError(error.message); }
      finally { setConditionBusy(false); }
    }); block.append(form); parent.append(block);
  }
  function setConditionBusy(busy) {
    savingConditions = busy; $('profile-button').disabled = busy;
    $('policy-dialog').querySelectorAll('.question-item input,.question-submit,.condition-edit-button,#start-policy-check').forEach(item => { item.disabled = busy; });
  }
  async function saveFollow(patch, id = state.activeId) {
    const generation = account.generation();
    followRevision += 1;
    if (!followFor(id)) {
      const data = await api('/api/follows', 'POST', { policy_id: id });
      if (generation !== account.generation()) throw new Error('登录状态已更新，请在当前账户下重试。');
      state = { ...state, follows: [...state.follows.filter(item => item.policy_id !== id), data.follow] };
    }
    if (patch) {
      const data = await api(`/api/follows/${encodeURIComponent(id)}`, 'PATCH', patch);
      if (generation !== account.generation()) throw new Error('登录状态已更新，请在当前账户下重试。');
      state = { ...state, follows: state.follows.map(item => item.policy_id === id ? data.follow : item) };
    }
    renderAll();
  }
  function setFollowBusy(busy) {
    savingFollow = busy;
    $('policy-dialog').querySelectorAll('.material-item input,.follow-status-label select,#detail-actions button,.manage-actions button').forEach(item => { item.disabled = busy; });
  }
  function materialItem(policy, material, follow) {
    const label = node('label', 'material-item'); const input = document.createElement('input'); input.type = 'checkbox'; input.checked = follow?.checked_materials?.includes(material.id) || false; input.dataset.material = material.id;
    input.disabled = savingFollow;
    const copy = node('span'); copy.append(node('strong', '', material.label)); if (material.note) copy.append(node('small', '', material.note)); label.append(input, copy);
    input.addEventListener('change', async () => {
      if (savingFollow) { input.checked = followFor(policy.id)?.checked_materials?.includes(material.id) || false; return; }
      setFollowBusy(true); const checked = input.checked; const scroll = $('detail-content').scrollTop;
      const current = followFor(policy.id)?.checked_materials || [];
      const checked_materials = checked ? [...new Set([...current, material.id])] : current.filter(item => item !== material.id);
      try {
        await saveFollow({ checked_materials }, policy.id);
        if (state.activeId === policy.id && $('policy-dialog').open) {
          renderDetail(); $('detail-content').scrollTop = scroll;
        }
      } catch (error) {
        input.checked = !checked;
        if (state.activeId === policy.id && $('policy-dialog').open) detailError(error.message);
      } finally {
        setFollowBusy(false);
        if (state.activeId === policy.id && $('policy-dialog').open) $('detail-content').querySelector(`[data-material="${CSS.escape(material.id)}"]`)?.focus({ preventScroll: true });
      }
    });
    return label;
  }
  function renderMaterials(policy, parent) {
    const follow = followFor(policy.id); const groups = ui.materialGroups(policy, state.profile);
    const block = section('准备好，再出发');
    const heading = block.querySelector('h3'); const wrap = node('div', 'material-heading'); heading.replaceWith(wrap);
    wrap.append(heading, node('span', '', `${groups.required.filter(item => follow?.checked_materials?.includes(item.id)).length} / ${groups.required.length} 项基础材料已准备`));
    if (!follow) block.append(node('p', 'subtle-note', '勾选材料会将这项政策加入“我的跟进”，便于下次继续。'));
    groups.required.forEach(material => block.append(materialItem(policy, material, follow)));
    if (groups.conditional.length) {
      block.append(node('p', 'subtle-note', '基础材料进度不包含按情形材料，请结合办理方式核对。'));
      const optional = node('details', 'materials-conditional conditional-materials');
      optional.dataset.detailKey = 'conditional-materials';
      optional.append(node('summary', '', `按情况补充 · ${groups.conditional.length} 项`), node('p', 'subtle-note', '只在对应情形下提供，不计入基础材料进度。请根据材料说明和官方要求核对。'));
      groups.conditional.forEach(material => optional.append(materialItem(policy, material, follow)));
      block.append(optional);
    }
    parent.append(block);
  }
  function renderManagement(policy, parent) {
    const follow = followFor(policy.id); if (!follow) return;
    const block = section('现在，进行到哪一步？'); const label = node('label', 'follow-status-label', '申请进度'); const select = document.createElement('select'); select.setAttribute('aria-label', '申请进度');
    select.disabled = savingFollow;
    Object.entries(statusLabels).forEach(([value, text]) => { const option = node('option', '', text); option.value = value; option.selected = follow.status === value; select.append(option); });
    select.addEventListener('change', async () => {
      if (savingFollow) return;
      setFollowBusy(true); const scroll = $('detail-content').scrollTop;
      try {
        await saveFollow({ status: select.value }, policy.id);
        if (state.activeId === policy.id && $('policy-dialog').open) { renderDetail(); $('detail-content').scrollTop = scroll; }
        notify(select.value === 'completed' ? '已记为完成，这项申请不再产生新提醒' : '申请进度已保存');
      } catch (error) { select.value = follow.status; if (state.activeId === policy.id && $('policy-dialog').open) detailError(error.message); }
      finally { setFollowBusy(false); }
    }); label.append(select); block.append(label);
    const details = node('details', 'manage-details'); details.dataset.detailKey = 'management'; details.append(node('summary', '', '更多跟进操作')); const actions = node('div', 'manage-actions');
    actions.append(button('导出申请清单 ↓', 'text-button', () => exportPolicy(policy)), button('取消关注', 'text-button danger-button', async event => {
      if (savingFollow) return;
      const trigger = event.currentTarget; setFollowBusy(true); trigger.disabled = true;
      const generation = account.generation();
      followRevision += 1;
      try {
        await api(`/api/follows/${encodeURIComponent(policy.id)}`, 'DELETE', {});
        if (generation !== account.generation()) return;
        state = { ...state, follows: state.follows.filter(item => item.policy_id !== policy.id), notifications: state.notifications.filter(item => item.policy_id !== policy.id) };
        renderAll(); if (state.activeId === policy.id && $('policy-dialog').open) renderDetail(); notify('已取消关注，不再检查这项政策');
      }
      catch (error) { trigger.disabled = false; detailError(error.message); }
      finally { setFollowBusy(false); }
    })); details.append(actions); block.append(details); parent.append(block);
  }
  function renderDetail() {
    const policy = policyFor(state.activeId); if (!policy) return;
    const parent = $('detail-content');
    const openSections = parent.dataset.policyId === policy.id ? [...parent.querySelectorAll('details[open][data-detail-key]')].map(item => item.dataset.detailKey) : [];
    parent.dataset.policyId = policy.id; parent.replaceChildren();
    parent.append(node('p', 'detail-category', policy.category || '天津生活支持'));
    const titleLine = node('div', 'detail-title-line'); const title = node('h2', '', policy.title); title.id = 'detail-title'; titleLine.append(title, badge(policy)); parent.append(titleLine);
    parent.append(node('p', 'detail-benefit', policy.benefit), node('p', 'detail-summary', policy.summary));
    const dates = node('div', 'date-row'); dates.append(node('span', ui.daysUntil(policy.deadline, state.now) <= 7 && policy.deadline ? 'deadline-soon' : '', deadlineCopy(policy)));
    if (policy.valid_until) dates.append(node('span', '', `政策有效期至 ${dateCopy(policy.valid_until)}`)); parent.append(dates);
    if (policy.source_review?.status === 'needs_review') {
      parent.append(node('p', 'source-alert', '官方原文或效力标注有变化。已整理的申请条件需要重新核对，请先查看原文或联系经办机构。'));
    }
    const relevance = section('为什么值得你留意');
    addList(relevance, policy.match?.reasons?.length ? policy.match.reasons : [policy.scope || '请先了解申请对象和条件，再结合个人情况核对。']); parent.append(relevance);
    renderQuestions(policy, parent); renderManagement(policy, parent); renderMaterials(policy, parent);
    const steps = section('接下来怎么做'); addList(steps, policy.steps, 'step-list', true); parent.append(steps);
    if (policy.cautions?.length) { const caution = section('这些细节，也请留意'); addList(caution, policy.cautions, 'caution'); parent.append(caution); }
    const source = node('details', 'detail-section source-details'); source.append(node('summary', '', '官方依据与资料时间'));
    source.dataset.detailKey = 'sources';
    source.append(node('p', '', `发布机构：${policy.issuer || '详见原文'}`), node('p', '', `公布日期：${dateCopy(policy.published_at)} · 资料核对：${dateCopy(policy.source_checked_at)}`));
    if (policy.effective_at) source.append(node('p', '', `施行日期：${dateCopy(policy.effective_at)}`));
    if (policy.source_review?.checked_at) {
      source.append(node('p', '', `最近原文检查：${dateCopy(policy.source_review.checked_at)} · ${policy.source_review.status === 'needs_review' ? '有变化，待复核' : '已建立原文对照记录'}`));
      source.append(node('p', '', `官网效力标注：${policy.source_review.validity || '未标明'}；原文监测不能代替经办审核。`));
    }
    source.append(link('查看官方政策原文 ↗', policy.source_url)); parent.append(source);
    parent.append(node('p', 'detail-disclaimer', '以上用于初步了解和申请准备，不代表资格已获确认。最终资格、受理时间及材料要求，以经办机构审核和官方最新通知为准。'));
    const actions = $('detail-actions'); actions.replaceChildren();
    if (followFor(policy.id)) actions.append(link('前往官方办理 ↗', policy.official_entry || policy.source_url, 'primary-button'), button('导出清单 ↓', 'text-button', () => exportPolicy(policy)));
    else {
      const followButton = button(policy.match?.status === 'expired' ? '关注后续政策 →' : '加入我的跟进 →', 'primary-button', async event => {
        if (savingFollow) return;
        const trigger = event.currentTarget; setFollowBusy(true); trigger.disabled = true; trigger.textContent = '正在保存…';
        try { await saveFollow(undefined, policy.id); if (state.activeId === policy.id && $('policy-dialog').open) renderDetail(); notify('已加入我的跟进，材料和重要变化会保留在这里'); }
        catch (error) { trigger.disabled = false; trigger.textContent = '加入我的跟进 →'; detailError(error.message); }
        finally { setFollowBusy(false); }
      }); actions.append(followButton, link('官方入口 ↗', policy.official_entry || policy.source_url, 'text-button'));
    }
    parent.querySelectorAll('details[data-detail-key]').forEach(item => { item.open = openSections.includes(item.dataset.detailKey); });
    if (savingConditions) setConditionBusy(true);
    if (savingFollow) setFollowBusy(true);
  }
  function exportPolicy(policy) {
    const blob = new Blob([ui.markdown(policy, followFor(policy.id), state.profile)], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `天津权益雷达-${policy.id}-申请清单.md`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function checkUpdates() {
    const generation = account.generation();
    if (!state.follows.some(follow => follow.status !== 'completed')) {
      notify('目前没有待检查的事项。关注政策后，会为你核对原文与相关资料。'); return;
    }
    const trigger = $('check-updates'); trigger.disabled = true; trigger.textContent = '正在核对官方来源…';
    try {
      const data = await api('/api/check-updates', 'POST', {});
      if (generation !== account.generation()) return;
      followRevision += 1;
      state = { ...state, notifications: data.notifications || [], service: { ...state.service, last_checked_at: data.checked_at } };
      await syncBootstrap();
      if (generation !== account.generation()) return;
      updateShell(); renderNotifications();
      const failures = (data.results || []).filter(item => item.status === 'error' || item.status === 'failed').length;
      const searches = data.search_status?.runs || [];
      const searchFailures = searches.filter(run => run.status === 'error').length;
      const status = node('div', 'update-status');
      const official = node('p', '', failures ? `官网原文核对：${failures} 项暂时无法读取，保留原记录。` : `官网原文核对：完成 ${(data.results || []).length} 项。`);
      official.dataset.state = failures ? 'error' : 'ok'; status.append(official);
      const yuanbao = node('p', '', !data.search_status?.configured ? '元宝资料检索：未启用，当前仅核对已关注的官网原文。' : searchFailures ? `元宝资料检索：${searchFailures} 项查询失败，新增资料暂未核对完成。` : searches.length ? `元宝资料检索：完成 ${searches.length} 项查询，发现的资料需进一步核对。` : '元宝资料检索：本次没有执行查询。');
      yuanbao.dataset.state = !data.search_status?.configured ? 'not_configured' : searchFailures ? 'error' : searches.length ? 'ok' : 'empty'; status.append(yuanbao);
      $('updates-title').closest('section').querySelector('.update-status')?.remove();
      $('monitor-note').after(status);
      notify(failures || searchFailures ? '本次检查部分完成，具体情况已列在提醒上方。' : '本次核对已完成，请查看来源检查记录。');
    } catch (error) { notify(error.message); }
    finally { trigger.disabled = false; trigger.replaceChildren(document.createTextNode('检查更新 '), node('span', '', '↻')); }
  }
  async function syncBootstrap() {
    if (syncing || savingFollow || (account.enabled() && !account.user())) return;
    syncing = true;
    const revision = followRevision;
    try {
      const data = await api('/api/bootstrap');
      // A slower poll must not replace a follow or notification write completed after it started.
      if (revision !== followRevision || savingFollow) return;
      const previousReview = policyFor(state.activeId)?.source_review;
      const previousFollow = followFor(state.activeId);
      const now = data.now || new Date().toISOString();
      const policies = data.policies.map(policy => {
        const previous = policyFor(policy.id); let match = previous?.match;
        if (policy.source_review?.status === 'needs_review' && match?.status === 'possible') {
          match = { ...match, status: 'needs_info', reasons: ['官方原文有变化，申请条件需要重新复核。', ...match.reasons] };
        }
        if ((policy.deadline && ui.daysUntil(policy.deadline, now) < 0) || (policy.valid_until && ui.daysUntil(policy.valid_until, now) < 0)) {
          match = { status: 'expired', reasons: ['当前申请期或政策有效期已结束，请查看新的官方通知。'], questions: [] };
        }
        return { ...policy, ...(match ? { match } : {}) };
      });
      const newItems = (data.notifications || []).filter(item => !knownNotifications.has(item.id) && !item.read);
      (data.notifications || []).forEach(item => knownNotifications.add(item.id));
      state = { ...state, policies, follows: data.follows || [], notifications: data.notifications || [], service: data.service || state.service, now };
      renderAll();
      if ($('policy-dialog').open && (JSON.stringify(previousReview) !== JSON.stringify(policyFor(state.activeId)?.source_review) || JSON.stringify(previousFollow) !== JSON.stringify(followFor(state.activeId)))) {
        const scroll = $('detail-content').scrollTop; renderDetail(); $('detail-content').scrollTop = scroll;
      }
      if (newItems.length) notify(`你关注的事项有 ${newItems.length} 条新提醒，可在“我的跟进”查看。`);
    } catch {
      if (revision === followRevision) $('connection-status').textContent = '本地服务连接中断 · 稍后自动重试';
    } finally { syncing = false; }
  }
  async function bootstrap() {
    const revision = ++bootstrapRevision;
    $('retry-button').disabled = true; showError('');
    try {
      if (!await account.initialize() || revision !== bootstrapRevision) return;
      const data = await api('/api/bootstrap');
      if (revision !== bootstrapRevision) return;
      if (!Array.isArray(data.policies)) throw new Error('政策资料暂未准备好，请稍后重试。');
      state = { ...state, policies: data.policies, follows: data.follows || [], notifications: data.notifications || [], service: data.service || {}, now: data.now || new Date().toISOString(), profile: account.enabled() ? data.profile || null : readProfile() };
      knownNotifications = new Set(state.notifications.map(item => item.id));
      renderAll();
      if (state.profile) {
        try { await discover(state.profile); }
        catch (error) { $('search-note').textContent = error.message; $('search-note').hidden = false; }
      }
    } catch (error) { if (revision === bootstrapRevision) { showError(error.message); $('connection-status').textContent = '本地服务暂未连接'; $('policy-list').replaceChildren(empty('暂时连接不到政策资料', '请确认服务正在运行，然后点击上方“重新连接”。')); } }
    finally { if (revision === bootstrapRevision) $('retry-button').disabled = false; }
  }
  function resetAccountView() {
    bootstrapRevision += 1; followRevision += 1;
    state = { ...state, policies: [], follows: [], notifications: [], service: {}, profile: null, activeId: null, view: 'discover', step: 0, draft: {}, busy: false };
    conditionDrafts = {}; editingConditions = null; knownNotifications = new Set();
    savingFollow = false; savingConditions = false; syncing = false;
    clearTimeout(toastTimer); $('toast').hidden = true;
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    $('detail-content').replaceChildren(); $('detail-actions').replaceChildren();
    $('search-note').replaceChildren(); $('search-note').hidden = true;
    $('updates-title').closest('section').querySelector('.update-status')?.remove();
    $('profile-button').disabled = false; renderAll(); switchView('discover');
  }
  document.addEventListener('policy-account-change', () => {
    resetAccountView(); bootstrap();
    if (!account.user()) $('auth-username').focus({ preventScroll: true });
  });
  document.addEventListener('policy-key-change', event => {
    state = { ...state, service: { ...state.service, search_configured: event.detail.configured } };
    $('search-note').replaceChildren(); $('search-note').hidden = true; updateShell(); syncBootstrap();
  });
  $('profile-button').addEventListener('click', openWizard);
  $('discover-tab').addEventListener('click', () => switchView('discover'));
  $('follow-tab').addEventListener('click', () => switchView('follow'));
  document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); switchView('discover'); });
  $('wizard-form').addEventListener('submit', submitWizard);
  $('wizard-back').addEventListener('click', () => { state = { ...state, step: Math.max(0, state.step - 1) }; renderWizard(); });
  $('wizard-close').addEventListener('click', () => $('wizard-dialog').close());
  $('detail-close').addEventListener('click', () => $('policy-dialog').close());
  $('policy-dialog').addEventListener('close', () => {
    if (account.enabled() && !account.user()) return;
    const inFollow = state.view === 'follow';
    const row = $(inFollow ? 'follow-list' : 'policy-list').querySelector(`[data-policy-id="${CSS.escape(state.activeId || '')}"]`);
    const trigger = row?.querySelector(inFollow ? '.follow-open' : '.row-action');
    (trigger || $(inFollow ? 'follow-tab' : 'discover-tab')).focus({ preventScroll: true });
  });
  [$('wizard-dialog'), $('policy-dialog')].forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog && event.clientX < dialog.getBoundingClientRect().left) dialog.close(); }));
  $('check-updates').addEventListener('click', checkUpdates);
  $('retry-button').addEventListener('click', bootstrap);
  setInterval(() => { if (document.visibilityState === 'visible' && state.policies.length) syncBootstrap(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && state.policies.length) syncBootstrap(); });
  bootstrap();
})();
