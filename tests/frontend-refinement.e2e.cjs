// Reuses the main E2E's isolated browser and temporary SQLite service.
module.exports = async function refinementChecks({ page, baseUrl, output, assert, errors }) {
  const path = require('node:path');
  const profileKey = 'tianjin-rights-profile-v1';
  const policyRow = title => page.locator('.policy-row').filter({ hasText: title });
  const refresh = async () => {
    await page.reload();
    await page.waitForSelector('.policy-row');
  };
  const sync = () => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  const holdBootstrap = async () => {
    let captured = false;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/bootstrap', async route => {
      const response = await route.fetch();
      const data = await response.json();
      captured = true;
      await gate;
      await route.fulfill({ response, json: data });
    });
    await sync();
    for (let index = 0; index < 100 && !captured; index++) await page.waitForTimeout(20);
    assert.equal(captured, true, 'An older bootstrap snapshot is held before the write');
    return async () => {
      const delivered = page.waitForResponse(response => response.url().endsWith('/api/bootstrap'));
      release();
      await delivered;
      await page.waitForTimeout(100);
      await page.unroute('**/api/bootstrap');
    };
  };

  await page.setViewportSize({ width: 390, height: 900 });
  await page.screenshot({ path: path.join(output, 'home-refined-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await policyRow('2027届').getByRole('button').click();
  assert.equal(await page.locator('#start-policy-check').count(), 1, 'Direct policy browsing offers a condition check without the global wizard');
  await page.locator('#start-policy-check').click();
  await page.waitForSelector('.question-item');
  const questions = page.locator('.question-item');
  for (let index = 0; index < await questions.count(); index++) {
    const question = questions.nth(index);
    const key = await question.getAttribute('data-fact');
    await question.locator('input').nth(key === 'support_condition' ? 1 : 0).check();
  }
  await page.getByRole('button', { name: '更新条件核对' }).click();
  await page.waitForFunction(() => document.querySelector('#detail-content .status-badge')?.textContent === '当前不适用');
  assert.equal(await page.locator('#edit-conditions').count(), 1, 'Known false answers remain editable');
  await page.locator('#edit-conditions').click();
  await page.locator('.question-item[data-fact="support_condition"] input').first().check();
  let releaseConditionSave;
  let conditionRequests = 0;
  const conditionGate = new Promise(resolve => { releaseConditionSave = resolve; });
  await page.route('**/api/discover', async route => {
    conditionRequests += 1;
    await conditionGate;
    await route.continue();
  });
  await page.getByRole('button', { name: '更新条件核对' }).click();
  assert.equal(await page.locator('.question-item input').evaluateAll(inputs => inputs.every(input => input.disabled)), true, 'Condition answers lock while the update is pending');
  assert.equal(await page.locator('.question-submit').isDisabled(), true, 'The condition submit button locks during save');
  assert.equal(await page.locator('#profile-button').isDisabled(), true, 'The profile entry cannot start a competing update');
  await page.locator('.question-submit').evaluate(button => button.form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  for (let index = 0; index < 50 && !conditionRequests; index++) await page.waitForTimeout(10);
  assert.equal(conditionRequests, 1, 'Repeated form submission does not send a second update');
  releaseConditionSave();
  await page.waitForFunction(() => document.querySelector('#detail-content .status-badge')?.textContent === '可能适用');
  await page.unroute('**/api/discover');
  assert.equal(await page.locator('#profile-button').isDisabled(), false, 'Profile editing is restored after the save');
  assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).support_condition, profileKey), true, 'Corrected answers are persisted');
  assert.equal(await page.locator('#discover-view.has-profile').count(), 1);
  const compactHeight = await page.locator('.hero').evaluate(element => element.getBoundingClientRect().height);
  assert.ok(compactHeight < 350, 'The returning-user hero leaves room for useful policy results');
  await page.locator('#detail-close').click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.waitForFunction(() => document.getElementById('toast').hidden);
  await page.screenshot({ path: path.join(output, 'dashboard-refined-desktop.png'), fullPage: true });

  // Another tab may update the same local single-user application.
  await policyRow('2027届').getByRole('button').click();
  await page.locator('#detail-actions').getByRole('button', { name: '加入我的跟进' }).click();
  await page.waitForSelector('.follow-status-label');
  await page.locator('#edit-conditions').click();
  await page.locator('.question-item[data-fact="support_condition"] input').nth(1).check();
  await page.request.patch(baseUrl + '/api/follows/job-seeking-grant', { data: { status: 'completed', checked_materials: ['application'] } });
  await sync();
  await page.waitForFunction(() => document.querySelector('.follow-status-label select')?.value === 'completed');
  assert.equal(await page.locator('[data-material="application"]').isChecked(), true, 'An open drawer follows external material updates');
  assert.equal(await page.locator('.question-item[data-fact="support_condition"] input').nth(1).isChecked(), true, 'Unsaved condition choices survive background refresh');
  await page.request.patch(baseUrl + '/api/follows/job-seeking-grant', { data: { status: 'preparing' } });
  await refresh();
  await page.locator('#follow-tab').click();
  const markRead = page.getByRole('button', { name: '标为已读', exact: true }).first();
  assert.ok(await markRead.count(), 'A near-deadline notification is available for retry testing');
  await page.route('**/api/notifications/*', route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { message: '受控测试：保存失败，请重试' } }) }));
  const priorErrors = errors.length;
  await markRead.click();
  await page.waitForFunction(() => /受控测试/.test(document.getElementById('toast').textContent));
  assert.equal(await markRead.isDisabled(), false, 'A failed read operation restores its button');
  assert.equal(errors.length, priorErrors, 'The error handler does not throw after await');
  await page.unroute('**/api/notifications/*');
  const releaseReadPoll = await holdBootstrap();
  await markRead.click();
  await page.waitForSelector('.notification.read');
  await releaseReadPoll();
  assert.equal(await page.locator('.notification.read').count(), 1, 'An old poll cannot undo a successful read operation');

  // A controlled source-change notice verifies update/poll ordering, not a real new policy.
  const releaseUpdatePoll = await holdBootstrap();
  await page.route('**/api/check-updates', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      checked_at: '2026-10-24T12:00:00+08:00', results: [], search_status: { configured: false, runs: [] },
      notifications: [{ id: 'qa-update-order', policy_id: 'job-seeking-grant', title: '受控测试：更新顺序',
        summary: '受控测试资料，非真实新政', kind: 'source_changed', read: false, created_at: '2026-10-24T12:00:00+08:00', diff: [] }],
    }),
  }));
  await page.locator('#check-updates').click();
  await page.waitForFunction(() => !document.getElementById('check-updates').disabled);
  await releaseUpdatePoll();
  assert.match(await page.locator('.notification').innerText(), /受控测试：更新顺序/, 'An old poll cannot discard a completed update notice');
  await page.unroute('**/api/check-updates');

  // These responses exercise display states only; no Yuanbao credential is used.
  let useCache = true;
  await page.route('**/api/discover', async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, search: {
      status: 'ok', cached: useCache, checked_at: '2026-10-01T08:30:00+08:00',
      request_id: 'qa-controlled-display-fixture', query: '受控测试：公开政策检索', results: [],
    } } });
  });
  await refresh();
  await page.waitForSelector('.search-status[data-state="cached"]');
  assert.match(await page.locator('#search-note').innerText(), /复用|缓存/);
  await page.locator('#search-note .search-evidence > summary').click();
  await page.locator('#search-note .search-record summary').click();
  assert.match(await page.locator('#search-note').innerText(), /08:30/);
  assert.match(await page.locator('#search-note').innerText(), /qa-controlled-display-fixture/);
  useCache = false;
  await refresh();
  await page.waitForSelector('.search-status[data-state="live"]');
  assert.match(await page.locator('#search-note').innerText(), /实时|本次检索/);
  await page.unroute('**/api/discover');

  const residenceProfile = { stage: 'other', graduation_year: null, tianjin_college: null,
    nonlocal_registration: true, stable_basis: true, residence_half_year: true, beijing_hebei_permit: false };
  await page.evaluate(({ key, profile }) => localStorage.setItem(key, JSON.stringify(profile)), { key: profileKey, profile: residenceProfile });
  await refresh();
  await policyRow('居住证').getByRole('button').click();
  for (const material of ['identity', 'photo', 'basis_proof']) {
    await page.locator(`[data-material="${material}"]`).check();
    await page.waitForFunction(() => !document.querySelector('.material-item input')?.disabled);
  }
  assert.match(await page.locator('.material-heading span').innerText(), /3\s*\/\s*3/);
  assert.equal(await page.locator('.materials-conditional').count(), 1, 'Conditional paperwork is separated from the basic checklist');
  const residence = await (await page.request.get(baseUrl + '/api/bootstrap')).json();
  const entry = residence.policies.find(item => item.id === 'residence-permit');
  const follow = residence.follows.find(item => item.policy_id === 'residence-permit');
  assert.equal(await page.evaluate(({ policy, follow, profile }) => PolicyUI.progress(policy, follow, profile), { policy: entry, follow, profile: residenceProfile }), 100, 'Ordinary self-applicants can complete their basic checklist');
  await page.locator('.materials-conditional > summary').click();
  await page.locator('[data-material="proxy"]').check();
  await page.waitForFunction(() => !document.querySelector('.material-item input')?.disabled);
  assert.equal(await page.locator('.materials-conditional').evaluate(details => details.open), true, 'Optional materials stay expanded after a saved checkbox rerenders the drawer');
  assert.match(await page.locator('.material-heading span').innerText(), /3\s*\/\s*3/, 'Optional paperwork does not change completed basic progress');
  await page.locator('#detail-close').click();

  for (const width of [320, 360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Refined dashboard fits ${width}px`);
  }

  // Leave the existing E2E scenario a fresh profile and no followed policies.
  await page.request.delete(baseUrl + '/api/follows/job-seeking-grant', { data: {} });
  await page.request.delete(baseUrl + '/api/follows/residence-permit', { data: {} });
  await page.evaluate(key => localStorage.removeItem(key), profileKey);
  await page.setViewportSize({ width: 1440, height: 1050 });
  await refresh();
  assert.deepEqual(errors, [], 'Refinement cases do not create browser exceptions');
};
