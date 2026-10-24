// Uses the shared E2E browser and isolated temporary database.
module.exports = async function pagePolishChecks({ page, baseUrl, output, assert }) {
  const path = require('node:path');
  const profileKey = 'tianjin-rights-profile-v1';
  const shot = async (name, fullPage = true) => {
    await page.waitForFunction(() => document.getElementById('toast').hidden);
    await page.screenshot({ path: path.join(output, name + '.png'), fullPage });
  };
  const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  const followLayout = () => page.evaluate(() => {
    const left = document.querySelector('.follow-primary').getBoundingClientRect();
    const right = document.querySelector('.follow-workspace .updates-section').getBoundingClientRect();
    return { columns: right.left >= left.right - 1, stacked: right.top >= left.bottom - 1 };
  });

  await module.exports.focusAfterSaveChecks({ page, baseUrl, assert });
  assert.equal(await page.locator('.nav button').count(), 2, 'Polishing keeps the two familiar navigation entries');
  await page.locator('#follow-tab').click();
  assert.equal(await page.locator('.follow-workspace .follow-primary').count(), 1, 'Follow items and reminders share a deliberate workspace');
  assert.equal((await followLayout()).columns, true, 'The desktop workspace shows list and reminders side by side');
  await shot('follow-empty-polished-desktop');
  await page.locator('#discover-tab').click();
  await page.setViewportSize({ width: 390, height: 900 });
  await shot('home-polished-mobile');
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.locator('#profile-button').click();
  await shot('wizard-polished-desktop', false);
  await page.locator('#wizard-close').click();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'profile-button', 'Closing the question dialog returns keyboard focus');

  const summary = await page.locator('.policy-row').first().locator('.policy-summary').boundingBox();
  await page.mouse.click(summary.x + summary.width / 2, summary.y + summary.height / 2);
  assert.equal(await page.locator('#policy-dialog').evaluate(dialog => dialog.open), true, 'Clicking card text opens its details without aiming for the arrow');
  await page.locator('#detail-close').click();
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains('row-action')), true, 'Closing policy details returns focus to its card');

  const profile = { stage: 'other', graduation_year: null, tianjin_college: null,
    nonlocal_registration: true, stable_basis: true, residence_half_year: true, beijing_hebei_permit: false };
  await page.evaluate(({ key, profile }) => localStorage.setItem(key, JSON.stringify(profile)), { key: profileKey, profile });
  await page.reload();
  await page.waitForSelector('.policy-row');
  await page.locator('.policy-row').filter({ hasText: '居住证' }).getByRole('button').click();
  await page.locator('[data-material="identity"]').check();
  await page.waitForFunction(() => !document.querySelector('.material-item input')?.disabled);
  assert.equal(await page.locator('.material-item').filter({ has: page.locator('[data-material="identity"]') }).locator('strong').evaluate(element => getComputedStyle(element).textDecorationLine), 'none', 'Prepared material names remain readable without strike-through');
  await page.locator('#detail-content').evaluate(element => { element.scrollTop = 0; });
  await shot('detail-top-polished-desktop', false);
  await page.locator('#detail-close').click();
  await page.locator('#follow-tab').click();
  assert.equal(await page.locator('.follow-item[data-status="preparing"] .follow-stage').innerText(), '准备中');
  const progress = page.getByRole('progressbar', { name: /基础材料准备进度/ });
  assert.equal(await progress.getAttribute('aria-valuenow'), '33', 'Saved one of three basic documents updates the accessible progress');
  assert.equal(await progress.getAttribute('aria-valuemin'), '0');
  assert.equal(await progress.getAttribute('aria-valuemax'), '100');
  assert.match(await progress.getAttribute('aria-valuetext'), /1\s*\/\s*3/);
  await page.getByRole('button', { name: '继续跟进' }).click();
  await page.locator('[data-material="photo"]').check();
  await page.waitForFunction(() => !document.querySelector('.material-item input')?.disabled);
  await page.locator('#detail-close').click();
  assert.equal(await progress.getAttribute('aria-valuenow'), '67', 'Preparing another document updates the existing progress bar');
  await page.getByRole('button', { name: '继续跟进' }).click();
  await page.getByLabel('申请进度', { exact: true }).selectOption('submitted');
  await page.waitForFunction(() => !document.querySelector('.follow-status-label select')?.disabled);
  await page.locator('#detail-close').click();
  assert.equal(await page.locator('.follow-item[data-status="submitted"] .follow-stage').innerText(), '已提交');
  await shot('follow-working-polished-desktop');

  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await fits(), true, `Follow workspace fits ${width}px`);
    assert.equal((await followLayout())[width >= 1024 ? 'columns' : 'stacked'], true, `Follow workspace adapts at ${width}px`);
    assert.equal(await page.locator('.follow-primary,.follow-workspace .updates-section').evaluateAll(elements => elements.every(element => element.scrollWidth <= element.clientWidth)), true, `Long titles and reminder controls fit their panels at ${width}px`);
    if (width === 390) await shot('follow-polished-mobile');
    await page.locator('#discover-tab').click();
    assert.equal(await fits(), true, `Policy cards fit ${width}px`);
    if (width === 390) {
      await shot('dashboard-polished-mobile');
      await page.locator('.policy-row').first().getByRole('button').click();
      await shot('detail-top-polished-mobile', false);
      await page.locator('#detail-close').click();
    }
    await page.locator('#follow-tab').click();
  }

  await page.getByRole('button', { name: '继续跟进' }).click();
  await page.getByLabel('申请进度', { exact: true }).selectOption('completed');
  await page.waitForFunction(() => !document.querySelector('.follow-status-label select')?.disabled);
  await page.locator('#detail-close').click();
  assert.equal(await page.locator('.follow-item[data-status="completed"] .follow-stage').innerText(), '已完成');
  assert.match(await page.locator('.follow-item .follow-deadline').innerText(), /不再产生新提醒/);
  assert.doesNotMatch(await page.locator('.follow-item .follow-deadline').innerText(), /还有\s*\d+\s*天/);

  await page.request.delete(baseUrl + '/api/follows/residence-permit', { data: {} });
  await page.evaluate(key => localStorage.removeItem(key), profileKey);
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.reload();
  await page.waitForSelector('.policy-row');
};

module.exports.focusAfterSaveChecks = async function focusAfterSaveChecks({ page, baseUrl, assert }) {
  await page.locator('.policy-row').filter({ hasText: '2027届' }).getByRole('button').click();
  await page.locator('.material-item input').first().check();
  await page.waitForFunction(() => !document.querySelector('.material-item input')?.disabled);
  await page.locator('#detail-close').click();
  await page.waitForFunction(() => document.activeElement.matches('#discover-view .row-action'), { timeout: 1500 });
  assert.equal(await page.evaluate(() => document.activeElement.matches('#discover-view .row-action')), true,
    'Closing after a material save returns focus to the redrawn policy card');
  await page.locator('#follow-tab').click();
  await page.getByRole('button', { name: '继续跟进' }).click();
  await page.getByLabel('申请进度', { exact: true }).selectOption('submitted');
  await page.waitForFunction(() => !document.querySelector('.follow-status-label select')?.disabled);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.activeElement.matches('#follow-view .follow-open'), { timeout: 1500 });
  assert.equal(await page.evaluate(() => document.activeElement.matches('#follow-view .follow-open')), true,
    'Escape after a status save returns focus to the redrawn follow entry');
  await page.request.delete(baseUrl + '/api/follows/job-seeking-grant', { data: {} });
  await page.reload();
  await page.waitForSelector('.policy-row');
};
