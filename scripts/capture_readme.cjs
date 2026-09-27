'use strict';
// Real packaged Electron UI + existing project data. No fixture or DOM content replacement.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs', 'images');
const scratch = path.join(root, '.local', 'readme-capture');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(scratch, { recursive: true });

(async () => {
  let app;
  const report = { capturedAt: new Date().toISOString(), method: 'BrowserWindow.capturePage',
    data: 'existing local project', manipulatedContent: false, images: [], errors: [] };
  try {
    app = await _electron.launch({
      executablePath: path.join(root, 'dist', 'JevContext', 'JevContext.exe'),
      args: ['--force-device-scale-factor=1'],
      cwd: root,
      env: { ...process.env, JEV_MANAGER_USER_DATA: path.join(scratch, 'user-data'),
        JEV_MANAGER_TEST_HIDDEN: '1' },
    });
    const page = await app.firstWindow();
    try {
      await page.locator('#setup-dialog').waitFor({ state: 'visible', timeout: 5000 });
      await page.locator('#setup-close').click();
    } catch (error) { if (await page.locator('#setup-dialog').isVisible()) throw error; }
    page.on('pageerror', error => report.errors.push(error.message));
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1280, 900));
    // A visible compositor is needed for fresh dialog frames on Windows.
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].showInactive());
    await page.waitForFunction(() => document.getElementById('connection-manager')?.textContent === '연결됨');
    await page.locator('#work-list button').filter({ hasText: '문맥 효율 개선 계획 적용' }).click();
    await page.waitForFunction(() => document.getElementById('work-title')?.textContent === '문맥 효율 개선 계획 적용');
    await page.locator('#check-connection').click();
    await page.waitForFunction(() => document.getElementById('connection-mcp')?.textContent === '통신 확인', null, { timeout: 30000 });
    await page.locator('#action-notice').waitFor({ state: 'visible' });
    await page.locator('#action-dismiss').click();
    await page.locator('#action-notice').waitFor({ state: 'hidden' });
    const state = await page.evaluate(() => window.jev.overview());
    report.version = await app.evaluate(({ app }) => app.getVersion());
    report.engine = { state: state.engine.state, worker_pid: state.engine.worker_pid, broker_pid: state.engine.broker_pid };
    assert.equal(state.engine.worker_pid, null, 'Screenshots must not load model weights');
    await page.evaluate(() => document.fonts.ready);
    async function capture(filename) {
      await page.waitForTimeout(350);
      const data = await app.evaluate(async ({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0];
        const image = await window.capturePage();
        return { png: image.toPNG().toString('base64'), size: image.getSize() };
      });
      const png = Buffer.from(data.png, 'base64');
      fs.writeFileSync(path.join(output, filename), png);
      report.images.push({ filename, ...data.size, sha256: crypto.createHash('sha256').update(png).digest('hex') });
    }
    await capture('desktop-overview.png');
    await page.locator('#command-open').click();
    await page.locator('#command-dialog').waitFor({ state: 'visible' });
    await capture('desktop-commands.png');
    assert.deepEqual(report.errors, []);
    fs.writeFileSync(path.join(scratch, 'capture.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally {
    if (app) await app.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
