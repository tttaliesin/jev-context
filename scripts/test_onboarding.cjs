'use strict';
// Real packaged app and filesystem. Only the native folder picker is substituted.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local', 'onboarding-validation', String(Date.now()));
const project = path.join(output, 'sample-project');
fs.mkdirSync(project, { recursive: true });
const config = path.join(project, '.local', 'project.toml');
const report = { output, project, observations: [], errors: [], captures: [], folderPicker: 'test substitution', modelRequested: false };
(async () => {
  let app;
  let originalClipboard;
  try {
    app = await _electron.launch({ executablePath: path.join(root, 'dist/JevContext/JevContext.exe'),
      args: ['--project-config', config, '--force-device-scale-factor=1'], cwd: root,
      env: { ...process.env, JEV_MANAGER_USER_DATA: path.join(output, 'user-data'), JEV_MANAGER_TEST_HIDDEN: '1' } });
    const page = await app.firstWindow();
    originalClipboard = await app.evaluate(({ clipboard }) => clipboard.readText());
    page.on('pageerror', error => report.errors.push(error.message));
    await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0]; win.setContentSize(1220, 860); win.showInactive();
    });
    await page.locator('#setup-dialog').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-folder').disabled);
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] }); }, project);
    await page.locator('#setup-folder').click();
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    assert.equal(fs.existsSync(config), false, 'Folder selection alone must not create configuration');
    await app.evaluate(({ dialog }) => { dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] }); });
    await page.locator('#setup-existing').click();
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    assert.equal(await page.locator('#setup-project').textContent(), project, 'Cancel must keep the selected folder');
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] }); }, project);
    await page.locator('#setup-existing').click();
    await page.locator('#setup-error').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    assert.equal(await page.locator('#setup-project').textContent(), project, 'A failed file chooser must preserve setup inputs');
    async function capture(name) {
      await page.waitForTimeout(250);
      const png = await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].capturePage()).toPNG().toString('base64'));
      fs.writeFileSync(path.join(output, name), Buffer.from(png, 'base64')); report.captures.push(name);
    }
    await capture('01-project.png');
    await page.locator('#setup-next').click();
    await page.locator('#setup-step-2').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    assert.equal(fs.existsSync(config), true);
    assert.equal(fs.existsSync(path.join(project, '.codex/config.toml')), false);
    await capture('02-preview.png');
    // A file changed after preview must not be silently overwritten.
    fs.mkdirSync(path.join(project, '.codex'), { recursive: true });
    fs.writeFileSync(path.join(project, '.codex/config.toml'), '# independent edit\n');
    await page.locator('#setup-next').click();
    await page.locator('#setup-error').waitFor({ state: 'visible' });
    assert.ok((await page.locator('#setup-error').textContent()).includes('다시 보기'));
    await page.locator('#setup-preview').click();
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    // Reject a renderer attempting to choose arbitrary write destinations.
    const rejected = await page.evaluate(async () => {
      try { await window.jev.setup('install', { fingerprint: 'x', config_path: 'C:/arbitrary' }); return false; }
      catch { return true; }
    });
    assert.ok(rejected);
    await page.locator('#setup-next').click();
    await page.locator('#setup-step-3').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-check').disabled);
    assert.ok(fs.existsSync(path.join(project, '.codex/config.toml')));
    assert.ok(fs.existsSync(path.join(project, '.agents/skills/jev-context/SKILL.md')));
    await page.locator('#setup-check').click();
    await page.waitForFunction(() => document.getElementById('setup-transport').textContent === '통신 확인');
    await page.waitForFunction(() => !document.getElementById('setup-challenge').disabled);
    await page.locator('#setup-challenge').click();
    await page.waitForFunction(() => !document.getElementById('setup-copy').disabled);
    await page.locator('#setup-copy').click();
    await page.waitForFunction(() => !document.getElementById('setup-copy').disabled);
    const copied = await app.evaluate(({ clipboard }) => clipboard.readText());
    assert.ok(copied.includes('jev-connect-'));
    assert.ok((await page.locator('#setup-receipt').textContent()).includes('기다리는 중'));
    await capture('03-verify.png');
    const overview = await page.evaluate(() => window.jev.overview());
    assert.equal(overview.engine.worker_pid, null); assert.equal(overview.engine.broker_pid, null);
    report.observations.push({ stage: 'installed-and-transport-checked', engine: overview.engine.state, sourceCount: overview.sources.count });
    await page.keyboard.press('Control+k');
    assert.equal(await page.locator('#command-dialog').isVisible(), false);
    await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0]; win.setContentSize(940, 690); win.webContents.setZoomFactor(1.25);
    });
    await page.waitForTimeout(450);
    const bounds = await page.evaluate(() => {
      const box = document.getElementById('setup-dialog').getBoundingClientRect();
      const footer = document.getElementById('setup-next').getBoundingClientRect();
      return { width: innerWidth, height: innerHeight, right: box.right, bottom: box.bottom, footerBottom: footer.bottom };
    });
    assert.ok(bounds.right <= bounds.width + 1 && bounds.bottom <= bounds.height + 1 && bounds.footerBottom <= bounds.height + 1);
    report.observations.push({ stage: 'minimum-window-125-percent', ...bounds });
    await capture('04-minimum-125.png');
    await page.locator('#setup-next').click();
    await page.locator('#setup-step-4').waitFor({ state: 'visible' });
    assert.ok((await page.locator('#setup-result').textContent()).includes('아직 확인되지 않았습니다'));
    await page.locator('#setup-next').click();
    await page.locator('#setup-dialog').waitFor({ state: 'hidden' });
    await page.locator('#setup-open').click();
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    await page.locator('#setup-next').click();
    await page.locator('#setup-step-2').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    await page.locator('#setup-next').click();
    await page.locator('#setup-step-3').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    const pending = await page.evaluate(() => window.jev.setup('status'));
    const probe = `import anyio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
async def run():
    params = StdioServerParameters(command=sys.executable, args=['-m','jev_context','serve','--config',sys.argv[1]])
    async with stdio_client(params) as streams, ClientSession(*streams, client_info=Implementation(name='codex-ui-test',version='test')) as session:
        await session.initialize()
        result = await session.call_tool('workspace_status', {'contract_version':'2.0','request_id':sys.argv[2]})
        assert result.structuredContent['outcome'] == 'ok'
anyio.run(run)`;
    execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-X', 'utf8', '-c', probe, config, pending.confirmation.request_id],
      { cwd: root, windowsHide: true, env: { ...process.env, PYTHONPATH: path.join(root, 'src') } });
    await page.waitForFunction(() => document.getElementById('setup-receipt').textContent.includes('요청 수신'));
    report.observations.push({ stage: 'challenge-received', client: 'codex-ui-test', actualCodexHost: false });
    await page.locator('#setup-restore').click();
    await page.locator('#setup-restore').click();
    await page.waitForFunction(() => !document.getElementById('setup-next').disabled);
    assert.equal(fs.readFileSync(path.join(project, '.codex/config.toml'), 'utf8'), '# independent edit\n');
    assert.equal(fs.existsSync(path.join(project, '.agents/skills/jev-context/SKILL.md')), false);
    assert.ok(fs.existsSync(config));
    report.observations.push({ stage: 'restored', projectRetained: true });
    assert.deepEqual(report.errors, []); report.passed = true;
  } catch (error) { report.passed = false; report.error = error.stack; process.exitCode = 1; }
  finally {
    if (app) {
      if (originalClipboard !== undefined) await app.evaluate(({ clipboard }, original) => clipboard.writeText(original), originalClipboard);
      await app.close();
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  }
})();
