'use strict';
// Run against the packaged executable. Playwright is supplied by the development runtime.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const outputArgument = process.argv.indexOf('--output-dir');
const output = outputArgument >= 0 && process.argv[outputArgument + 1]
  ? path.resolve(root, process.argv[outputArgument + 1]) : path.join(root, '.local', 'desktop-20260926');
fs.mkdirSync(output, { recursive: true });
const withModel = process.argv.includes('--model');
const report = { withModel, startedAt: new Date().toISOString(), observations: [], consoleErrors: [],
  layouts: [], captures: [], screenshotMethod: 'BrowserWindow.capturePage' };
const reportPath = path.join(output, withModel ? 'app-model-test.json' : 'app-read-test.json');
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  let app;
  try {
    app = await _electron.launch({ executablePath: path.join(root, 'dist', 'JevContext', 'JevContext.exe'),
      args: process.argv.includes('--scale=1') ? ['--force-device-scale-factor=1'] : [],
      cwd: output,
      env: { ...process.env, JEV_MANAGER_USER_DATA: path.join(output, 'test-user-data'), JEV_MANAGER_TEST_HIDDEN: '1' },
      timeout: 30000 });
    const page = await app.firstWindow();
    async function layoutMetrics() {
      const native = await app.evaluate(({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0];
        return { windowBounds: window.getBounds(), contentBounds: window.getContentBounds(),
          zoomFactor: window.webContents.getZoomFactor() };
      });
      const dom = await page.evaluate(() => ({
        viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
        document: { width: document.documentElement.scrollWidth,
          height: document.documentElement.scrollHeight },
        controls: Object.fromEntries(['prepare-model', 'stop-model', 'refresh', 'check-connection'].map(id => {
          const element = document.getElementById(id);
          if (!element) return [id, null];
          const box = element.getBoundingClientRect();
          return [id, { x: box.x, y: box.y, right: box.right, bottom: box.bottom,
            width: box.width, height: box.height }];
        })),
      }));
      return { ...native, ...dom };
    }

    async function stableLayout() {
      // Hidden Electron windows can throttle rAF. Poll native and CSS bounds instead, and
      // require the CSS viewport to agree with the requested native content size/zoom.
      let previous;
      let repeated = 0;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const metrics = await layoutMetrics();
        const serialized = JSON.stringify(metrics);
        const sized = Math.abs(metrics.viewport.width - metrics.contentBounds.width / metrics.zoomFactor) <= 2
          && Math.abs(metrics.viewport.height - metrics.contentBounds.height / metrics.zoomFactor) <= 2;
        repeated = sized && serialized === previous ? repeated + 1 : 0;
        if (repeated >= 2) return metrics;
        previous = serialized;
        await delay(50);
      }
      throw new Error(`Window layout did not settle: ${previous}`);
    }

    async function checkControlBounds(stage) {
      const metrics = await stableLayout();
      report.layouts.push({ stage, ...metrics }); save();
      assert.ok(metrics.document.width <= metrics.viewport.width + 1,
        `${stage}: document overflows the CSS viewport`);
      for (const [id, box] of Object.entries(metrics.controls)) {
        assert.ok(box && box.width > 0 && box.height > 0, `${stage}: #${id} must occupy visible space`);
        assert.ok(box.x >= -1 && box.y >= -1
          && box.right <= metrics.viewport.width + 1 && box.bottom <= metrics.viewport.height + 1,
        `${stage}: #${id} is outside the CSS viewport: ${JSON.stringify({ box, viewport: metrics.viewport })}`);
      }
    }

    async function screenshot(filename) {
      await stableLayout();
      const captured = await app.evaluate(async ({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0];
        const image = await window.capturePage();
        return { png: image.toPNG().toString('base64'), imageSize: image.getSize(),
          contentBounds: window.getContentBounds(), zoomFactor: window.webContents.getZoomFactor() };
      });
      const png = Buffer.from(captured.png, 'base64');
      fs.writeFileSync(path.join(output, filename), png);
      report.captures.push({ filename, method: 'BrowserWindow.capturePage',
        imageSize: captured.imageSize, contentBounds: captured.contentBounds,
        zoomFactor: captured.zoomFactor,
        pngPixels: { width: png.readUInt32BE(16), height: png.readUInt32BE(20) } });
      save();
    }

    page.on('pageerror', error => report.consoleErrors.push(error.message));
    await page.waitForFunction(() => document.getElementById('connection-manager')?.textContent === '연결됨', null, { timeout: 20000 });
    await page.waitForFunction(() => {
      const mark = document.querySelector('img.brand-mark');
      return mark?.complete && mark.naturalWidth === 32 && mark.naturalHeight === 32;
    });
    let first = await page.evaluate(() => window.jev.overview());
    assert.equal(first.project.project_root.toLowerCase(), root.toLowerCase());
    assert.equal(first.engine.worker_pid, null, 'Opening the application must not load the model');
    assert.equal(first.engine.broker_pid, null, 'Opening the application must not start a broker');
    assert.ok(first.works.items.length > 0);
    assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
    assert.equal(await page.evaluate(() => typeof window.process), 'undefined');
    report.observations.push({ stage: 'initial', snapshot: first }); save();
    await page.locator('#work-list button').first().click();
    await page.waitForFunction(() => document.getElementById('work-goal').textContent.length > 0);
    assert.equal(await page.locator('#work-title').textContent(), first.works.items[0].title);
    await page.keyboard.press('Control+f');
    await page.locator('#work-filter').fill('존재하지않는검증용작업');
    await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 0);
    await page.keyboard.press('Escape');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.getElementById('work-detail').hidden);
    report.devicePixelRatio = await page.evaluate(() => window.devicePixelRatio);
    // A real keyboard-accessibility navigation must preserve the privileged bridge boundary.
    await page.evaluate(() => { location.hash = 'main'; });
    assert.equal((await page.evaluate(() => window.jev.overview())).engine.worker_pid, null);
    await page.locator('#check-connection').click();
    await page.waitForFunction(() => document.getElementById('connection-mcp').textContent === '통신 확인', null, { timeout: 25000 });
    const checked = await page.evaluate(() => window.jev.overview());
    assert.equal(checked.engine.worker_pid, null, 'MCP checks must not load model weights');
    assert.equal(checked.connection.desktop_current_session, 'not_observed');
    report.observations.push({ stage: 'connection-checked', snapshot: checked }); save();
    if (await page.locator('#action-dismiss').isVisible()) await page.locator('#action-dismiss').click();
    await screenshot('manager-idle.png');
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(940, 690));
    await checkControlBounds('940x690-zoom100');
    await screenshot('manager-small.png');
    const initialGoal = await page.locator('#work-goal').boundingBox();
    assert.ok(initialGoal && initialGoal.y < await page.evaluate(() => innerHeight), 'Work goal should start in the first viewport');
    await page.locator('#connection-details summary').click();
    await page.locator('#connection-description').waitFor({ state: 'visible' });
    await screenshot('manager-connections.png');
    await page.locator('#connection-details summary').click();
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1.25));
    await checkControlBounds('940x690-zoom125');
    await screenshot('manager-zoom125.png');
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1));
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1220, 860));
    await stableLayout();
    if (withModel) {
      const started = Date.now();
      await page.locator('#prepare-model').click();
      let current;
      do {
        current = await page.evaluate(() => window.jev.overview());
        if (current.engine.state === 'shadow') break;
        if (current.engine.state === 'unavailable') throw new Error(JSON.stringify(current.engine));
        await delay(1000);
      } while (Date.now() - started < 45000);
      assert.equal(current.engine.state, 'shadow', 'Model should become ready from cached assets');
      assert.equal(current.engine.startup.loaded_from_cache, true);
      report.readyObservedSeconds = (Date.now() - started) / 1000;
      report.observations.push({ stage: 'model-ready', snapshot: current }); save();
      await page.locator('#refresh').click();
      await page.waitForFunction(() => document.getElementById('model-state').textContent === '준비 완료');
      await screenshot('manager-ready.png');
      await page.locator('#stop-model').click();
      const stopDeadline = Date.now() + 10000;
      do {
        current = await page.evaluate(() => window.jev.overview());
        if (!current.engine.worker_pid && !current.engine.broker_pid) break;
        await delay(250);
      } while (Date.now() < stopDeadline);
      assert.equal(current.engine.worker_pid, null);
      assert.equal(current.engine.broker_pid, null);
      report.observations.push({ stage: 'model-stopped', snapshot: current });
    }
    assert.deepEqual(report.consoleErrors, []);
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.stack;
    process.exitCode = 1;
  } finally {
    if (app) await app.close();
    report.finishedAt = new Date().toISOString(); save();
    console.log(JSON.stringify({ passed: report.passed, output: reportPath, error: report.error, readyObservedSeconds: report.readyObservedSeconds }));
  }
})();
