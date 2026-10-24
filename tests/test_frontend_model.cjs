const strictAssert = require('node:assert/strict');
let checks = 0;
const assert = new Proxy(strictAssert, { get(target, key) { const method = target[key]; return typeof method === 'function' ? (...args) => { checks += 1; return method(...args); } : method; } });
const ui = require('../static/app-model.js');

assert.equal(ui.safeUrl('https://www.tj.gov.cn/policy'), 'https://www.tj.gov.cn/policy');
for (const value of ['javascript:alert(1)', 'http://www.tj.gov.cn/policy', 'https://www.tj.gov.cn@evil.test/x']) {
  assert.equal(ui.safeUrl(value), null);
}
assert.equal(ui.matchLabel({ match: { status: 'possible' } }), '可能适用');
assert.equal(ui.matchLabel({ match: { status: 'unlikely' } }), '当前不适用');
assert.equal(ui.daysUntil('2026-10-31', '2026-10-24T12:00:00+08:00'), 7);
assert.equal(ui.daysUntil('2026-10-23', '2026-10-24T00:30:00+08:00'), -1);
assert.equal(ui.daysUntil(null), null);
assert.equal(ui.safeUrl('not a URL'), null);
assert.equal(ui.daysUntil('unknown'), null);
assert.equal(ui.matchLabel({}), '了解申请条件');
const policy = {
  id: 'test', title: '测试政策', benefit: '按规定核定', summary: '用于验证导出',
  source_url: 'https://www.tj.gov.cn/policy', materials: [{ id: 'proof', label: '证明材料' }],
  match: { status: 'needs_info', reasons: ['毕业年份还需确认'], questions: [] },
};
const report = ui.markdown(policy, { status: 'preparing', checked_materials: ['proof'] });
assert.match(report, /\[x\] 证明材料/);
assert.match(report, /毕业年份还需确认/);
assert.match(report, /https:\/\/www\.tj\.gov\.cn\/policy/);
assert.match(report, /经办机构审核/);
assert.equal(ui.progress(policy, { checked_materials: ['unknown'] }), 0);
assert.equal(ui.progress(policy, { checked_materials: ['proof'] }), 100);

const residence = { ...policy, id: 'residence-permit', materials: [
  { id: 'identity', label: '身份证' }, { id: 'photo', label: '近期照片' },
  { id: 'basis_proof', label: '居住或就业证明' },
  { id: 'mutual_permit', label: '京冀居住证' }, { id: 'proxy', label: '代办证明' },
] };
const residenceSnapshot = JSON.stringify(residence);
const regular = ui.materialGroups(residence, { residence_half_year: true });
assert.deepEqual(regular.required.map(item => item.id), ['identity', 'photo', 'basis_proof']);
assert.deepEqual(regular.conditional.map(item => item.id), ['mutual_permit', 'proxy']);
const ready = { status: 'preparing', checked_materials: ['identity', 'photo', 'basis_proof'] };
assert.equal(ui.progress(residence, ready, { residence_half_year: true }), 100);
assert.equal(ui.progress(residence, ready, { residence_half_year: false, beijing_hebei_permit: true }), 75);
assert.equal(ui.progress(residence, ready, { residence_half_year: null, beijing_hebei_permit: true }), 75, 'A confirmed exception needs its proof when the main condition is unknown');
assert.deepEqual(ui.materialGroups(residence, { residence_half_year: false, beijing_hebei_permit: true }).required.map(item => item.id), ['identity', 'photo', 'basis_proof', 'mutual_permit']);
assert.equal(JSON.stringify(residence), residenceSnapshot, 'Grouping must preserve source materials and stored checkbox IDs');
const residenceReport = ui.markdown(residence, ready, { residence_half_year: true });
assert.match(residenceReport, /## 基础必备材料/);
assert.match(residenceReport, /## 按情况补充/);
assert.ok(residenceReport.indexOf('## 按情况补充') < residenceReport.indexOf('京冀居住证'));

const flexible = { ...policy, id: 'flexible-employment', materials: [
  { id: 'application', label: '申请表' }, { id: 'origin_proof', label: '天津生源证明' },
] };
assert.equal(ui.materialGroups(flexible, { tianjin_college: true }).required.length, 1);
assert.equal(ui.materialGroups(flexible, { tianjin_college: false, tianjin_origin: true }).required.length, 2);
assert.equal(ui.materialGroups(flexible, { tianjin_college: null, tianjin_origin: true }).required.length, 2, 'Confirmed Tianjin origin needs proof when the college is unknown');
assert.equal(ui.progress({ materials: [] }, null), 0);
assert.equal(ui.progress({}, null, null), 0, 'Missing materials never imply completion');
const recommendations = [
  { id: 'job-seeking-grant', match: { status: 'needs_info' } },
  { id: 'flexible-employment', match: { status: 'needs_info' } },
  { id: 'residence-permit', match: { status: 'needs_info' } },
];
assert.equal(ui.sortPolicies(recommendations, { stage: 'flexible' })[0].id, 'flexible-employment');
assert.equal(ui.sortPolicies(recommendations, { stage: 'employed' })[0].id, 'residence-permit');
const possibleFirst = recommendations.map(item => item.id === 'job-seeking-grant' ? { ...item, match: { status: 'possible' } } : item);
assert.equal(ui.sortPolicies(possibleFirst, { stage: 'flexible' })[0].id, 'job-seeking-grant', 'Matching facts take precedence over presentation preferences');
assert.equal(recommendations[0].id, 'job-seeking-grant', 'Recommendations must not mutate the source order');
assert.deepEqual(ui.sortPolicies(recommendations, null), recommendations, 'An unknown stage keeps the existing reading order');
assert.equal(ui.sortPolicies([{ id: 'unknown' }, possibleFirst[0]], null)[0].id, 'job-seeking-grant');
const timedReport = ui.markdown({ ...policy, deadline: '2026-10-31', valid_until: '2026-12-31',
  issuer: '测试发布机构', source_checked_at: '2026-10-24', materials: [{ id: 'proof', label: '证明材料', note: '请备原件' }] }, null);
assert.match(timedReport, /申请截止：2026-10-31[\s\S]*政策有效期至：2026-12-31/);
assert.match(timedReport, /\[ \] 证明材料：请备原件/);
console.log('Frontend model: ' + checks + ' checks passed.');
