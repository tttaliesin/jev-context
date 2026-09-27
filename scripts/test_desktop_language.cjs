'use strict';
// Real packaged app, Python service and isolated project. Native file pickers are substituted.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local/language-validation', String(Date.now()));
const project = path.join(output, 'language-project');
const config = path.join(project, '.local/project.toml');
const userData = path.join(output, 'user-data');
fs.mkdirSync(project, { recursive: true });
const report = { output, stages: [], pageErrors: [], modelRequested: false, nativePicker: 'substituted' };
const python = path.join(root, '.venv/Scripts/python.exe');
const pythonOptions = { cwd: root, windowsHide: true, encoding: 'utf8', env: { ...process.env, PYTHONPATH: path.join(root, 'src') } };
let app, page, originalClipboard;
async function launch() {
  app = await _electron.launch({ executablePath: path.join(root, 'dist/JevContext/JevContext.exe'),
    args: ['--project-config', config], cwd: root,
    env: { ...process.env, JEV_MANAGER_USER_DATA: userData, JEV_MANAGER_TEST_HIDDEN: '1' } });
  page = await app.firstWindow();
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => report.pageErrors.push(error.message));
  await page.waitForFunction(() => Boolean(window.jevI18n));
  await app.evaluate(({ BrowserWindow }) => { const win = BrowserWindow.getAllWindows()[0]; win.setContentSize(1220, 860); win.showInactive(); });
}
async function language(value, setup = true) {
  await page.locator(`${setup ? '#setup-dialog' : '.topbar'} [data-language-select]`).selectOption(value);
  await page.waitForFunction(expected => document.documentElement.lang === expected && [...document.querySelectorAll('[data-language-select]')].every(el => !el.disabled), value);
}
async function idle() { await page.waitForFunction(() => !document.getElementById('setup-next').disabled); }
async function capture(name) {
  const png = await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].capturePage()).toPNG().toString('base64'));
  fs.writeFileSync(path.join(output, name), Buffer.from(png, 'base64'));
}
async function uiKorean(selector, excluded = []) {
  return page.locator(selector).evaluate((node, exclusions) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT); const found = [];
    while (walker.nextNode()) {
      const text = walker.currentNode, parent = text.parentElement;
      if (!parent || parent.closest('option, [hidden], .sr-only') || exclusions.some(s => parent.closest(s))) continue;
      if (parent.getClientRects().length && /[가-힣]/u.test(text.textContent)) found.push(text.textContent.trim());
    }
    return found;
  }, excluded);
}
(async () => {
  try {
    await launch();
    originalClipboard = await app.evaluate(({ clipboard }) => clipboard.readText());
    await page.locator('#setup-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
    await page.locator('#setup-paths').fill('README.md\ndocs/*.md');
    await language('en');
    assert.equal(await page.locator('#setup-title').textContent(), 'Connect your project');
    assert.equal(await page.locator('#setup-paths').inputValue(), 'README.md\ndocs/*.md');
    assert.deepEqual(await uiKorean('#setup-dialog'), []);
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async (_win, options) => {
      globalThis.languageDialogTitle = options.title;
      return { canceled: false, filePaths: [selected] };
    }; }, project);
    await page.locator('#setup-folder').click(); await idle();
    assert.equal(await app.evaluate(() => globalThis.languageDialogTitle), 'Choose a project folder');
    await page.locator('#setup-next').click(); await page.locator('#setup-step-2').waitFor({ state: 'visible' }); await idle();
    const rawPreview = await page.locator('#setup-entry').textContent();
    const rawSkill = await page.locator('#setup-skill').textContent();
    await language('ko'); await language('en');
    assert.equal(await page.locator('#setup-entry').textContent(), rawPreview);
    assert.equal(await page.locator('#setup-skill').textContent(), rawSkill);
    fs.mkdirSync(path.join(project, '.codex'), { recursive: true });
    fs.writeFileSync(path.join(project, '.codex/config.toml'), '# preserve me\n');
    await page.locator('#setup-next').click();
    await page.locator('#setup-error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#setup-error').textContent(), 'Settings changed. Click Refresh preview.');
    await language('ko');
    assert.match(await page.locator('#setup-error').textContent(), /다시 보기/);
    await language('en');
    await page.locator('#setup-preview').click(); await idle();
    await page.locator('#setup-next').click(); await page.locator('#setup-step-3').waitFor({ state: 'visible' }); await idle();
    await page.locator('#setup-check').click(); await idle();
    assert.equal(await page.locator('#setup-transport').textContent(), 'Transport verified');
    await page.locator('#setup-challenge').click(); await idle();
    const englishPrompt = await page.locator('#setup-prompt').inputValue();
    assert.ok(englishPrompt.startsWith('Call this project'));
    await page.locator('#setup-copy').click(); await idle();
    assert.equal(await app.evaluate(({ clipboard }) => clipboard.readText()), englishPrompt);
    await language('ko');
    const koreanPrompt = await page.locator('#setup-prompt').inputValue();
    assert.equal(koreanPrompt.split('\n')[1], englishPrompt.split('\n')[1]);
    await page.locator('#setup-copy').click(); await idle();
    assert.equal(await app.evaluate(({ clipboard }) => clipboard.readText()), koreanPrompt);
    await language('en');
    assert.equal(await page.locator('#setup-prompt').inputValue(), englishPrompt);
    assert.deepEqual(await uiKorean('#setup-dialog'), []);
    await capture('desktop-onboarding-en.png');
    await app.evaluate(({ BrowserWindow }) => { const win = BrowserWindow.getAllWindows()[0]; win.setContentSize(940, 690); win.webContents.setZoomFactor(1.25); });
    await page.waitForFunction(() => innerWidth === 752);
    const bounds = await page.evaluate(() => {
      const dialog = document.getElementById('setup-dialog').getBoundingClientRect(), footer = document.getElementById('setup-next').getBoundingClientRect();
      return { right: dialog.right, bottom: dialog.bottom, footer: footer.bottom, width: innerWidth, height: innerHeight };
    });
    assert.ok(bounds.right <= bounds.width + 1 && bounds.bottom <= bounds.height + 1 && bounds.footer <= bounds.height + 1);
    await capture('minimum-english.png');
    report.stages.push({ name: 'setup-round-trip', sameNonce: true, originalPreviews: true, bounds });
    await page.locator('#setup-next').click(); await page.locator('#setup-step-4').waitFor({ state: 'visible' }); await idle();
    assert.deepEqual(await uiKorean('#setup-dialog'), []);
    await page.locator('#setup-next').click(); await page.locator('#setup-dialog').waitFor({ state: 'hidden' });
    // Seed a real work record through the public CLI in the isolated project.
    const input = { contract_version: '2.0', request_id: 'language-test-create', mutation_id: 'language-test-create',
      create: { title: '작업 기록', goal: '설정 원문을 보존합니다.', origin: { quote: '언어 테스트' },
        scope: { mode: 'implement', allowed_actions: ['read'], constraints: ['경로 C:\\한글 폴더 유지'] } } };
    const inputPath = path.join(output, 'work.json'); fs.writeFileSync(inputPath, JSON.stringify(input));
    execFileSync(python, ['-X', 'utf8', '-m', 'jev_context', 'call', '--config', config, '--tool', 'work_open', '--input', inputPath], pythonOptions);
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.getElementById('work-title').textContent === '작업 기록');
    const before = await page.evaluate(() => ({ title: document.getElementById('work-title').textContent, goal: document.getElementById('work-goal').textContent, constraints: document.getElementById('work-constraints').textContent }));
    await page.locator('#work-filter').fill('작업');
    await language('ko', false); await language('en', false);
    assert.deepEqual(await page.evaluate(() => ({ title: document.getElementById('work-title').textContent, goal: document.getElementById('work-goal').textContent, constraints: document.getElementById('work-constraints').textContent })), before);
    assert.equal(await page.locator('#work-filter').inputValue(), '작업');
    assert.match(await page.locator('#work-meta').textContent(), /^Updated /);
    assert.doesNotMatch(await page.locator('#work-meta').textContent(), /[가-힣]/u);
    await page.locator('#command-open').click();
    await page.locator('#command-input').fill('refresh');
    assert.match(await page.locator('#command-results').textContent(), /Refresh project status/);
    await page.keyboard.press('Escape');
    await page.locator('#shortcuts-open').click();
    assert.deepEqual(await uiKorean('#shortcuts-dialog'), []);
    await page.keyboard.press('Escape');
    const badLanguage = await page.evaluate(async () => { try { await window.jev.setLanguage('fr'); return false; } catch { return true; } });
    assert.equal(badLanguage, true);
    const overview = await page.evaluate(() => window.jev.overview());
    assert.equal(overview.engine.worker_pid, null); assert.equal(overview.engine.broker_pid, null); assert.equal(overview.sources.count, 0);
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] }); }, config);
    await page.locator('#select-project').click();
    await page.waitForFunction(() => !document.getElementById('select-project').disabled);
    const settings = JSON.parse(fs.readFileSync(path.join(userData, 'settings.json'), 'utf8'));
    assert.equal(settings.language, 'en'); assert.equal(settings.configPath, config); assert.ok(settings.pythonPath);
    // A failed preference write must not claim the language was saved or switch the UI.
    const pending = path.join(userData, 'settings.json.pending');
    fs.mkdirSync(pending);
    try {
      await page.locator('.topbar [data-language-select]').selectOption('ko');
      await page.waitForFunction(() => document.querySelector('main [data-language-error]').textContent.length > 0);
      assert.equal(await page.locator('html').getAttribute('lang'), 'en');
      assert.equal(await page.locator('.topbar [data-language-select]').inputValue(), 'en');
      assert.equal(JSON.parse(fs.readFileSync(path.join(userData, 'settings.json'), 'utf8')).language, 'en');
    } finally { fs.rmdirSync(pending); }
    await language('en', false);
    await capture('desktop-work-en.png');
    report.stages.push({ name: 'dashboard', originalWorkPreserved: true, commands: true, shortcuts: true, noModelOrCollection: true });
    // The support flow uses the real preload IPC, main process and Python bridge.
    const savedBeforeReconnect = fs.readFileSync(path.join(userData, 'settings.json'), 'utf8');
    await page.locator('#support-open').click();
    await page.waitForFunction(() => !document.getElementById('support-copy').disabled);
    assert.equal(await page.locator('#support-title').textContent(), 'Troubleshoot');
    const diagnosticPreview = await page.locator('#support-report').textContent();
    assert.equal(JSON.parse(diagnosticPreview).app.version, '0.7.0');
    for (const privateValue of [project, config, userData, before.title, before.goal, '한글 폴더']) {
      assert.ok(!diagnosticPreview.includes(privateValue), `Private content in diagnostic report: ${privateValue}`);
    }
    await page.locator('#support-copy').click();
    assert.equal(await app.evaluate(({ clipboard }) => clipboard.readText()), diagnosticPreview);
    await page.locator('#support-dialog [data-language-select]').selectOption('ko');
    await page.waitForFunction(() => document.documentElement.lang === 'ko');
    assert.equal(await page.locator('#support-report').textContent(), diagnosticPreview);
    await page.locator('#support-dialog [data-language-select]').selectOption('en');
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    fs.mkdirSync(pending);
    try {
      await page.locator('#support-reconnect').click();
      await page.waitForFunction(() => !document.getElementById('support-reconnect').disabled && !document.getElementById('support-copy').disabled);
      assert.equal(await page.locator('#support-error').isVisible(), false);
      assert.equal(fs.readFileSync(path.join(userData, 'settings.json'), 'utf8'), savedBeforeReconnect);
      await page.locator('#support-dialog [data-language-select]').selectOption('ko');
      await page.waitForFunction(() => document.querySelector('#support-dialog [data-language-error]').textContent.length > 0);
      assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    } finally { fs.rmdirSync(pending); }
    await page.locator('#support-dialog [data-language-select]').selectOption('en');
    await page.waitForFunction(() => !document.querySelector('#support-dialog [data-language-select]').disabled);
    await page.locator('#support-dialog .setup-details summary').click();
    await capture('desktop-support-en.png');
    assert.deepEqual(await uiKorean('#support-dialog'), []);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#support-open').evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator('#work-filter').inputValue(), '작업');
    assert.equal(await page.locator('#work-title').textContent(), before.title);
    const reconnected = await page.evaluate(() => window.jev.overview());
    assert.equal(reconnected.engine.worker_pid, null); assert.equal(reconnected.engine.broker_pid, null);
    assert.equal(reconnected.sources.count, 0);
    report.stages.push({ name: 'support', exactClipboard: true, privateContentExcluded: true,
      reconnectWithoutSettingsWrite: true, preservedWorkAndFilter: true, noModelOrCollection: true });
    await app.close(); app = null; await launch();
    await page.waitForFunction(() => document.documentElement.lang === 'en' && document.getElementById('work-title').textContent === '작업 기록');
    assert.equal(await page.locator('#work-filter').inputValue(), '작업');
    assert.equal(await page.locator('#model-heading').textContent(), 'Local model');
    await language('ko', false); await app.close(); app = null; await launch();
    await page.waitForFunction(() => document.documentElement.lang === 'ko' && document.getElementById('work-title').textContent === '작업 기록');
    assert.equal(await page.locator('#model-heading').textContent(), '로컬 모델');
    report.stages.push({ name: 'restart', englishPersisted: true, koreanPersisted: true });
    assert.deepEqual(report.pageErrors, []); report.passed = true;
  } catch (error) { report.passed = false; report.error = error.stack; process.exitCode = 1; }
  finally {
    if (app) { if (originalClipboard !== undefined) await app.evaluate(({ clipboard }, value) => clipboard.writeText(value), originalClipboard); await app.close(); }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  }
})();
