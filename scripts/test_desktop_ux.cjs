'use strict';
// Actual packaged Electron round trips. No fixture API, model actions, or database writes.
const { _electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const argument = process.argv.indexOf('--output-dir');
const output = argument >= 0 && process.argv[argument + 1]
  ? path.resolve(root, process.argv[argument + 1]) : path.join(root, '.local', 'desktop-ux-20260926');
const executablePath = path.join(root, 'dist', 'JevContext', 'JevContext.exe');
const roundtripData = path.join(output, 'roundtrip-user-data');
const maximizedData = path.join(output, 'maximized-user-data');
const cwd = path.join(output, 'cwd');
fs.mkdirSync(cwd, { recursive: true });
const reportPath = path.join(output, 'ux-roundtrip-report.json');
const report = { kind: 'actual-packaged-electron', fixture: false, executablePath,
  startedAt: new Date().toISOString(), modelActionsRequested: [], desktop_current_session_verified: false,
  userData: { roundtrip: roundtripData, maximized: maximizedData }, stages: [], snapshots: [],
  captures: [], pageErrors: [], boundsToleranceDIP: 2 };
const save = () => fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let application;
let page;

async function stage(name, action) {
  const entry = { name, startedAt: new Date().toISOString(), status: 'running' };
  report.stages.push(entry); save();
  try { const result = await action(); entry.status = 'passed'; return result; }
  catch (error) { entry.status = 'failed'; entry.error = error.stack; throw error; }
  finally { entry.finishedAt = new Date().toISOString(); save(); }
}

async function nativeState() {
  return application.evaluate(({ BrowserWindow, screen }) => {
    const window = BrowserWindow.getAllWindows()[0];
    return { bounds: window.getBounds(), normalBounds: window.getNormalBounds(),
      contentBounds: window.getContentBounds(), minimized: window.isMinimized(),
      maximized: window.isMaximized(), visible: window.isVisible(),
      workArea: screen.getDisplayMatching(window.getNormalBounds()).workArea };
  });
}

async function settledNative(predicate = () => true) {
  let previous;
  let repeated = 0;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const current = await nativeState();
    const serialized = JSON.stringify(current);
    repeated = predicate(current) && serialized === previous ? repeated + 1 : 0;
    if (repeated >= 2) return current;
    previous = serialized;
    await delay(100);
  }
  throw new Error(`Native window did not settle: ${previous}`);
}

function nearBounds(actual, expected, label) {
  for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(Math.abs(actual[key] - expected[key]) <= 2,
      `${label}.${key}: actual ${actual[key]}, expected ${expected[key]} (DIP tolerance 2)`);
  }
}

function inside(bounds, area) {
  assert.ok(bounds.x >= area.x - 2 && bounds.y >= area.y - 2
    && bounds.x + bounds.width <= area.x + area.width + 2
    && bounds.y + bounds.height <= area.y + area.height + 2,
  `Normal bounds must fit in the work area: ${JSON.stringify({ bounds, area })}`);
}

async function readOnlySnapshot(label) {
  const snapshot = await page.evaluate(() => window.jev.overview());
  report.snapshots.push({ stage: label, snapshot }); save();
  assert.equal(snapshot.project?.project_root?.toLowerCase(), root.toLowerCase());
  assert.equal(snapshot.engine.worker_pid, null, `${label}: reading must not start a model worker`);
  assert.equal(snapshot.engine.broker_pid, null, `${label}: reading must not start a model broker`);
  assert.ok(!snapshot.workspace_error, `${label}: workspace must be readable`);
  return snapshot;
}

async function launch(userData, hidden) {
  const env = { ...process.env, JEV_MANAGER_USER_DATA: userData };
  if (hidden) env.JEV_MANAGER_TEST_HIDDEN = '1';
  else delete env.JEV_MANAGER_TEST_HIDDEN;
  application = await _electron.launch({ executablePath,
    args: ['--project-config', path.join(root, '.local', 'project.toml')], cwd, env, timeout: 30000 });
  page = await application.firstWindow();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => { report.pageErrors.push(error.message); save(); });
  await page.waitForFunction(() => document.getElementById('connection-manager')?.textContent === '연결됨'
    && document.getElementById('work-panel')?.getAttribute('aria-busy') === 'false', null, { timeout: 25000 });
  await page.waitForFunction(() => {
    const mark = document.querySelector('img.brand-mark');
    return mark?.complete && mark.naturalWidth > 0 && mark.naturalHeight > 0;
  });
  const isolation = await page.evaluate(() => ({ require: typeof window.require, process: typeof window.process,
    mark: { src: document.querySelector('img.brand-mark').currentSrc,
      width: document.querySelector('img.brand-mark').naturalWidth,
      height: document.querySelector('img.brand-mark').naturalHeight } }));
  assert.equal(isolation.require, 'undefined');
  assert.equal(isolation.process, 'undefined');
  assert.equal(isolation.mark.src, 'jev://app/jev-mark.svg');
  const native = await settledNative(value => hidden ? !value.visible : value.visible);
  return { isolation, native };
}

async function close() {
  if (!application) return;
  const closing = application;
  // ElectronApplication.close calls app.quit, allowing BrowserWindow's normal close save.
  await closing.close();
  application = null;
  page = null;
}

async function capture(filename) {
  const wasVisible = await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible());
  try {
    if (!wasVisible) await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
    await page.waitForFunction(() => !document.hidden);
    await page.evaluate(() => new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Visible capture frame did not paint')), 5000);
      requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(timeout); resolve(); }));
    }));
    await settledNative(value => value.visible);
    const dom = await page.evaluate(() => ({
      sidebarClass: document.getElementById('app-shell').className,
      sidebarCollapsed: document.getElementById('app-shell').classList.contains('sidebar-collapsed'),
      sidebarWidth: document.getElementById('sidebar').getBoundingClientRect().width,
      dialogOpen: document.getElementById('shortcuts-dialog').open,
      commandDialogOpen: document.getElementById('command-dialog').open,
      viewportWidth: innerWidth, viewportHeight: innerHeight, documentHidden: document.hidden,
    }));
    const evidence = { filename, method: 'BrowserWindow.capturePage', wasVisible,
      paintSynchronization: 'visible-double-requestAnimationFrame', dom };
    report.captures.push(evidence); save();
    const image = await application.evaluate(async ({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      const captured = await window.capturePage();
      return { base64: captured.toPNG().toString('base64'), imageSize: captured.getSize(),
        contentBounds: window.getContentBounds(), zoomFactor: window.webContents.getZoomFactor() };
    });
    const png = Buffer.from(image.base64, 'base64');
    fs.writeFileSync(path.join(output, filename), png);
    Object.assign(evidence, { imageSize: image.imageSize, contentBounds: image.contentBounds,
      zoomFactor: image.zoomFactor,
      pngPixels: { width: png.readUInt32BE(16), height: png.readUInt32BE(20) } });
    save();
  } finally {
    if (!wasVisible) await application.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      if (window && !window.isDestroyed()) window.hide();
    });
  }
}

async function collapsed(expected) {
  await page.waitForFunction(value => document.getElementById('app-shell')
    .classList.contains('sidebar-collapsed') === value, expected);
  assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'), String(!expected));
  if (expected) {
    await page.waitForFunction(() => document.getElementById('sidebar').getBoundingClientRect().width <= 1);
    assert.equal(await page.locator('#work-filter').isVisible(), false,
      'A collapsed sidebar must release its full width and hide its controls');
  }
}

async function sidebarWidth(expected) {
  await page.waitForFunction(width => Math.abs(document.getElementById('sidebar')
    .getBoundingClientRect().width - width) <= 1, expected);
  const actual = await page.locator('#sidebar').evaluate(element => element.getBoundingClientRect().width);
  assert.ok(Math.abs(actual - expected) <= 1, `Sidebar width: expected=${expected}, actual=${actual}`);
}

async function chooseBounds() {
  const initial = await nativeState();
  const target = await application.evaluate(({ BrowserWindow, screen }) => {
    const window = BrowserWindow.getAllWindows()[0];
    const previous = window.getNormalBounds();
    const area = screen.getDisplayMatching(previous).workArea;
    const [minimumWidth, minimumHeight] = window.getMinimumSize();
    const width = Math.min(area.width, Math.max(minimumWidth, previous.width === 1040 ? 1080 : 1040));
    const height = Math.min(area.height, Math.max(minimumHeight, previous.height === 740 ? 760 : 740));
    const bounds = { x: area.x + Math.min(previous.x === area.x + 37 ? 61 : 37, area.width - width),
      y: area.y + Math.min(previous.y === area.y + 29 ? 47 : 29, area.height - height), width, height };
    window.setBounds(bounds);
    return bounds;
  });
  const actual = await settledNative(value => !value.maximized && !value.minimized);
  nearBounds(actual.normalBounds, target, 'set normal bounds');
  inside(actual.normalBounds, actual.workArea);
  assert.notDeepEqual(actual.normalBounds, initial.normalBounds,
    'The round trip must use bounds different from the initial/default window');
  return { initial, target, actual };
}

async function workMatches(id) {
  const work = await page.evaluate(workId => window.jev.openWork(workId), id);
  assert.equal(work.work_id, id);
  await page.waitForFunction(expected => !document.getElementById('work-detail').hidden
    && document.getElementById('work-panel').getAttribute('aria-busy') === 'false'
    && document.getElementById('work-title').textContent === expected.title
    && document.getElementById('work-goal').textContent === expected.goal,
  { title: work.title || '제목 없는 작업', goal: work.goal || '등록된 목표가 없습니다.' });
  assert.equal(await page.locator('#work-revision').textContent(), `기록 r${work.revision}`);
  return { work_id: work.work_id, revision: work.revision, title: work.title, goal: work.goal };
}

(async () => {
  try {
    let selectedId;
    let query;
    let savedBounds;
    let preference;
    let preferredWidth;
    let firstWorkId;
    await stage('first-hidden-launch-read-isolation-svg', async () => {
      report.firstLaunch = await launch(roundtripData, true);
      const snapshot = await readOnlySnapshot('first-hidden-launch');
      const choices = snapshot.works.items.filter(work => work.work_id && !work.redacted && work.title?.trim());
      assert.ok(choices.length >= 2, 'Need two readable records to distinguish restored selection from the default first record');
      selectedId = choices[1].work_id;
      firstWorkId = choices[0].work_id;
      query = Array.from(choices[1].title.trim()).slice(0, 8).join('');
      report.selectedId = selectedId;
      report.query = query;
    });
    await stage('sidebar-shortcuts-record-query-preferences', async () => {
      await page.keyboard.press('Control+f');
      await collapsed(false);
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('Escape');
      await page.locator(`#work-list button[data-work-id="${firstWorkId}"]`).click();
      await workMatches(firstWorkId);
      await page.locator('#sidebar-resize').focus();
      await page.keyboard.press('Home');
      await sidebarWidth(220);
      await page.keyboard.press('ArrowRight');
      await page.keyboard.press('ArrowRight');
      preferredWidth = 252;
      await sidebarWidth(preferredWidth);
      report.preferredSidebarWidth = preferredWidth;
      await page.locator('#sidebar-toggle').click();
      await collapsed(true);
      await capture('manager-collapsed.png');
      await page.keyboard.press('Control+k');
      await page.waitForFunction(() => document.getElementById('command-dialog').open);
      assert.equal(await page.locator('#command-input').evaluate(element => element === document.activeElement), true);
      await collapsed(true);
      assert.match(await page.locator('#command-scope').textContent(), /불러온/);
      await capture('manager-commands-collapsed.png');
      await page.locator(`#command-results [role="option"][data-command-id="work:${selectedId}"]`).click();
      await page.waitForFunction(() => !document.getElementById('command-dialog').open);
      report.selectedWork = await workMatches(selectedId);
      assert.equal(await page.locator('#location-work').textContent(), report.selectedWork.title);
      await collapsed(true);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarCollapsed), true,
        'Choosing an existing record from the palette must preserve the collapsed preference');
      await page.keyboard.press('Control+f');
      await collapsed(false);
      await sidebarWidth(preferredWidth);
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      await page.locator('#work-filter').fill(query);
      assert.match(await page.locator('#search-scope').textContent(), /불러온/);
      assert.equal(await page.locator(`#work-list button[data-work-id="${selectedId}"]`).getAttribute('aria-current'), 'true');
      // Ctrl+B deliberately acts outside text editing; the detail pane is keyboard focusable.
      await page.locator('#work-panel').focus();
      await page.keyboard.press('Control+b');
      await collapsed(true);
      await page.keyboard.press('Control+b');
      await collapsed(false);
      await page.locator('#work-panel').focus();
      await page.keyboard.press('F1');
      await page.waitForFunction(() => document.getElementById('shortcuts-dialog').open);
      assert.equal(await page.locator('#shortcuts-close').evaluate(element => element === document.activeElement), true);
      await capture('manager-shortcuts.png');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('shortcuts-dialog').open);
      assert.equal(await page.locator('#work-panel').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('Control+b');
      await collapsed(true);
      preference = await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')));
      assert.equal(preference.sidebarCollapsed, true);
      assert.equal(preference.sidebarWidth, preferredWidth);
      const entry = Object.entries(preference.projects).find(([, value]) => value.selectedId === selectedId);
      assert.ok(entry, 'Selected work must be stored under a project preference');
      assert.equal(entry[1].query, query);
      assert.deepEqual(Object.keys(entry[1]).sort(), ['query', 'selectedId'], 'Persist references, not stale record content');
      report.preferencesBeforeClose = preference;
    });
    await stage('set-native-bounds-normal-close', async () => {
      report.boundsChange = await chooseBounds();
      savedBounds = report.boundsChange.actual.normalBounds;
      await readOnlySnapshot('before-first-close');
      await close();
      const record = JSON.parse(fs.readFileSync(path.join(roundtripData, 'window-state.json'), 'utf8'));
      nearBounds(record.normalBounds, savedBounds, 'saved normal bounds');
      assert.equal(record.maximized, false);
      report.savedWindowState = record;
    });
    await stage('second-hidden-launch-restores-real-record-and-ui', async () => {
      report.secondLaunch = await launch(roundtripData, true);
      nearBounds(report.secondLaunch.native.normalBounds, savedBounds, 'reopened normal bounds');
      await readOnlySnapshot('second-hidden-launch');
      await collapsed(true);
      assert.equal(await page.locator('#work-filter').inputValue(), query);
      assert.equal(await page.locator(`#work-list button[data-work-id="${selectedId}"]`).getAttribute('aria-current'), 'true');
      // Fresh openWork is read-only; compare the automatically restored UI with current DB content.
      report.restoredWork = await workMatches(selectedId);
      assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1'))), preference);
      await capture('manager-restored.png');
      await page.locator('#sidebar-toggle').click();
      await collapsed(false);
      const expectedActualWidth = await page.evaluate(width => Math.min(width,
        Math.max(220, Math.min(400, innerWidth - 480))), preferredWidth);
      await sidebarWidth(expectedActualWidth);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarWidth), preferredWidth,
        'Relaunch must retain preferred width even if the restored native window needs a narrower layout');
      await capture('manager-width-restored.png');
      await page.locator('#sidebar-toggle').click();
      await collapsed(true);
      await close();
    });
    await stage('visible-maximized-normal-close', async () => {
      report.maximizedFirstLaunch = await launch(maximizedData, false);
      await readOnlySnapshot('visible-maximize-first-launch');
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].maximize());
      report.maximizedBeforeClose = await settledNative(value => value.maximized && value.visible);
      await close();
      const record = JSON.parse(fs.readFileSync(path.join(maximizedData, 'window-state.json'), 'utf8'));
      assert.equal(record.maximized, true);
      nearBounds(record.normalBounds, report.maximizedBeforeClose.normalBounds, 'saved maximized normal bounds');
      report.maximizedSavedWindowState = record;
    });
    await stage('visible-maximized-relaunch', async () => {
      await launch(maximizedData, false);
      report.maximizedRestored = await settledNative(value => value.maximized && value.visible);
      nearBounds(report.maximizedRestored.normalBounds, report.maximizedSavedWindowState.normalBounds,
        'reopened maximized normal bounds');
      await readOnlySnapshot('visible-maximized-relaunch');
      await close();
    });
    assert.deepEqual(report.pageErrors, []);
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.stack;
    process.exitCode = 1;
    if (application) {
      try { await capture('manager-failure.png'); } catch (captureError) { report.captureError = captureError.message; }
    }
  } finally {
    try { await close(); }
    catch (error) { report.closeError = error.stack; report.passed = false; process.exitCode = 1; }
    report.finishedAt = new Date().toISOString(); save();
    console.log(JSON.stringify({ passed: report.passed, report: reportPath, error: report.error, closeError: report.closeError }));
  }
})();
