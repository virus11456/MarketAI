const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const { readFile, mkdir } = require('node:fs/promises');
let server, browser;
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
});
after(async () => {
  if (browser) await browser.close();
  if (server) server.kill();
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
    await page.getByRole('button', { name: '產出月報' }).click();
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
