const strictAssert = require('node:assert/strict');
let checks = 0;
const assert = new Proxy(strictAssert, { get(target, key) { const method = target[key]; return typeof method === 'function' ? (...args) => { checks += 1; return method(...args); } : method; } });
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
let playwright;
try { playwright = require('playwright'); }
catch {
  if (!process.env.PLAYWRIGHT_MODULE) throw new Error('Install Playwright for QA, or set PLAYWRIGHT_MODULE to an existing package directory.');
  playwright = require(process.env.PLAYWRIGHT_MODULE);
}
const { chromium } = playwright;
const project = path.resolve(__dirname, '..');
let browser;
let qaServer;
let qaDirectory;

async function startQaServer() {
  qaDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'tianjin-e2e-'));
  const code = "import sys; from policy_server import create_server; server = create_server(port=0, db_path=sys.argv[1]); print(server.server_port, flush=True); server.serve_forever()";
  const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key.toUpperCase() !== 'TENCENTCLOUD_WSA_APIKEY')), PYTHONUTF8: '1' };
  qaServer = spawn(process.env.PYTHON || (process.platform === 'win32' ? 'py' : 'python3'), ['-B', '-c', code, path.join(qaDirectory, 'state.sqlite3')], { cwd: project, env, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('QA server did not start in time.')), 15000);
    qaServer.on('error', error => { clearTimeout(timer); reject(error); });
    qaServer.on('exit', code => { clearTimeout(timer); if (!output.trim()) reject(new Error('QA server exited: ' + code)); });
    qaServer.stdout.on('data', chunk => {
      output += chunk.toString();
      const port = Number(output.trim());
      if (Number.isInteger(port) && port > 0 && port <= 65535) { clearTimeout(timer); resolve('http://127.0.0.1:' + port); }
    });
  });
}
async function cleanup() {
  if (browser) await browser.close();
  if (qaServer && qaServer.exitCode === null) { const closed = once(qaServer, 'close'); qaServer.kill(); await closed; }
  if (qaDirectory && path.dirname(path.resolve(qaDirectory)) === path.resolve(os.tmpdir()) && path.basename(qaDirectory).startsWith('tianjin-e2e-')) fs.rmSync(qaDirectory, { recursive: true, force: true });
}

(async () => {
  const baseUrl = await startQaServer();
  const chrome = process.env.CHROME_PATH || (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
  browser = await chromium.launch({ headless: true, ...(chrome ? { executablePath: chrome } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const output = path.join(project, 'screenshots');
  fs.mkdirSync(output, { recursive: true });
  await page.goto(baseUrl + '/');
  await page.waitForSelector('.policy-row');
  await require('./page-polish.e2e.cjs')({ page, baseUrl, output, assert, errors });
  const refinementOutput = path.join(qaDirectory, 'refinement-screenshots');
  fs.mkdirSync(refinementOutput, { recursive: true });
  await require('./frontend-refinement.e2e.cjs')({ page, baseUrl, output: refinementOutput, assert, errors });
  assert.equal(await page.locator('.policy-row').count(), 3);
  assert.match(await page.locator('#connection-status').innerText(), /联网检索未启用/);
  await page.screenshot({ path: path.join(output, 'home-polished-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '看看我能申请什么' }).click();
  await page.getByText('在校学生', { exact: true }).click();
  await page.locator('#wizard-next').click();
  await page.getByText('2027 年', { exact: true }).click();
  await page.locator('#wizard-next').click();
  await page.getByText('是，天津院校', { exact: true }).click();
  await page.locator('#wizard-next').click();
  await page.waitForFunction(() => !document.getElementById('wizard-dialog').open);
  assert.match(await page.locator('#policy-list-title').innerText(), /结合你的情况/);
  await page.locator('.policy-row').filter({ hasText: '2027届' }).getByRole('button').click();
  const questions = page.locator('.question-item');
  for (let index = 0; index < await questions.count(); index++) await questions.nth(index).locator('input').first().check();
  await page.getByRole('button', { name: '更新条件核对' }).click();
  await page.waitForFunction(() => document.querySelector('#detail-content .status-badge')?.textContent === '可能适用');
  assert.match(await page.locator('#detail-content').innerText(), /不代表.*审核|经办机构审核/);
  await page.route('**/api/follows**', async route => {
    if (['POST', 'PATCH'].includes(route.request().method())) await new Promise(resolve => setTimeout(resolve, 250));
    await route.continue();
  });
  await page.locator('.material-item input').first().check();
  assert.equal(await page.locator('.material-item input').nth(1).isDisabled(), true, 'Material group locks while a save is pending');
  await page.locator('.material-item input').nth(1).check();
  await page.waitForFunction(() => document.querySelector('#detail-content .material-heading span')?.textContent?.startsWith('2 /'));
  assert.equal(await page.locator('.material-item input').first().isChecked(), true, 'Fast successive selections retain the first saved material');
  await page.unroute('**/api/follows**');
  let stalePollCaptured = false;
  await page.route('**/api/bootstrap', async route => {
    const response = await route.fetch(); const data = await response.json();
    stalePollCaptured = true;
    await new Promise(resolve => setTimeout(resolve, 1200));
    await route.fulfill({ response, json: data });
  });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  for (let index = 0; index < 100 && !stalePollCaptured; index++) await page.waitForTimeout(20);
  assert.equal(stalePollCaptured, true, 'A bootstrap snapshot is in flight before the next material save');
  await page.locator('.material-item input').nth(2).check();
  await page.waitForFunction(() => document.querySelector('#detail-content .material-heading span')?.textContent?.startsWith('3 /'));
  await page.waitForTimeout(1350);
  await page.unroute('**/api/bootstrap');
  await page.locator('.material-item input').nth(3).check();
  await page.waitForFunction(() => document.querySelector('#detail-content .material-heading span')?.textContent?.startsWith('4 /'));
  const saved = await (await page.request.get(baseUrl + '/api/bootstrap')).json();
  assert.equal(saved.follows.find(item => item.policy_id === 'job-seeking-grant').checked_materials.length, 4, 'A stale poll did not replace newly saved material selections');
  await page.getByLabel('申请进度', { exact: true }).selectOption('submitted');
  await page.waitForFunction(() => !document.querySelector('.follow-status-label select')?.disabled);
  await page.waitForFunction(() => document.getElementById('toast').hidden);
  await page.screenshot({ path: path.join(output, 'detail-polished-desktop.png'), fullPage: false });
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#detail-actions').getByRole('button', { name: '导出清单' }).click();
  const download = await downloadPromise;
  const downloadPath = await download.path();
  assert.match(fs.readFileSync(downloadPath, 'utf8'), /\[x\]/);
  await page.locator('#detail-close').click();
  await page.locator('#follow-tab').click();
  assert.equal(await page.locator('.follow-item').count(), 1);
  await page.reload();
  await page.waitForSelector('.policy-row');
  await page.locator('#follow-tab').click();
  await page.locator('.follow-open').click();
  assert.equal(await page.getByLabel('申请进度', { exact: true }).inputValue(), 'submitted');
  assert.equal(await page.locator('.material-item input').first().isChecked(), true);
  await page.getByLabel('申请进度', { exact: true }).selectOption('completed');
  await page.waitForFunction(() => !document.querySelector('.follow-status-label select')?.disabled);
  await page.locator('#detail-close').click();
  await page.screenshot({ path: path.join(output, 'follow-polished-desktop.png'), fullPage: true });
  for (const width of [320, 360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.locator('#discover-tab').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Home overflow ${width}`);
    await page.locator('.policy-row').first().getByRole('button').click();
    assert.equal(await page.evaluate(() => document.getElementById('policy-dialog').scrollWidth <= innerWidth), true, `Detail overflow ${width}`);
    if (width === 390) { await page.waitForFunction(() => document.getElementById('toast').hidden); await page.screenshot({ path: path.join(output, 'detail-polished-mobile.png'), fullPage: false }); }
    await page.locator('#detail-close').click();
    if (width === 390) await page.screenshot({ path: path.join(output, 'dashboard-polished-mobile.png'), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.locator('#follow-tab').click();
  await page.locator('.follow-open').click();
  await page.getByText('更多跟进操作', { exact: true }).click();
  await page.getByRole('button', { name: '取消关注', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.follow-status-label'));
  await page.locator('#detail-close').click();
  assert.equal(await page.locator('.follow-item').count(), 0);
  await page.locator('#discover-tab').click();
  await page.locator('.policy-row').filter({ hasText: '2027届' }).getByRole('button').click();
  await page.locator('#detail-actions').getByRole('button', { name: '加入我的跟进' }).click();
  await page.waitForSelector('.follow-status-label');
  await page.locator('#detail-close').click();
  await page.locator('#follow-tab').click();
  const updateResponse = page.waitForResponse(response => response.url().endsWith('/api/check-updates') && response.request().method() === 'POST', { timeout: 65000 });
  await page.locator('#check-updates').click();
  const update = await (await updateResponse).json();
  assert.ok(update.results.some(item => ['baseline', 'unchanged', 'changed'].includes(item.status)), 'A real official policy source was read successfully');
  await page.waitForFunction(() => !document.getElementById('check-updates').disabled, { timeout: 65000 });
  if (await page.getByRole('button', { name: '标为已读', exact: true }).count()) {
    await page.getByRole('button', { name: '标为已读', exact: true }).first().click();
    await page.waitForSelector('.notification.read');
  }
  await page.waitForFunction(() => document.getElementById('toast').hidden);
  await page.screenshot({ path: path.join(output, 'updates-polished-desktop.png'), fullPage: true });
  await page.locator('.follow-open').click();
  // Controlled transport fixture verifies the UI response to a monitored source change.
  await page.route('**/api/bootstrap', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.policies = data.policies.map(policy => policy.id === 'job-seeking-grant'
      ? { ...policy, source_review: { status: 'needs_review', checked_at: '2026-10-24T12:00:00+08:00', validity: '有效' } }
      : policy);
    data.notifications = [{ id: 'e2e-change-notice', policy_id: 'job-seeking-grant', title: '测试：官方原文变化', summary: '受控测试资料，非真实新政', kind: 'source_changed', read: false, created_at: '2026-10-24T12:00:00+08:00', diff: [] }];
    await route.fulfill({ response, json: data });
  });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(() => document.querySelector('#detail-content .status-badge')?.textContent === '待补信息');
  assert.match(await page.locator('.source-alert').innerText(), /重新核对/);
  await page.locator('#detail-close').click();
  await page.locator('#follow-tab').click();
  assert.equal(await page.locator('.notification').count(), 1);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.notification').count(), 1);
  await page.unroute('**/api/bootstrap');
  await page.request.delete(baseUrl + '/api/follows/job-seeking-grant', { data: {} });
  await page.route('**/api/bootstrap', route => route.abort());
  await page.reload();
  await page.waitForSelector('#global-error:not([hidden])');
  assert.match(await page.locator('#error-copy').innerText(), /连接不到/);
  await page.unroute('**/api/bootstrap');
  await page.locator('#retry-button').click();
  await page.waitForFunction(() => document.getElementById('global-error').hidden);
  assert.deepEqual(errors, []);
  console.log('Frontend E2E: ' + checks + ' assertions passed.');
  console.log('Frontend E2E passed: profile, tri-state matching, questions, follow, materials, status, persistence, export, cancel, source-change polling, notification deduplication, error recovery and responsive layouts.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(cleanup);
