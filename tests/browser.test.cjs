const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const { readFile, mkdir } = require('node:fs/promises');
let server, browser, authServer, authFixture;
const authBase = 'http://127.0.0.1:5057';
const base = 'http://127.0.0.1:5056';
const attack = `<img src=x onerror="window.__xss=1">`;
const roleId = `ads');window.__xss=1;//" autofocus onfocus="window.__xss=1`;

before(async () => {
  server = spawn(process.env.PYTHON || 'python3', ['-m', 'flask', '--app', 'app', 'run', '--port', '5056', '--no-debugger', '--no-reload'], { stdio: 'ignore' });
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error('Flask test server failed to start');
    try { if ((await fetch(base)).ok) break; } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  await mkdir('test-results', { recursive: true });
  authServer = spawn(process.env.PYTHON || 'python3', ['tests/auth_test_server.py'], { stdio: ['ignore', 'pipe', 'ignore'] });
  authFixture = await new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error('Auth fixture startup timed out')), 10000);
    authServer.once('error', error => { clearTimeout(timeout); reject(error); });
    authServer.stdout.on('data', chunk => {
      output += chunk;
      if (output.includes('\n')) {
        clearTimeout(timeout);
        try { resolve(JSON.parse(output.split('\n')[0])); } catch (error) { reject(error); }
      }
    });
  });
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(authBase + '/login')).ok) break; } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
});
after(async () => {
  if (browser) await browser.close();
  if (server) server.kill();
  if (authServer) authServer.kill();
});

async function pageFor(path) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // No real provider requests or credentials are used by these tests.
  await page.route('https://**/*', route => route.abort());
  await page.goto(base + path);
  await page.evaluate(() => localStorage.setItem('marketai_deepseek_key', 'test-only'));
  return { page, errors };
}
async function assertSafe(page, selector) {
  assert.equal(await page.locator(`${selector} img, ${selector} script, ${selector} iframe`).count(), 0);
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.ok((await page.locator(selector).textContent()).includes(attack));
}
async function download(page, button, filename) {
  const event = page.waitForEvent('download');
  await button.click();
  const result = await event;
  assert.equal(result.suggestedFilename(), filename);
  const content = await readFile(await result.path());
  assert.equal(content.subarray(0, 2).toString(), 'PK');
}

test('monthly report escapes model output and custom platforms, and downloads Word', async () => {
  const { page, errors } = await pageFor('/ad-report');
  try {
    page.once('dialog', dialog => dialog.accept(attack));
    await page.getByText('其他平台', { exact: false }).click();
    const custom = page.locator('.platform-panel').last();
    assert.equal(await custom.locator('img').count(), 0);
    await custom.getByRole('button', { name: '貼上數據' }).click();
    await custom.locator('textarea').fill('花費 100');
    await page.fill('#client-name', '測試客戶');
    await page.fill('#company-name', '測試公司');
    await page.fill('#report-month', '2026-09');
    let submitted;
    await page.route('**/api/ad-report/process', route => {
      submitted = route.request().postDataJSON();
      return route.fulfill({ json: {
        cover: { title: attack, client_name: attack, company_name: attack, report_month: '2026-09' },
        sections: [{ title: attack, content: `**正常粗體**\n\n| 指標 | 值 |\n| --- | --- |\n| 花費 | 100 |\n\n${attack}` }],
        closing: { title: attack, content: attack }
      } });
    });
    await page.check('#ar-period-confirmed');
    await page.getByRole('button', { name: '核對數據（不使用 AI 額度）' }).click();
    await page.locator('#ar-section-review.active').waitFor();
    await assertSafe(page, '#ar-audit-content');
    await page.check('#ar-review-confirmed');
    await page.getByRole('button', { name: '確認並產生 AI 月報' }).click();
    await page.locator('#ar-section-results.active').waitFor();
    assert.equal(submitted.platforms[attack], '花費 100');
    await assertSafe(page, '#ar-report-content');
    assert.equal(await page.locator('#ar-report-content strong').textContent(), '正常粗體');
    assert.equal(await page.locator('#ar-report-content table').count(), 1);
    await page.screenshot({ path: 'test-results/report.png', fullPage: true });
    await download(page, page.getByRole('button', { name: '匯出 DOCX' }), '廣告月報.docx');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('dispatch escapes model fields and role identifiers; custom roles still toggle/remove', async () => {
  const { page, errors } = await pageFor('/work-dispatch');
  try {
    await page.fill('#wd-project-name', '測試專案');
    await page.fill('#wd-quotation-text', '素材設計 1000 元');
    await page.getByRole('button', { name: '下一步' }).click();
    await page.locator('.role-card').first().waitFor();
    await page.fill('#wd-new-role-name', attack);
    await page.fill('#wd-new-role-desc', attack);
    await page.getByRole('button', { name: '新增角色' }).click();
    await assertSafe(page, '#wd-roles-grid');
    const custom = page.locator('.role-card.custom');
    await custom.click();
    assert.ok(!(await custom.getAttribute('class')).includes('selected'));
    await custom.click();
    await custom.locator('.role-remove').click();
    assert.equal(await page.locator('.role-card.custom').count(), 0);
    await page.route('**/api/work-dispatch/process', route => route.fulfill({ json: {
      project_name: attack, summary: attack, total_items: 1,
      work_packages: [{ id: attack, name: attack, description: attack, module: attack, amount: attack, assigned_to: roleId, assigned_role_name: attack, estimated_days: 2, priority: attack, deliverables: [attack], dependencies: [attack], notes: attack }],
      role_summary: [{ role_id: roleId, role_name: attack, package_count: 1, total_days: 2 }],
      timeline_suggestion: attack, notes: attack
    } }));
    await page.getByRole('button', { name: '開始拆分' }).click();
    await page.locator('#wd-section-results.active').waitFor();
    await assertSafe(page, '#wd-results-content');
    await page.getByRole('button', { name: '全部工作包', exact: true }).click();
    await page.locator('[data-filter-role]').click();
    assert.ok((await page.locator('#wd-view-role').getAttribute('class')).includes('active'));
    await download(page, page.getByRole('button', { name: '匯出 DOCX' }), '工作分派.docx');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('meeting export downloads from POST and shows server errors without downloading', async () => {
  const { page, errors } = await pageFor('/meeting');
  try {
    await page.route('**/api/meeting/organize', route => route.fulfill({ json: { notes: '# 會議記錄\n- 驗證下載' } }));
    // Provide a synthetic transcript, then use the existing organize/export functions.
    await page.evaluate(() => { document.getElementById('transcribed-text').value = '驗證下載'; return organizeNotes(); });
    await page.locator('#section-notes.active').waitFor();
    await download(page, page.locator('button[onclick="exportNotesDocx()"]'), '會議記錄.docx');
    let downloads = 0;
    page.on('download', () => downloads++);
    await page.route('**/api/meeting/export', route => route.fulfill({ status: 500, json: { error: '測試匯出錯誤' } }));
    await page.locator('button[onclick="exportNotesDocx()"]').click();
    await page.getByText('匯出失敗：測試匯出錯誤', { exact: true }).waitFor();
    assert.equal(downloads, 0);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});


test('KPI review uses real backend, blocks bad/stale inputs, and needs no API key', async () => {
  const { page, errors } = await pageFor('/ad-report');
  try {
    await page.evaluate(() => localStorage.clear());
    await page.fill('#client-name', '指標測試');
    await page.fill('#company-name', '測試公司');
    await page.fill('#report-month', '2026-09');
    await page.check('#ar-period-confirmed');
    const panel = page.locator('#panel-meta');
    await panel.getByRole('button', { name: '貼上數據' }).click();
    await panel.locator('.platform-currency').selectOption('TWD');
    await panel.locator('.platform-conversion-type').fill('purchase');
    await panel.locator('textarea').fill('名稱,花費,曝光,點擊,成果,轉換價值\nA,100,1000,10,2,300\nB,900,3000,90,8,1700\n總計：平台,1000,4000,100,10,2000');
    await page.getByRole('button', { name: '核對數據（不使用 AI 額度）' }).click();
    await page.locator('#ar-section-review.active').waitFor();
    const ctr = page.locator('#ar-audit-content tr').filter({ has: page.getByText('CTR (%)', { exact: true }) });
    assert.equal(await ctr.locator('td').nth(1).textContent(), '2.5');
    assert.ok((await page.locator('#ar-audit-content').textContent()).includes('排除總計行：4'));
    assert.equal(await page.locator('#ar-generate-button').isDisabled(), false);
    await page.screenshot({ path: 'test-results/kpi-review.png', fullPage: true });
    await page.check('#ar-review-confirmed');
    await page.getByRole('button', { name: '確認並產生 AI 月報' }).click();
    await page.getByText('請在 API 設定填入 DeepSeek Key 後再產生月報', { exact: true }).waitFor();
    await page.evaluate(() => { document.getElementById('api-settings-panel').classList.remove('show'); });
    await page.getByRole('button', { name: '修改資料' }).click();
    await panel.locator('textarea').fill('名稱,花費,曝光\nA,100,1000\nA,100,1000');
    await page.getByRole('button', { name: '核對數據（不使用 AI 額度）' }).click();
    await page.locator('#ar-section-review.active').waitFor();
    assert.equal(await page.locator('#ar-generate-button').isDisabled(), true);
    assert.ok((await page.locator('.audit-errors').textContent()).includes('重複匯入'));
    await page.getByRole('button', { name: '修改資料' }).click();
    await panel.locator('textarea').fill('花費：100\n曝光：1000');
    await page.getByRole('button', { name: '核對數據（不使用 AI 額度）' }).click();
    await page.locator('#ar-section-review.active').waitFor();
    // Simulate another in-flight input update after validation.
    await page.evaluate(() => { document.querySelector('#panel-meta textarea').value = '花費：999'; });
    await page.check('#ar-review-confirmed');
    await page.getByRole('button', { name: '確認並產生 AI 月報' }).click();
    await page.locator('#ar-section-info.active').waitFor();
    await page.getByText('資料未通過核對或已變更，請重新核對數據', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});


test('company login protects API and pages, keeps keys per user/tab, and clears them on logout', async () => {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const unauthorized = await fetch(authBase + '/api/ad-report/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    assert.equal(unauthorized.status, 401);
    await page.goto(authBase + '/ad-report');
    assert.ok(page.url().includes('/login'));
    await page.getByRole('link', { name: '使用 Google Workspace 登入' }).waitFor();
    await page.screenshot({ path: 'test-results/company-login.png' });
    await page.context().addCookies([{ name: 'marketai_session', value: authFixture.cookie, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
    await page.evaluate(() => localStorage.setItem('marketai_deepseek_key', 'legacy-key'));
    await page.goto(authBase + '/ad-report');
    assert.equal(await page.evaluate(() => localStorage.getItem('marketai_deepseek_key')), null);
    await page.getByRole('button', { name: 'API 設定' }).click();
    await page.fill('#deepseek-key-input', 'fake-tab-key');
    await page.locator('button[onclick="saveDeepSeekKey()"]').click();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('marketai_browser-test-user_deepseek_key')), 'fake-tab-key');
    assert.equal(await page.evaluate(() => localStorage.getItem('marketai_deepseek_key')), null);
    const status = await page.evaluate(async () => {
      const response = await fetch('/api/ad-report/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_name: '測試', company_name: '測試', report_month: '2026-09', platforms: { meta: '花費：100' } }) });
      return response.status;
    });
    assert.equal(status, 200); // Common wrapper supplied the real CSRF token.
    const forged = await page.request.post(authBase + '/api/ad-report/validate', { data: {} });
    assert.equal(forged.status(), 403); // Raw clients still require CSRF.
    let leakedHeader;
    await page.route('https://external.example/**', route => {
      leakedHeader = route.request().headers()['x-csrf-token'];
      return route.fulfill({ status: 200, headers: { 'Access-Control-Allow-Origin': '*' }, body: '{}' });
    });
    await page.evaluate(() => fetch('https://external.example/check', { method: 'POST', body: 'test' }));
    assert.equal(leakedHeader, undefined);
    const logoutResponse = page.waitForResponse(response => response.url().endsWith('/auth/logout'));
    await page.getByRole('button', { name: '登出', exact: true }).click();
    const logoutResult = await logoutResponse;
    assert.equal(logoutResult.status(), 200, await logoutResult.text());
    await page.getByRole('heading', { name: '已登出 MarketAI' }).waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('marketai_browser-test-user_deepseek_key')), null);
    const loggedOut = await page.request.get(authBase + '/api/work-dispatch/roles');
    assert.equal(loggedOut.status(), 401);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

async function libraryPage(path = '/library') {
  const page = await browser.newPage();
  page.on('dialog', dialog => dialog.accept());
  await page.context().addCookies([{ name: 'marketai_session', value: authFixture.cookie, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
  await page.goto(authBase + path);
  return page;
}
async function libraryProject(page, suffix) {
  await page.fill('#library-client-name', '客戶 ' + suffix);
  await page.getByRole('button', { name: '新增客戶', exact: true }).click();
  await page.getByText('客戶已新增。', { exact: true }).waitFor();
  await page.selectOption('#library-client', { label: '客戶 ' + suffix });
  await page.fill('#library-project-name', '專案 ' + suffix);
  await page.getByRole('button', { name: '新增專案', exact: true }).click();
  await page.getByText('專案已新增。', { exact: true }).waitFor();
  return page.locator('#library-filter option').filter({ hasText: '專案 ' + suffix }).getAttribute('value');
}
async function saveDocument(page, version) {
  await page.getByRole('button', { name: '儲存版本', exact: true }).click();
  await page.locator('#document-status').filter({ hasText: `已儲存版本 ${version}。` }).waitFor();
}

test('project library saves meeting drafts and edits, reopens versions, exports, and blocks stale saves', async () => {
  const page = await libraryPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let stale;
  try {
    const project = await libraryProject(page, '會議');
    await page.goto(authBase + '/meeting');
    await page.selectOption('#document-project', project);
    await page.fill('#document-title', '測試會議 ' + attack);
    await page.getByRole('button', { name: '直接貼上逐字稿', exact: true }).click();
    await page.fill('#meeting-text', '客戶希望下週完成素材。');
    await saveDocument(page, 1);
    const url = page.url();
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 1' }).waitFor();
    assert.equal(await page.locator('#transcribed-text').inputValue(), '客戶希望下週完成素材。');
    await page.getByRole('button', { name: 'API 設定' }).click();
    await page.fill('#deepseek-key-input', 'fixture-secret-not-saved');
    await page.locator('button[onclick="saveDeepSeekKey()"]').click();
    await page.getByRole('button', { name: 'API 設定' }).click();
    await page.route('**/api/meeting/organize', route => route.fulfill({ json: { notes: '# 會議記錄\n原始結論' } }));
    await page.getByRole('button', { name: '整理成會議記錄', exact: false }).click();
    await page.locator('#notes-content').filter({ hasText: '原始結論' }).waitFor();
    await page.locator('#document-edit summary').click();
    await page.locator('.library-editor').fill('# 修訂記錄\n' + attack);
    await saveDocument(page, 2); // Save also applies edited text without requiring a separate click.
    const id = new URL(page.url()).searchParams.get('document');
    const stored = await page.evaluate(async id => (await (await fetch('/api/library/documents/' + id)).json()), id);
    assert.ok(!JSON.stringify(stored).includes('fixture-secret-not-saved'));
    assert.ok(stored.snapshot.result.notes.includes(attack));
    stale = await libraryPage(new URL(url).pathname + new URL(url).search);
    await stale.locator('#document-status').filter({ hasText: '已開啟版本 2' }).waitFor();
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 2' }).waitFor();
    await assertSafe(page, '#notes-content');
    await download(page, page.getByRole('button', { name: '匯出 DOCX', exact: false }), '會議記錄.docx');
    await page.fill('#document-title', '第三版');
    await saveDocument(page, 3);
    await stale.fill('#document-title', '過期編輯');
    await stale.getByRole('button', { name: '儲存版本', exact: true }).click();
    await stale.locator('#document-status').filter({ hasText: '其他分頁已儲存新版本' }).waitFor();
    await page.goto(authBase + '/library');
    await page.fill('#library-query', '第三版');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByRole('heading', { name: '第三版', exact: true }).waitFor();
    await page.getByRole('button', { name: '版本歷史', exact: true }).click();
    await page.getByRole('link', { name: '開啟版本 1', exact: true }).click();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 1（最新版本 3）' }).waitFor();
    assert.equal(await page.locator('#document-title').inputValue(), '測試會議 ' + attack);
    await page.goto(authBase + '/library');
    await page.screenshot({ path: 'test-results/project-library.png', fullPage: true });
    assert.deepEqual(errors, []);
  } finally { if (stale) await stale.close(); await page.close(); }
});

test('ad report and dispatch drafts round-trip including custom platforms and roles', async () => {
  const page = await libraryPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const project = await libraryProject(page, '月報與工作包');
    await page.goto(authBase + '/ad-report');
    await page.selectOption('#document-project', project);
    await page.fill('#document-title', '廣告草稿');
    await page.fill('#client-name', '品牌');
    await page.fill('#company-name', '公司');
    await page.fill('#report-month', '2026-09');
    await page.getByText('其他平台', { exact: false }).click({ trial: true });
    // Prompt dialogs need a supplied platform name for this flow.
    page.removeAllListeners('dialog');
    page.once('dialog', dialog => dialog.accept(attack));
    await page.getByText('其他平台', { exact: false }).click();
    page.on('dialog', dialog => dialog.accept());
    const custom = page.locator('.platform-panel').last();
    await custom.getByRole('button', { name: '貼上數據' }).click();
    await custom.locator('textarea').fill('花費：100\n曝光：1000\n點擊：10');
    await custom.locator('.platform-currency').selectOption('TWD');
    await saveDocument(page, 1);
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 1' }).waitFor();
    assert.equal(await page.locator('.platform-data').count(), 1);
    assert.equal(await page.locator('.platform-data').inputValue(), '花費：100\n曝光：1000\n點擊：10');
    await assertSafe(page, '#platform-panels');
    assert.equal(await page.locator('#ar-period-confirmed').isChecked(), false);
    await page.check('#ar-period-confirmed');
    await page.getByRole('button', { name: '核對數據（不使用 AI 額度）' }).click();
    await page.locator('#ar-section-review.active').waitFor();
    await page.getByRole('button', { name: 'API 設定' }).click();
    await page.fill('#deepseek-key-input', 'fixture-key');
    await page.locator('button[onclick="saveDeepSeekKey()"]').click();
    await page.getByRole('button', { name: 'API 設定' }).click();
    const metricPrefix = '### 程式計算指標\n| 花費 | 100 |\n\n### AI 解讀（需人工覆核）\n';
    await page.route('**/api/ad-report/process', route => route.fulfill({ json: {
      cover: { title: '月報', client_name: '品牌' },
      sections: [{ id: 'platform_0', title: '平台解讀', content: metricPrefix + '原始分析' }],
      closing: { title: '覆核', content: '待覆核' }
    } }));
    await page.check('#ar-review-confirmed');
    await page.getByRole('button', { name: '確認並產生 AI 月報' }).click();
    await page.locator('#ar-section-results.active').waitFor();
    await page.locator('#document-edit summary').click();
    assert.equal(await page.locator('.library-editor').first().inputValue(), '原始分析');
    await page.locator('.library-editor').first().fill('更新分析');
    await saveDocument(page, 2);
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 2' }).waitFor();
    assert.ok((await page.locator('#ar-report-content').textContent()).includes('更新分析'));
    assert.ok((await page.locator('#ar-report-content').textContent()).includes('100'));
    await download(page, page.getByRole('button', { name: '匯出 DOCX', exact: false }), '廣告月報.docx');
    await page.goto(authBase + '/work-dispatch');
    await page.selectOption('#document-project', project);
    await page.fill('#document-title', '工作包草稿');
    await page.fill('#wd-project-name', '秋季活動');
    await page.fill('#wd-quotation-text', '製作三張宣傳圖');
    await page.getByRole('button', { name: '下一步', exact: false }).click();
    await page.fill('#wd-new-role-name', attack);
    await page.fill('#wd-new-role-desc', '視覺設計');
    await page.getByRole('button', { name: '新增角色', exact: false }).click();
    await saveDocument(page, 1);
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 1' }).waitFor();
    assert.equal(await page.locator('#wd-quotation-text').inputValue(), '製作三張宣傳圖');
    await page.getByRole('button', { name: '下一步', exact: false }).click();
    await assertSafe(page, '#wd-roles-grid');
    await page.route('**/api/work-dispatch/process', route => route.fulfill({ json: {
      project_name: '秋季活動', summary: '提案摘要', role_summary: [],
      work_packages: [{ id: 'wp1', name: '素材設計', description: '設計初稿', estimated_days: 2 }]
    } }));
    await page.getByRole('button', { name: '開始拆分', exact: false }).click();
    await page.locator('#wd-section-results.active').waitFor();
    await saveDocument(page, 2);
    await page.reload();
    await page.locator('#document-status').filter({ hasText: '已開啟版本 2' }).waitFor();
    await page.getByRole('button', { name: '全部工作包', exact: true }).click();
    assert.ok((await page.locator('#wd-view-all').textContent()).includes('設計初稿'));
    await download(page, page.getByRole('button', { name: '匯出 DOCX', exact: false }), '工作分派.docx');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});
