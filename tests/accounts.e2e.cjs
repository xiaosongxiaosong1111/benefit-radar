// Isolated account QA uses a controlled provider transport, never a real SearchPro key.
const strictAssert = require('node:assert/strict');
let checks = 0;
const assert = new Proxy(strictAssert, { get(target, key) { const method = target[key]; return typeof method === 'function' ? (...args) => { checks += 1; return method(...args); } : method; } });
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const project = path.resolve(__dirname, '..');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
const password = require('node:crypto').randomBytes(24).toString('base64url');
const keys = { admin: 'qa-controlled-admin-Z900', alice: 'qa-controlled-alice-A001', bob: 'qa-controlled-bob-B002', invalid: 'qa-controlled-invalid-X000', environment: 'qa-controlled-environment-NOPE' };
const profileKey = 'tianjin-rights-profile-v1';
const output = path.join(project, 'screenshots');
let directory;
let server;
let browser;

const serverCode = `
import io,json,os,sys,threading
from datetime import datetime,timezone
from pathlib import Path
from policy_accounts import AccountStore
import policy_sources
import policy_tenants
from policy_server import create_server
root=Path(sys.argv[1])
store=AccountStore(root)
admin=store.ensure_admin('qa_admin',os.environ['QA_ACCOUNTS_PASSWORD'])
store.set_api_key(admin['id'],'qa-controlled-admin-Z900')
counts={'admin':0,'alice':0,'bob':0,'invalid':0,'unexpected':0}
tags={'qa-controlled-admin-Z900':'admin','qa-controlled-alice-A001':'alice','qa-controlled-bob-B002':'bob','qa-controlled-invalid-X000':'invalid'}
lock=threading.Lock()
def controlled_provider(request,timeout):
    key=request.get_header('Authorization','').removeprefix('Bearer ')
    tag=tags.get(key,'unexpected')
    with lock:
        counts[tag]+=1
        (root/'provider-counts.json').write_text(json.dumps(counts),encoding='utf-8')
    if tag in {'invalid','unexpected'}:
        data={'Response':{'Error':{'Code':'AuthFailure','Message':'Controlled QA rejection'}}}
    else:
        page={'title':'受控测试：官方资料显示用例','url':'https://www.tj.gov.cn/qa-controlled-source','date':'2026-10-24','passage':'此结果仅验证账户隔离与界面状态，不代表真实检索召回。'}
        data={'Response':{'RequestId':'qa-controlled-provider-'+tag+'-'+str(counts[tag]),'Pages':[json.dumps(page,ensure_ascii=False)]}}
    return io.BytesIO(json.dumps(data,ensure_ascii=False).encode('utf-8'))
policy_sources._open_search=controlled_provider
real_service=policy_tenants.PolicyService
def controlled_service(db_path,**kwargs):
    return real_service(db_path,**{**kwargs,'clock':lambda:datetime(2026,10,24,12,tzinfo=timezone.utc)})
policy_tenants.PolicyService=controlled_service
(root/'provider-counts.json').write_text(json.dumps(counts),encoding='utf-8')
server=create_server(port=0,accounts=True,data_dir=root,start_monitor=False)
print(server.server_port,flush=True)
server.serve_forever()
`;

async function startServer() {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'benefit-accounts-e2e-'));
  const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key.toUpperCase() !== 'TENCENTCLOUD_WSA_APIKEY')), TENCENTCLOUD_WSA_APIKEY: keys.environment, QA_ACCOUNTS_PASSWORD: password, PYTHONUTF8: '1' };
  server = spawn(python, ['-B', '-c', serverCode, directory], { cwd: project, env, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let out = ''; let errorOutput = '';
    const timer = setTimeout(() => reject(new Error('Temporary account QA server did not start: ' + errorOutput)), 15000);
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); if (!out.trim()) reject(new Error('QA server exited ' + code + ': ' + errorOutput)); });
    server.stderr.on('data', chunk => { errorOutput += chunk; });
    server.stdout.on('data', chunk => { out += chunk; if (/^\d+\s*$/.test(out)) { clearTimeout(timer); resolve('http://127.0.0.1:' + Number(out)); } });
  });
}

async function runControl(code, payload) {
  const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key.toUpperCase() !== 'TENCENTCLOUD_WSA_APIKEY')), PYTHONUTF8: '1' };
  const child = spawn(python, ['-B', '-c', code, directory], { cwd: project, env, stdio: ['pipe', 'pipe', 'pipe'] });
  let errors = ''; child.stderr.on('data', chunk => { errors += chunk; });
  child.stdin.end(JSON.stringify(payload));
  const [exit] = await once(child, 'exit');
  if (exit !== 0) throw new Error('Controlled QA state setup failed: ' + errors);
}

async function session(page, base) { return (await page.request.get(base + '/api/auth/session')).json(); }
async function data(page, base) { return (await page.request.get(base + '/api/bootstrap')).json(); }
async function metadata(page, base) { return (await page.request.get(base + '/api/account/api-key')).json(); }
async function write(page, base, method, route, body = {}) {
  const identity = await session(page, base);
  return page.request[method](base + route, { data: body, headers: identity.csrf_token ? { 'X-CSRF-Token': identity.csrf_token } : {} });
}
async function login(page, username) {
  await page.locator('#auth-username').fill(username); await page.locator('#auth-password').fill(password);
  await page.locator('#auth-submit').click(); await page.waitForSelector('#account-button:not([hidden])');
}
async function register(page, username) { await page.locator('#auth-mode-toggle').click(); await login(page, username); }
async function openSettings(page) { await page.locator('#account-button').click(); await page.waitForFunction(() => document.getElementById('account-dialog').open); }
function providerCounts() { return JSON.parse(fs.readFileSync(path.join(directory, 'provider-counts.json'), 'utf8')); }
async function noStoredSecrets(page) {
  const value = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, text: document.body.innerText }));
  for (const forbidden of [password, ...Object.values(keys)]) assert.equal(value.includes(forbidden), false, 'Credentials are absent from storage and displayed text');
}

async function holdOldBootstrap(page) {
  await page.evaluate(() => {
    const original = window.fetch; let held = false;
    window.__qaCapturedYear = null; window.__qaReleaseBody = null;
    window.__qaRestoreFetch = () => { window.fetch = original; };
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (!held && String(args[0]).endsWith('/api/bootstrap')) {
        held = true; const json = response.json.bind(response);
        response.json = async () => {
          const body = await json(); window.__qaCapturedYear = body.profile.graduation_year;
          await new Promise(resolve => { window.__qaReleaseBody = resolve; }); return body;
        };
      }
      return response;
    };
  });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(() => typeof window.__qaReleaseBody === 'function');
  assert.equal(await page.evaluate(() => window.__qaCapturedYear), 2027, 'Headers arrived for the previous account, with its JSON body delayed');
  return async () => {
    await page.evaluate(() => { window.__qaReleaseBody(); window.__qaRestoreFetch(); delete window.__qaReleaseBody; delete window.__qaRestoreFetch; delete window.__qaCapturedYear; });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  };
}

async function setupUsers(base) {
  const contextA = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const contextB = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const adminContext = await browser.newContext(); const pageA = await contextA.newPage(); const pageB = await contextB.newPage();
  const admin = await adminContext.newPage(); const errors = [];
  for (const page of [pageA, pageB, admin]) page.on('pageerror', error => errors.push(String(error)));
  for (const page of [pageA, pageB]) await page.addInitScript(({ key }) => localStorage.setItem(key, JSON.stringify({ stage: 'student', graduation_year: 1999, tianjin_college: true })), { key: profileKey });
  await pageA.goto(base); await pageA.waitForSelector('#auth-view:not([hidden])');
  assert.equal((await pageA.request.get(base + '/api/bootstrap')).status(), 401, 'Anonymous users cannot read personal business data');
  assert.equal((await session(pageA, base)).user, null);
  await pageA.screenshot({ path: path.join(output, 'accounts-login-desktop.png') });
  const escalation = await pageA.request.post(base + '/api/auth/register', { data: { username: 'qa_extra', password, is_admin: true } });
  assert.equal(escalation.status(), 400, 'A registration payload cannot request the administrator role');
  await register(pageA, 'qa_alice'); await pageA.waitForSelector('.policy-row');
  const userA = (await session(pageA, base)).user; assert.equal(userA.is_admin, false);
  assert.equal((await data(pageA, base)).profile ?? null, null, 'Account mode ignores an old shared browser profile');
  assert.equal(await pageA.locator('#auth-password').inputValue(), '', 'The submitted password is cleared');
  await pageB.goto(base); await pageB.waitForSelector('#auth-view:not([hidden])'); await register(pageB, 'qa_bob');
  const userB = (await session(pageB, base)).user; assert.notEqual(userB.id, userA.id);
  await admin.goto(base); await admin.waitForSelector('#auth-view:not([hidden])'); await login(admin, 'qa_admin');
  assert.equal((await session(admin, base)).user.is_admin, true);
  return { base, pageA, pageB, admin, contextA, contextB, userA, userB, errors };
}

async function prepareAlice(fixture) {
  const { pageA, pageB, base } = fixture;
  await pageA.locator('#profile-button').click(); await pageA.getByText('在校学生', { exact: true }).click();
  await pageA.locator('#wizard-next').click(); await pageA.getByText('2027 年', { exact: true }).click();
  await pageA.locator('#wizard-next').click(); await pageA.getByText('是，天津院校', { exact: true }).click();
  await pageA.locator('#wizard-next').click(); await pageA.waitForFunction(() => !document.getElementById('wizard-dialog').open);
  await pageA.locator('.policy-row').filter({ hasText: '2027届' }).getByRole('button').click();
  const questions = pageA.locator('.question-item');
  for (let index = 0; index < await questions.count(); index++) await questions.nth(index).locator('input').first().check();
  await pageA.getByRole('button', { name: '更新条件核对' }).click();
  await pageA.waitForFunction(() => document.querySelector('#detail-content .status-badge')?.textContent === '可能适用');
  await pageA.locator('[data-material="application"]').check();
  await pageA.waitForFunction(() => document.querySelector('.material-heading span')?.textContent.startsWith('1 /'));
  await pageA.locator('#detail-close').click();
  const a = await data(pageA, base); const b = await data(pageB, base);
  assert.equal(a.profile.graduation_year, 2027); assert.deepEqual(a.follows[0].checked_materials, ['application']);
  assert.deepEqual(b.follows, []); assert.equal(b.profile ?? null, null); assert.deepEqual(b.notifications, []);
  assert.equal(b.service.search_configured, false, 'An unconfigured user cannot inherit the administrator or environment key');
  assert.deepEqual(providerCounts(), { admin: 0, alice: 0, bob: 0, invalid: 0, unexpected: 0 }, 'Discovery without an account key made no paid provider request');
}

async function checkDataIsolation(fixture) {
  const { pageA, pageB, base, userA } = fixture;
  const code = "import json,sys; from pathlib import Path; from policy_accounts import AccountStore; from policy_tenants import TenantServices; value=json.load(sys.stdin); pool=TenantServices(AccountStore(Path(sys.argv[1]))); pool.service_for(value['user_id'])._add_notification({'id':'qa-private-a-notice','policy_id':'job-seeking-grant','title':'受控测试：仅账户A可见','summary':'验证个人提醒隔离，非真实政策变化','kind':'review','diff':[]}); pool.close()";
  await runControl(code, { user_id: userA.id });
  assert.ok((await data(pageA, base)).notifications.some(item => item.id === 'qa-private-a-notice'));
  assert.equal((await data(pageB, base)).notifications.some(item => item.id === 'qa-private-a-notice'), false);
  await write(pageB, base, 'post', '/api/follows', { policy_id: 'job-seeking-grant' });
  const rejected = await write(pageB, base, 'patch', '/api/notifications/qa-private-a-notice', { read: true });
  assert.equal(rejected.status(), 404, 'A user cannot mark another account notification read even when its ID is known');
  const override = await write(pageB, base, 'post', '/api/follows', { policy_id: 'residence-permit', user_id: userA.id });
  assert.equal(override.status(), 400, 'Client supplied ownership is rejected');
  const noCsrf = await pageA.request.post(base + '/api/follows', { data: { policy_id: 'residence-permit' } });
  assert.equal(noCsrf.status(), 403, 'Authenticated writes need the session CSRF token');
  const crossSite = await pageA.request.post(base + '/api/follows', { data: { policy_id: 'residence-permit' }, headers: { Origin: 'https://untrusted.example' } });
  assert.equal(crossSite.status(), 421, 'Foreign Origins remain rejected');
  const profileB = { stage: 'employed', graduation_year: 2024, tianjin_college: false };
  await write(pageB, base, 'post', '/api/discover', { profile: profileB }); await pageB.reload(); await pageB.waitForSelector('.policy-row');
  assert.equal((await data(pageB, base)).profile.graduation_year, 2024); assert.equal((await data(pageA, base)).profile.graduation_year, 2027);
  assert.deepEqual((await data(pageB, base)).follows[0].checked_materials, []);
  assert.deepEqual((await data(pageA, base)).follows[0].checked_materials, ['application']);
}

async function checkKeyStorage(fixture) {
  const { pageA, pageB, admin, base, contextA } = fixture;
  const original = (await metadata(pageA, base)).api_key; assert.equal(original.configured, false);
  const b = (await metadata(pageB, base)).api_key; assert.equal(b.configured, false);
  const adminKey = (await metadata(admin, base)).api_key; assert.equal(adminKey.configured, true); assert.match(adminKey.masked_key, /Z900$/);
  await openSettings(pageA); assert.equal(await pageA.locator('#account-api-key').getAttribute('type'), 'password');
  await pageA.locator('#account-api-key').fill(keys.alice);
  const saved = await (await write(pageA, base, 'post', '/api/account/api-key', { api_key: keys.alice })).json();
  assert.equal(saved.api_key.status, 'saved_unverified', 'Saving is not presented as a successful provider validation');
  assert.equal(JSON.stringify(saved).includes(keys.alice), false, 'A key is never returned by the save endpoint');
  await pageA.locator('#account-close').click();
  await pageA.waitForFunction(() => document.getElementById('account-api-key').value === '');
  assert.equal(await pageA.locator('#account-api-key').inputValue(), '', 'Closing settings clears an unsubmitted secret');
  await openSettings(pageA); await pageA.waitForSelector('#account-key-status[data-state="saved_unverified"]');
  assert.match(await pageA.locator('#account-key-mask').innerText(), /A001$/);
  assert.equal(await pageA.locator('#account-api-key').inputValue(), '');
  assert.equal((await metadata(pageB, base)).api_key.configured, false);
  const cookie = (await contextA.cookies()).find(item => item.name === 'benefit_session');
  assert.equal(cookie.httpOnly, true); assert.equal(cookie.sameSite, 'Lax');
  await noStoredSecrets(pageA);
}

async function checkProviderStatus(fixture) {
  const { pageA, pageB, base } = fixture;
  await pageA.locator('#account-save-verify').click(); await pageA.waitForSelector('#account-key-status[data-state="verified"]');
  assert.ok((await metadata(pageA, base)).api_key.verified_at, 'The controlled provider response sets the verification time');
  assert.equal(providerCounts().alice, 1, 'Verification bypassed search cache and used Alice own key');
  assert.equal(await pageA.locator('#account-api-key').inputValue(), ''); await noStoredSecrets(pageA);
  await pageA.screenshot({ path: path.join(output, 'accounts-settings-desktop.png') });
  await openSettings(pageB); await pageB.locator('#account-api-key').fill(keys.bob); await pageB.locator('#account-save-verify').click();
  await pageB.waitForSelector('#account-key-status[data-state="verified"]'); assert.equal(providerCounts().bob, 1);
  assert.match((await metadata(pageA, base)).api_key.masked_key, /A001$/);
  assert.match((await metadata(pageB, base)).api_key.masked_key, /B002$/);
  await pageA.locator('#account-api-key').fill(keys.invalid); await pageA.locator('#account-save-verify').click();
  await pageA.waitForSelector('#account-key-status[data-state="error"]'); assert.equal(providerCounts().invalid, 1);
  assert.equal((await metadata(pageA, base)).api_key.verified_at, null, 'A replacement key cannot inherit the old validation');
  assert.equal(await pageA.locator('#account-api-key').inputValue(), '', 'A rejected verification still clears the saved secret field');
  await pageA.locator('#account-delete-key').click(); await pageA.waitForSelector('#account-key-status[data-state="not_configured"]');
  assert.equal((await data(pageA, base)).service.search_configured, false);
  assert.equal((await metadata(pageB, base)).api_key.status, 'verified');
  const result = await (await write(pageA, base, 'post', '/api/discover', { profile: (await data(pageA, base)).profile })).json();
  assert.equal(result.search.status, 'not_configured', 'Deleting a key also prevents reuse of previously configured search results');
  assert.equal(providerCounts().unexpected, 0); assert.equal(providerCounts().admin, 0);
  await noStoredSecrets(pageA); await noStoredSecrets(pageB);
}

async function checkAccountSwitching(fixture) {
  const { pageA, pageB, base, contextA, contextB } = fixture;
  const expired = (await contextB.cookies()).find(item => item.name === 'benefit_session').value;
  await runControl("import json,sys; from pathlib import Path; from policy_accounts import AccountStore; value=json.load(sys.stdin); AccountStore(Path(sys.argv[1])).delete_session(value['token'])", { token: expired });
  await pageB.reload(); await pageB.waitForSelector('#auth-view:not([hidden])');
  assert.equal((await pageB.request.get(base + '/api/bootstrap')).status(), 401, 'An expired session cannot read cached account data');
  const sibling = await contextA.newPage(); sibling.on('pageerror', error => fixture.errors.push(String(error)));
  await sibling.goto(base); await sibling.waitForSelector('.policy-row');
  const releaseOld = await holdOldBootstrap(pageA);
  await pageA.locator('#account-logout').click(); await pageA.waitForSelector('#auth-view:not([hidden])');
  assert.equal(await pageA.locator('#account-api-key').inputValue(), ''); assert.equal(await pageA.locator('#auth-password').inputValue(), '');
  await login(pageA, 'qa_bob'); await pageA.waitForSelector('.policy-row');
  await releaseOld();
  await pageA.waitForFunction(() => document.getElementById('profile-note').textContent.includes('已经就业'));
  assert.equal((await session(pageA, base)).user.username, 'qa_bob');
  const current = await data(pageA, base); assert.equal(current.profile.graduation_year, 2024);
  assert.deepEqual(current.follows[0].checked_materials, []); assert.equal(current.notifications.some(item => item.id === 'qa-private-a-notice'), false);
  await pageA.locator('#follow-tab').click(); assert.match(await pageA.locator('.follow-item').innerText(), /0\s*\/\s*5/);
  await openSettings(pageA); await pageA.waitForSelector('#account-key-status[data-state="verified"]');
  assert.match(await pageA.locator('#account-key-mask').innerText(), /B002$/);
  await sibling.bringToFront();
  await sibling.waitForFunction(() => document.getElementById('account-button').textContent.includes('qa_bob'));
  await sibling.locator('#follow-tab').click();
  assert.match(await sibling.locator('.follow-item').innerText(), /0\s*\/\s*5/, 'Another tab cannot retain the old account materials');
  assert.equal((await sibling.locator('#notifications').innerText()).includes('仅账户A可见'), false);
  await sibling.close();
  await noStoredSecrets(pageA);
}

async function checkResponsive(fixture) {
  const { pageA } = fixture;
  for (const width of [320, 390]) {
    await pageA.setViewportSize({ width, height: 900 });
    assert.equal(await pageA.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Account shell fits ' + width);
    assert.equal(await pageA.locator('#account-dialog').evaluate(element => element.scrollWidth <= element.clientWidth && element.getBoundingClientRect().width <= innerWidth), true, 'Account settings fits ' + width);
    for (const id of ['account-close', 'account-save-verify', 'account-delete-key', 'account-logout']) assert.ok((await pageA.locator('#' + id).boundingBox()).height >= 44, id + ' remains touchable at ' + width);
    if (width === 390) await pageA.screenshot({ path: path.join(output, 'accounts-settings-mobile.png') });
  }
  await pageA.locator('#account-logout').click(); await pageA.waitForSelector('#auth-view:not([hidden])');
  assert.equal(await pageA.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'The mobile login gate fits');
  await pageA.screenshot({ path: path.join(output, 'accounts-login-mobile.png') });
}

(async () => {
  const base = await startServer(); fs.mkdirSync(output, { recursive: true });
  browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' } : {}) });
  const fixture = await setupUsers(base);
  await prepareAlice(fixture); await checkDataIsolation(fixture); await checkKeyStorage(fixture);
  await checkProviderStatus(fixture); await checkAccountSwitching(fixture); await checkResponsive(fixture);
  assert.deepEqual(fixture.errors, [], 'Account workflows do not throw browser exceptions');
  console.log('Accounts E2E: ' + checks + ' assertions passed. Controlled provider only; no real credential or user data was used.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) { const closed = once(server, 'close'); server.kill(); await closed; }
  if (directory && path.dirname(path.resolve(directory)) === path.resolve(os.tmpdir()) && path.basename(directory).startsWith('benefit-accounts-e2e-')) fs.rmSync(directory, { recursive: true, force: true });
});
