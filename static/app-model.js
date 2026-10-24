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
  const api = Object.freeze({ safeUrl, daysUntil, matchLabel, sortPolicies, materialGroups, progress, markdown, stages });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PolicyUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
