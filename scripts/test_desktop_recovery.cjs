'use strict';

const { _electron } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { BridgeClient } = require('../desktop/bridge-client.cjs');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local', 'recovery-validation', String(Date.now()));
const pythonPath = path.join(root, '.venv/Scripts/python.exe');
fs.mkdirSync(output, { recursive: true });
const projects = ['project-a', 'project-b'].map(name => {
  const project = path.join(output, name);
  fs.mkdirSync(path.join(project, '.local'), { recursive: true });
  const config = path.join(project, '.local/project.toml');
  execFileSync(pythonPath, ['-X', 'utf8', '-c',
    'import sys; from jev_context.cli import write_config; from jev_context.onboarding import initialize_existing; write_config(sys.argv[1],sys.argv[2],sys.argv[3],[]); initialize_existing(sys.argv[1])',
    config, project, path.join(project, '.local/data')], { cwd: root, windowsHide: true });
  return { project, config };
});
const report = { output, pageErrors: [], scenarios: [] };

async function open(userData, config) {
  const app = await _electron.launch({ executablePath: path.join(root, 'dist/JevContext/JevContext.exe'),
    args: config ? ['--project-config', config] : [],
    env: { ...process.env, JEV_MANAGER_USER_DATA: userData, JEV_MANAGER_TEST_HIDDEN: '1' } });
  return app;
}

async function dashboard(app, project) {
  const page = await app.firstWindow();
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => report.pageErrors.push(error.message));
  await page.locator('#setup-dialog').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.getElementById('setup-close').disabled);
  await page.locator('#setup-close').click();
  await page.waitForFunction(name => document.getElementById('project-name').textContent === name, project);
  return page;
}

(async () => {
  for (const route of ['project', 'onboarding']) {
    const userData = path.join(output, route, 'user-data');
    let app;
    try {
      app = await open(userData, projects[0].config);
      const page = await dashboard(app, 'project-a');
      await page.evaluate(() => window.jev.setLanguage('ko'));
      const savedPath = path.join(userData, 'settings.json');
      const before = fs.readFileSync(savedPath, 'utf8');
      const pending = `${savedPath}.pending`;
      fs.mkdirSync(pending); // A real filesystem write failure in isolated userData.
      await app.evaluate(({ dialog }, selected) => {
        dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] });
      }, route === 'project' ? projects[1].config : projects[1].project);
      if (route === 'onboarding') {
        await page.evaluate(() => window.jev.setup('begin'));
        await page.evaluate(() => window.jev.setup('folder'));
      }
      const switchProject = () => page.evaluate(async selectedRoute => {
        try {
          if (selectedRoute === 'project') await window.jev.selectProject();
          else await window.jev.setup('create', { allowed_paths: [] });
          return { ok: true };
        } catch (error) { return { ok: false, message: error.message }; }
      }, route);
      const failure = await switchProject();
      assert.equal(failure.ok, false);
      assert.match(failure.message, /EISDIR|EPERM|EACCES/);
      const afterFailure = await page.evaluate(() => window.jev.overview());
      assert.equal(afterFailure.project.project_root, projects[0].project);
      assert.equal(fs.readFileSync(savedPath, 'utf8'), before);
      // Refresh the actual renderer too; it must still display A after the failure.
      await page.locator('#refresh').click();
      await page.waitForFunction(() => !document.getElementById('refresh').disabled);
      assert.equal(await page.locator('#project-name').textContent(), 'project-a');
      await page.screenshot({ path: path.join(output, `${route}-preserved.png`) });
      // Remove only the empty test blocker, then retry through the same IPC path.
      fs.rmdirSync(pending);
      assert.deepEqual(await switchProject(), { ok: true });
      const afterRecovery = await page.evaluate(() => window.jev.overview());
      assert.equal(afterRecovery.project.project_root, projects[1].project);
      assert.equal(JSON.parse(fs.readFileSync(savedPath, 'utf8')).configPath, projects[1].config);
      await app.close(); app = null;
      app = await open(userData);
      await dashboard(app, 'project-b');
      report.scenarios.push({ route, failure: failure.message, preserved: true, recovered: true, restart: true });
    } finally { if (app) await app.close(); }
  }
  const settings = { projectRoot: root, configPath: projects[0].config, pythonPath: path.join(output, 'missing-python.exe') };
  const bridge = new BridgeClient(settings);
  try {
    await assert.rejects(bridge.request('overview'), /ENOENT/);
    settings.pythonPath = pythonPath;
    const result = await bridge.request('overview');
    assert.equal(result.project.project_root, projects[0].project);
    report.pythonRecovery = true;
  } finally { bridge.close(); }
  assert.deepEqual(report.pageErrors, []);
  report.passed = true;
})().catch(error => { report.error = error.stack; process.exitCode = 1; }).finally(() => {
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
