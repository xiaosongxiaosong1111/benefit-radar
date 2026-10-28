(function (root) {
  'use strict';
  const labels = { possible: '可能适用', needs_info: '待补信息', unlikely: '当前不适用', expired: '已截止' };
  const stages = { preparing: '准备中', submitted: '已提交', completed: '已完成' };
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function daysUntil(value, now = new Date().toISOString()) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date(now));
    return Math.round((Date.parse(value + 'T00:00:00Z') - Date.parse(today + 'T00:00:00Z')) / 86400000);
  }
  function matchLabel(policy) { return labels[policy.match?.status] || '了解申请条件'; }
  function sortPolicies(policies, profile) {
    const rank = { possible: 0, needs_info: 1, unlikely: 2, expired: 3 };
    const preferred = { student: 'job-seeking-grant', graduate: 'flexible-employment', flexible: 'flexible-employment', employed: 'residence-permit', other: 'residence-permit' }[profile?.stage];
    return [...policies].sort((a, b) => (rank[a.match?.status] ?? 1) - (rank[b.match?.status] ?? 1)
      || Number(b.id === preferred) - Number(a.id === preferred));
  }
  function materialGroups(policy, profile = {}) {
    const answers = profile || {};
    const conditionalIds = new Set();
    if (policy.id === 'residence-permit') {
      conditionalIds.add('proxy');
      if (!(answers.residence_half_year !== true && answers.beijing_hebei_permit === true)) conditionalIds.add('mutual_permit');
    }
    if (policy.id === 'flexible-employment' && !(answers.tianjin_college !== true && answers.tianjin_origin === true)) conditionalIds.add('origin_proof');
    const items = policy.materials || [];
    return { required: items.filter(item => !conditionalIds.has(item.id)), conditional: items.filter(item => conditionalIds.has(item.id)) };
  }
  function progress(policy, follow, profile) {
    const items = materialGroups(policy, profile).required;
    return items.length ? Math.round(items.filter(item => follow?.checked_materials?.includes(item.id)).length / items.length * 100) : 0;
  }
  function nextStep(policy, follow, profile, now) {
    if (follow?.status === 'completed') return { kind: 'progress', label: '查看办理记录', text: '已记录完成，可回看材料和办理进度。' };
    if (policy.source_review?.status === 'needs_review') return { kind: 'source', label: '核对最新依据', text: '官方原文有变化，请先核对最新要求与你的申请是否有关。' };
    if (follow?.status === 'submitted') return { kind: 'progress', label: '记录办理进展', text: '已记录提交，请留意经办机构反馈；需要补件时可继续更新材料。' };
    if ((policy.deadline && daysUntil(policy.deadline, now) < 0) || (policy.valid_until && daysUntil(policy.valid_until, now) < 0) || policy.match?.status === 'expired') {
      return { kind: 'source', label: '查看官方依据', text: '当前申请期或政策有效期已结束，请核对新的官方通知。' };
    }
    if (policy.effective_at && daysUntil(policy.effective_at, now) > 0) return { kind: 'source', label: '查看施行安排', text: '当前政策尚未施行，请先核对官方公布的办理安排。' };
    if (policy.match?.status === 'unlikely') return { kind: 'conditions', label: '重新核对条件', text: '你已填的情况与部分条件不符，请先核对这些回答。' };
    if (policy.match?.status !== 'possible') return { kind: 'conditions', label: '补充申请条件', text: '还有申请条件未确认，先核对是否适用，再准备材料。' };
    const required = materialGroups(policy, profile).required;
    if (!required.length) return { kind: 'source', label: '核对材料要求', text: '材料要求仍需核对，请先查看官方说明。' };
    const missing = required.filter(item => !follow?.checked_materials?.includes(item.id));
    if (missing.length) return { kind: 'materials', label: '准备下一项材料', text: `还差 ${missing.length} 项基础材料，下一项：${missing[0].label}。`, materialId: missing[0].id };
    return { kind: 'apply', label: '核对并前往办理', text: '基础材料已勾选完成，请核对按情形补充材料和办理步骤，再到官方渠道申请。' };
  }
  function markdown(policy, follow, profile) {
    const lines = ['# ' + policy.title, '', '> 天津权益雷达 · 申请准备清单', '',
      '- 支持内容：' + (policy.benefit || '以官方规定为准'),
      '- 初步判断：' + matchLabel(policy),
      '- 跟进进度：' + (stages[follow?.status] || '尚未跟进')];
    if (policy.deadline) lines.push('- 申请截止：' + policy.deadline);
    if (policy.valid_until) lines.push('- 政策有效期至：' + policy.valid_until);
    lines.push('', '## 与你的关系', '', ...(policy.match?.reasons || []).map(item => '- ' + item));
    const groups = materialGroups(policy, profile);
    for (const [key, title] of [['required', '基础必备材料'], ['conditional', '按情况补充']]) {
      if (!groups[key].length) continue;
      lines.push('', '## ' + title, '');
      if (key === 'conditional') lines.push('仅在对应办理情形下提供；不计入基础材料准备进度，具体要求请核对官方渠道。', '');
      lines.push(...groups[key].map(item => '- [' + (follow?.checked_materials?.includes(item.id) ? 'x' : ' ') + '] ' + item.label + (item.note ? '：' + item.note : '')));
    }
    lines.push('', '## 办理步骤', '', ...(policy.steps || []).map((item, index) => (index + 1) + '. ' + item));
    lines.push('', '## 官方依据', '', '- ' + policy.source_url, '- 发布机构：' + (policy.issuer || '见原文'),
      '- 资料核对时间：' + (policy.source_checked_at || '请查看原文'), '',
      '> 本清单用于准备申请，最终资格和材料要求以经办机构审核及官方最新通知为准。', '');
    return lines.join('\n');
  }
  const api = Object.freeze({ safeUrl, daysUntil, matchLabel, sortPolicies, materialGroups, progress, nextStep, markdown, stages });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PolicyUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
