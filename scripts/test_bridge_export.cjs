'use strict';
// Isolated real Electron export. Only the native save dialog is substituted.
const { _electron } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local/bridge-export-validation', String(Date.now()));
const project = path.join(output, 'project');
const config = path.join(output, 'project.toml');
const destination = path.join(output, '한글 connection.json');
fs.mkdirSync(project, { recursive: true });
execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-X', 'utf8', '-c',
  'from jev_context.cli import write_config; from jev_context.onboarding import initialize_existing; import sys; write_config(sys.argv[1],sys.argv[2],sys.argv[3],[]); initialize_existing(sys.argv[1])',
  config, project, path.join(output, 'data')], { cwd: root, windowsHide: true });
const report = { output, errors: [], nativeSaveDialog: 'substituted' };
(async () => {
  let app;
  try {
    app = await _electron.launch({ executablePath: path.join(root, 'dist/JevContext/JevContext.exe'), args: ['--project-config', config],
      env: { ...process.env, JEV_MANAGER_USER_DATA: path.join(output, 'user-data'), JEV_MANAGER_TEST_HIDDEN: '1' } });
    const page = await app.firstWindow(); page.setDefaultTimeout(30000);
    page.on('pageerror', error => report.errors.push(error.message));
    await page.locator('#setup-dialog').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-close').disabled);
    await page.locator('#setup-close').click();
    await page.waitForFunction(() => !document.getElementById('export-bridge').disabled);
    await page.locator('.topbar [data-language-select]').selectOption('en');
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    await page.locator('#connection-details summary').click();
    assert.equal(await page.locator('#export-bridge').textContent(), 'Export Workroom connection file');
    await app.evaluate(({ dialog }) => { dialog.showSaveDialog = async () => ({ canceled: true }); });
    await page.locator('#export-bridge').click();
    await page.waitForFunction(() => !document.getElementById('export-bridge').disabled);
    assert.equal(fs.existsSync(destination), false);
    await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async (_win, options) => { globalThis.saveTitle = options.title; return { canceled: false, filePath }; }; }, destination);
    await page.locator('#export-bridge').click();
    await page.waitForFunction(() => document.getElementById('action-message').textContent.includes('Connection file saved'));
    const bytes = fs.readFileSync(destination);
    const descriptor = JSON.parse(bytes);
    assert.equal(descriptor.contract, 'workroom-jev/1');
    assert.equal(descriptor.cwd, project);
    assert.equal(descriptor.args.at(-1), config);
    assert.ok(path.isAbsolute(descriptor.command));
    assert.deepEqual(Object.keys(descriptor).sort(), ['args', 'command', 'contract', 'cwd']);
    assert.equal(await app.evaluate(() => globalThis.saveTitle), 'Export Workroom connection file');
    assert.equal(fs.existsSync(path.join(project, '.codex')), false);
    const state = await page.evaluate(() => window.jev.overview());
    assert.equal(state.sources.count, 0); assert.equal(state.engine.worker_pid, null);
    assert.equal(fs.existsSync(path.join(path.dirname(state.project.database_path), 'workroom')), false);
    await page.locator('#export-bridge').click();
    await page.waitForFunction(() => document.getElementById('error-message').textContent.includes('already exists'));
    assert.deepEqual(fs.readFileSync(destination), bytes);
    await page.locator('.topbar [data-language-select]').selectOption('ko');
    await page.waitForFunction(() => document.documentElement.lang === 'ko');
    assert.equal(await page.locator('#export-bridge').textContent(), 'Workroom 연결 파일 내보내기');
    assert.match(await page.locator('#error-message').textContent(), /다른 이름/);
    assert.deepEqual(report.errors, []);
    report.passed = true; report.descriptor = descriptor;
  } catch (error) { report.passed = false; report.error = error.stack; process.exitCode = 1; }
  finally { if (app) await app.close(); fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2)); }
})();
