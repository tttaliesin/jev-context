'use strict';

// UI fixtures only: serve the real renderer, never load Electron main/preload or Python/DB/model.
// NODE_PATH must point to the development runtime containing Playwright. No browser download.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');

const root = path.resolve(__dirname, '..');
const renderer = path.join(root, 'desktop', 'renderer');
const outputArgument = process.argv.indexOf('--output');
const output = outputArgument >= 0
  ? path.resolve(process.argv[outputArgument + 1])
  : path.join(root, '.local', 'desktop-redesign-20260926');
fs.mkdirSync(output, { recursive: true });
const reportFile = path.join(output, 'fixture-state-report.json');
const sourceFiles = ['index.html', 'style.css', 'renderer.js', 'command-menu.js', '../assets/jev-mark.svg'];
const hashes = () => Object.fromEntries(sourceFiles.map(name => [name,
  crypto.createHash('sha256').update(fs.readFileSync(path.join(renderer, name))).digest('hex')]));
const report = {
  kind: 'renderer_fixture_validation', fixtureRendering: true,
  scope: 'Real renderer over loopback HTTP with injected window.jev fixtures; Electron IPC and native runtimes are not exercised.',
  nativeModelTest: false, databaseAccess: false, nativeCodexConnectionVerified: false,
  startedAt: new Date().toISOString(), sourceHashes: hashes(), scenarios: [], pageErrors: [],
};
const save = () => fs.writeFileSync(reportFile, JSON.stringify(report, null, 2));

function work(index, extra = {}) {
  return {
    work_id: `work-fixture-${String(index).padStart(3, '0')}`,
    title: `검증 작업 ${String(index).padStart(3, '0')}`,
    goal: `작업 ${index}의 저장된 목표를 확인합니다.`, revision: 1,
    updated_at: '2026-09-26T08:00:00Z', status: 'active', provenance: 'agent_reported',
    scope: { mode: 'implement', constraints: ['원문과 기존 작업 기록을 보존합니다.'] },
    progress: { summary: '화면 상태를 확인하는 가짜 기록입니다.', next_actions: ['근거와 결과를 대조합니다.'] },
    issues: [], ...extra,
  };
}

function fixture(state = 'idle', options = {}) {
  const rows = options.rows || [work(1), work(2), work(3)];
  return {
    rows, details: { ...Object.fromEntries(rows.map(item => [item.work_id, item])), ...options.details },
    failOverview: false, holdWork: !!options.holdWork,
    ...(options.storageSeed !== undefined ? { storageSeed: options.storageSeed } : {}),
    snapshot: {
      config_path: 'C:\\fixture-only\\project.toml',
      project: { project_id: 'project-fixture', project_root: 'C:\\fixture-only\\한국어 작업 공간',
        data_root: 'C:\\fixture-only\\state', contract_version: '2.0', read_only: false },
      engine: { state, configured_mode: state === 'active' ? 'active' : 'shadow',
        model_revision: 'FixtureModel@not-a-real-model', device: 'GPU (fixture)',
        broker_pid: ['idle', 'unavailable'].includes(state) ? null : 101,
        worker_pid: ['shadow', 'active'].includes(state) ? 102 : null,
        active_requests: 0, idle_timeout_seconds: 120,
        can_prepare: ['idle', 'unavailable'].includes(state),
        can_stop: ['shadow', 'active'].includes(state), startup: null,
        ...options.engine },
      works: { list_revision: 100 },
      sources: { count: 7, stale_sources: { count: 0 } }, workspace_error: null,
      connection: { manager_bridge: 'connected', mcp_stdio: 'not_checked',
        desktop_current_session: 'not_observed', ...options.connection },
    },
  };
}

function installFixture(data) {
  const copy = value => JSON.parse(JSON.stringify(value));
  // A reload keeps the actual renderer's writes; seed each scenario only once.
  if (data.storageSeed !== undefined && !sessionStorage.getItem('jev.fixture.seeded')) {
    localStorage.setItem('jev.ui.v1', typeof data.storageSeed === 'string'
      ? data.storageSeed : JSON.stringify(data.storageSeed));
    sessionStorage.setItem('jev.fixture.seeded', '1');
  }
  const fixture = window.__fixture = { ...data, calls: [], pendingWork: [], pendingOverview: [],
    projectQueue: data.projectQueue || [], failWorkIds: [] };
  const overview = (options = {}) => {
    if (fixture.failOverview) throw new Error('가짜 조회 실패: 현재 상태를 확인할 수 없습니다.');
    const start = options.cursor ? Number(options.cursor) : 0;
    const limit = options.limit || 50;
    return { ...copy(fixture.snapshot), works: {
      ...copy(fixture.snapshot.works), items: copy(fixture.rows.slice(start, start + limit)),
      next_cursor: start + limit < fixture.rows.length ? String(start + limit) : null,
    } };
  };
  window.jev = {
    overview: async (options) => {
      fixture.calls.push({ method: 'overview', options });
      if (fixture.holdOverview) await new Promise(resolve => fixture.pendingOverview.push(resolve));
      return overview(options);
    },
    openWork: async (id) => {
      fixture.calls.push({ method: 'openWork', id });
      if (fixture.holdWork) await new Promise(resolve => fixture.pendingWork.push(resolve));
      if (fixture.failWorkIds.includes(id) || !fixture.details[id]) throw new Error('가짜 상세 조회 실패');
      return copy(fixture.details[id]);
    },
    prepareModel: async () => {
      fixture.calls.push({ method: 'prepareModel' });
      Object.assign(fixture.snapshot.engine, { state: 'preparing', can_prepare: false, can_stop: false });
      return overview();
    },
    stopModel: async () => {
      fixture.calls.push({ method: 'stopModel' });
      Object.assign(fixture.snapshot.engine, { state: 'idle', worker_pid: null, broker_pid: null,
        can_prepare: true, can_stop: false });
      return overview();
    },
    checkConnection: async () => {
      fixture.calls.push({ method: 'checkConnection' });
      Object.assign(fixture.snapshot.connection, { mcp_stdio: 'verified', checked_at: new Date().toISOString() });
      return overview();
    },
    selectProject: async () => {
      fixture.calls.push({ method: 'selectProject' });
      const next = fixture.projectQueue.shift();
      if (!next) return null;
      fixture.rows = copy(next.rows);
      fixture.details = copy(next.details);
      fixture.snapshot = copy(next.snapshot);
      return overview();
    },
  };
}

async function waitText(page, selector, expected) {
  await page.waitForFunction(({ selector, expected }) =>
    document.querySelector(selector)?.textContent.includes(expected), { selector, expected });
}

async function assertNoOverflow(page) {
  const metrics = await page.evaluate(() => ({ width: innerWidth,
    scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) }));
  assert.ok(metrics.scroll <= metrics.width + 1, `Horizontal overflow: ${JSON.stringify(metrics)}`);
}

async function waitIdle(page) {
  await page.waitForFunction(() => !document.getElementById('refresh').disabled
    && document.querySelector('.work-panel').getAttribute('aria-busy') === 'false');
}

async function assertNoModelActions(page) {
  assert.deepEqual(await page.evaluate(() => window.__fixture.calls.filter(call =>
    ['prepareModel', 'stopModel'].includes(call.method))), [],
  'Basic UI interactions must never start or stop the model');
}

function savedUI(data, selectedId, query = '', sidebarCollapsed = false) {
  const snapshot = data.snapshot;
  const key = `${snapshot.project?.project_id || ''}|${snapshot.config_path || snapshot.project?.project_root || ''}`
    .replaceAll('\\', '/').toLowerCase();
  return { sidebarCollapsed, projects: { [key]: { selectedId, query } } };
}

async function expectSidebarWidth(page, expected, label = 'Sidebar width') {
  await page.waitForFunction(width => Math.abs(document.getElementById('sidebar')
    .getBoundingClientRect().width - width) <= 1, expected);
  const actual = await page.locator('#sidebar').evaluate(element => element.getBoundingClientRect().width);
  assert.ok(Math.abs(actual - expected) <= 1, `${label}: expected=${expected}, actual=${actual}`);
}

async function openCommands(page) {
  await page.keyboard.press('Control+k');
  await page.waitForFunction(() => document.getElementById('command-dialog').open);
  assert.equal(await page.locator('#command-dialog').evaluate(element => element.tagName), 'DIALOG');
  assert.equal(await page.locator('#command-input').evaluate(element => element === document.activeElement), true);
}

function command(page, id) {
  return page.locator(`#command-results [role="option"][data-command-id="${id}"]`);
}

async function closeCommands(page) {
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.getElementById('command-dialog').open);
}

(async () => {
  let browser;
  const server = http.createServer((request, response) => {
    const route = new URL(request.url, 'http://localhost').pathname;
    const name = route === '/jev-mark.svg' ? '../assets/jev-mark.svg'
      : route === '/' ? 'index.html' : route.slice(1);
    if (!sourceFiles.includes(name)) { response.writeHead(404); response.end(); return; }
    response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript; charset=utf-8'
      : name.endsWith('.css') ? 'text/css; charset=utf-8'
        : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html; charset=utf-8');
    response.end(fs.readFileSync(path.join(renderer, name)));
  });
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const executable = [process.env.JEV_TEST_BROWSER, chromium.executablePath(),
      path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      path.join(process.env.ProgramFiles || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe')]
      .filter(Boolean).find(candidate => fs.existsSync(candidate));
    if (!executable) throw new Error('No installed Chromium browser. Set JEV_TEST_BROWSER; this test never downloads one.');
    report.browserExecutable = executable;
    browser = await chromium.launch({ executablePath: executable, headless: true, args: ['--disable-gpu'] });

    async function scenario(name, data, checks, viewport = { width: 1280, height: 900 }) {
      const context = await browser.newContext({ viewport, locale: 'ko-KR', colorScheme: 'light' });
      const page = await context.newPage();
      page.setDefaultTimeout(5000);
      page.on('pageerror', error => report.pageErrors.push({ scenario: name, message: error.message }));
      await page.route('**/*', route => route.request().url().startsWith(origin + '/')
        ? route.continue() : route.abort());
      await page.addInitScript(installFixture, data);
      const result = { name, fixtureRendering: true, viewport };
      try {
        await page.goto(origin, { waitUntil: 'load' });
        await waitText(page, '#connection-manager', '연결됨');
        await checks(page);
        await assertNoOverflow(page);
        result.passed = true;
      } catch (error) {
        result.passed = false;
        result.error = error.stack;
      } finally {
        result.calls = await page.evaluate(() => window.__fixture?.calls || []).catch(() => []);
        result.screenshot = path.join(output, `${name}.png`);
        await page.screenshot({ path: result.screenshot, fullPage: true }).catch(error => { result.screenshotError = error.message; });
        report.scenarios.push(result);
        save();
        await context.close();
      }
    }

    await scenario('idle-no-eager-model', fixture(), async page => {
      await waitText(page, '#model-state', '대기');
      assert.equal(await page.locator('#prepare-model').isEnabled(), true);
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
      await page.locator('#refresh').click();
      await page.locator('#check-connection').click();
      await waitText(page, '#connection-mcp', '통신 확인');
      await waitText(page, '#connection-desktop', '직접 관측 안 함');
      const mutations = await page.evaluate(() => window.__fixture.calls.filter(call =>
        ['prepareModel', 'stopModel'].includes(call.method)));
      assert.deepEqual(mutations, [], 'Read/connection actions must not request model lifecycle changes');
    });
    await scenario('explicit-prepare', fixture(), async page => {
      await page.locator('#prepare-model').click();
      await waitText(page, '#model-state', '준비 중');
      assert.equal(await page.locator('#prepare-model').isDisabled(), true);
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
      assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'prepareModel').length), 1);
    });
    await scenario('preparing', fixture('preparing'), async page => {
      await waitText(page, '#model-state', '준비 중');
      assert.equal(await page.locator('#preparing-indicator').isVisible(), true);
      assert.equal(await page.locator('#prepare-model').isDisabled(), true);
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
    });
    await scenario('shadow-ready', fixture('shadow'), async page => {
      await waitText(page, '#model-state', '준비 완료');
      await waitText(page, '#model-mode', '관찰');
      assert.equal(await page.locator('#stop-model').isEnabled(), true);
      assert.equal(await page.locator('#prepare-model').isDisabled(), true);
    });
    await scenario('broker-without-model', fixture('idle', {
      engine: { broker_pid: 101, worker_pid: null, can_stop: true },
    }), async page => {
      await waitText(page, '#model-state', '대기');
      assert.equal(await page.locator('#stop-model').isDisabled(), true,
        'A lightweight broker alone must not enable model termination');
    });
    await scenario('active-request', fixture('active', { engine: { active_requests: 1, can_stop: false } }), async page => {
      await waitText(page, '#model-state', '판단 중');
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
    });
    await scenario('model-unavailable', fixture('unavailable', { engine: { preparation_error: 'engine_unavailable' } }), async page => {
      await waitText(page, '#model-detail', 'engine_unavailable');
      assert.equal(await page.locator('#prepare-model').isEnabled(), true);
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
    });
    await scenario('connection-unverified', fixture(), async page => {
      await waitText(page, '#connection-mcp', '아직 점검 안 함');
      await waitText(page, '#connection-desktop', '직접 관측 안 함');
      assert.equal(await page.locator('#check-connection').isVisible(), true);
    });
    await scenario('inflight-detail-survives-connection-action', fixture('idle', { holdWork: true }), async page => {
      await page.waitForFunction(() => window.__fixture.pendingWork.length === 1);
      await page.locator('#check-connection').click();
      await waitText(page, '#connection-mcp', '통신 확인');
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.holdWork = false;
        fixture.pendingWork.splice(0).forEach(resolve => resolve());
      });
      await waitText(page, '#work-title', '검증 작업 001');
      assert.equal(await page.locator('.work-panel').getAttribute('aria-busy'), 'false');
      assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length), 1,
        'An unrelated same-project action should not cancel or duplicate an in-flight detail read');
    });
    await scenario('stale-status', fixture('shadow'), async page => {
      await waitText(page, '#model-state', '준비 완료');
      await page.evaluate(() => { window.__fixture.failOverview = true; });
      await page.locator('#refresh').click();
      await waitText(page, '#error-title', '상태를 갱신하지 못했습니다');
      await waitText(page, '#model-state', '현재 상태 미확인');
      await waitText(page, '#model-memory', '미확인');
      await waitText(page, '#connection-manager', '연결 미확인');
      assert.equal(await page.locator('#prepare-model').isDisabled(), true);
      assert.equal(await page.locator('#stop-model').isDisabled(), true);
    });
    await scenario('workspace-error-clears-detail', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => {
        window.__fixture.snapshot.workspace_error = { code: 'storage_failed', message: '가짜 저장소 오류' };
        window.__fixture.rows = [];
      });
      await page.locator('#refresh').click();
      await waitText(page, '#error-message', '가짜 저장소 오류');
      assert.equal(await page.locator('#work-detail').isVisible(), false);
      assert.equal(await page.locator('#work-list button').count(), 0);
    });
    await scenario('no-saved-work', fixture('idle', { rows: [] }), async page => {
      await waitText(page, '#work-list-empty', '저장된 작업이 없습니다');
      assert.equal(await page.locator('#work-detail').isVisible(), false);
    });
    await scenario('no-filter-match', fixture(), async page => {
      await page.locator('#work-filter').fill('존재하지않는검색어');
      await waitText(page, '#work-list-empty', '검색 결과가 없습니다');
      assert.equal(await page.locator('#work-list button').count(), 0);
    });

    const long = work(1, {
      title: '프로젝트 이동 뒤 원문과 결정 기록을 보존하며 모델 연결 상태를 검증하는 한국어 작업 '.repeat(5),
      goal: '아주긴한국어목표문장을공백없이표시해도창너비를넘지않고확인할수있어야합니다'.repeat(12),
      scope: { mode: 'implement', constraints: ['긴한국어제약을그대로보존합니다'.repeat(20)] },
      issues: [{ status: 'open', text: '문서와 코드의 근거 대조가 남아 있습니다.' }],
    });
    await scenario('long-korean-small-window', fixture('shadow', { rows: [long] }), async page => {
      await waitText(page, '#work-goal', '아주긴한국어목표');
      await waitText(page, '#work-issues', '문서와 코드의 근거 대조');
      assert.equal((await page.locator('#work-goal').textContent()).length, long.goal.length);
      const titleBox = await page.locator('#work-title').boundingBox();
      assert.ok(titleBox && titleBox.y < 690, 'Work title should be visible in the compact window');
    }, { width: 940, height: 690 });

    await scenario('pagination-refresh-and-detail-update', fixture('idle', { rows: Array.from({ length: 75 }, (_, index) => work(index + 1)) }), async page => {
      await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 50);
      await page.locator('#load-more').click();
      await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 75);
      await page.locator('#work-list button').nth(54).click();
      await waitText(page, '#work-title', '검증 작업 055');
      await page.locator('#refresh').click();
      await page.waitForFunction(() => !document.getElementById('refresh').disabled);
      assert.equal(await page.locator('#work-list button').count(), 75, 'A same-revision refresh must keep loaded pages');
      await waitText(page, '#work-title', '검증 작업 055');
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.snapshot.works.list_revision += 1;
        fixture.rows[54].revision = 2;
        fixture.details[fixture.rows[54].work_id] = { ...fixture.details[fixture.rows[54].work_id], revision: 2,
          goal: '첫 페이지 밖에서 선택한 작업의 최신 목표입니다.' };
      });
      await page.locator('#refresh').click();
      await waitText(page, '#work-goal', '첫 페이지 밖에서 선택한 작업의 최신 목표');
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.snapshot.works.list_revision += 1;
        fixture.savedDetail = fixture.details['work-fixture-055'];
        delete fixture.details['work-fixture-055'];
      });
      await page.locator('#refresh').click();
      await page.locator('#work-retry').waitFor({ state: 'visible' });
      await waitText(page, '#work-empty', '불러오지 못');
      assert.equal(await page.locator('#work-detail').isVisible(), false);
      await page.evaluate(() => {
        // Restore availability without changing the list revision: Refresh must retry the detail.
        window.__fixture.details['work-fixture-055'] = window.__fixture.savedDetail;
      });
      await page.locator('#refresh').click();
      await waitText(page, '#work-goal', '첫 페이지 밖에서 선택한 작업의 최신 목표');
      assert.equal(await page.locator('#work-detail').isVisible(), true);
      await page.locator('#work-list button[data-work-id="work-fixture-001"]').click();
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.snapshot.works.list_revision += 1;
        fixture.rows[0].revision = 2;
        fixture.details[fixture.rows[0].work_id] = { ...fixture.details[fixture.rows[0].work_id], revision: 2,
          goal: '새 revision에서 갱신한 목표입니다.' };
      });
      await page.locator('#refresh').click();
      await waitText(page, '#work-goal', '새 revision에서 갱신한 목표');
      await waitText(page, '#work-revision', '2');
    });
    await scenario('selected-work-access-revoked', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => {
        const fixture = window.__fixture;
        const removed = fixture.rows.shift();
        fixture.snapshot.works.list_revision += 1;
        fixture.details[removed.work_id] = { redacted: true, reason: 'source_policy_denied' };
      });
      await page.locator('#refresh').click();
      await page.waitForFunction(() => document.getElementById('work-detail').hidden);
      assert.equal(await page.locator('#work-detail').isVisible(), false,
        'An inaccessible selected work must not keep its old evidence on screen');
    });
    await scenario('keyboard-list-and-search', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.keyboard.press('Control+f');
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('ArrowDown');
      const focused = () => page.evaluate(() => document.activeElement?.dataset.workId);
      assert.equal(await focused(), 'work-fixture-001');
      await page.keyboard.press('ArrowDown');
      assert.equal(await focused(), 'work-fixture-002');
      await page.keyboard.press('End');
      assert.equal(await focused(), 'work-fixture-003');
      await page.keyboard.press('ArrowUp');
      assert.equal(await focused(), 'work-fixture-002');
      await page.keyboard.press('Home');
      assert.equal(await focused(), 'work-fixture-001');
      await page.keyboard.press('End');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 003');
      await page.keyboard.press('Control+f');
      await page.locator('#work-filter').fill('찾을수없는내용');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#work-filter').inputValue(), '');
      assert.equal(await page.locator('#work-list button').count(), 3);
    });

    await scenario('sidebar-collapse-focus-search-and-reload', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      const collapsed = () => page.locator('.app-shell').evaluate(element => element.classList.contains('sidebar-collapsed'));
      assert.equal(await collapsed(), false);
      await page.locator('#work-list button').first().focus();
      await page.keyboard.press('Control+b');
      assert.equal(await collapsed(), true);
      assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'), 'false');
      await expectSidebarWidth(page, 0, 'A collapsed sidebar must release its complete width');
      assert.equal(await page.locator('#work-filter').isVisible(), false);
      assert.equal(await page.locator('#sidebar-toggle').evaluate(element => element === document.activeElement), true,
        'Collapsing a focused sidebar must leave focus on an available control');
      await page.reload();
      await waitText(page, '#work-title', '검증 작업 001');
      assert.equal(await collapsed(), true, 'The collapsed preference must survive app reload');
      // The complete-collapse design replaces the old rail search icon with quick commands.
      await page.locator('#command-open').click();
      await page.waitForFunction(() => document.getElementById('command-dialog').open);
      await command(page, 'filter').click();
      assert.equal(await collapsed(), false);
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      await page.locator('#work-filter').fill('검증');
      await page.keyboard.press('Control+b');
      assert.equal(await collapsed(), false, 'Ctrl+B must not hide the input while typing');
      assert.equal(await page.locator('#work-filter').inputValue(), '검증');
      await page.locator('#sidebar-toggle').click();
      assert.equal(await collapsed(), true);
      await page.keyboard.press('Control+f');
      assert.equal(await collapsed(), false, 'Search shortcut must reveal a collapsed sidebar');
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'), 'true');
      await assertNoModelActions(page);
    }, { width: 940, height: 690 });

    await scenario('shortcut-dialog-keyboard-and-focus-return', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.locator('#shortcuts-open').click();
      await page.waitForFunction(() => document.getElementById('shortcuts-dialog').open);
      assert.equal(await page.locator('#shortcuts-dialog').evaluate(element => element.tagName), 'DIALOG');
      await page.locator('#shortcuts-close').click();
      assert.equal(await page.locator('#shortcuts-dialog').isVisible(), false);
      assert.equal(await page.locator('#shortcuts-open').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('Control+f');
      await page.keyboard.press('F1');
      await page.waitForFunction(() => document.getElementById('shortcuts-dialog').open);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('shortcuts-dialog').open);
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true,
        'Closing help must return focus to the element that opened it');
      await assertNoModelActions(page);
    });

    await scenario('diagnostic-popover-dismissal', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      const summary = page.locator('#model-diagnostics > summary');
      await summary.click();
      assert.equal(await page.locator('#model-diagnostics').evaluate(element => element.open), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#model-diagnostics').evaluate(element => element.open), false);
      assert.equal(await summary.evaluate(element => element === document.activeElement), true);
      await summary.click();
      await page.locator('#work-title').click();
      assert.equal(await page.locator('#model-diagnostics').evaluate(element => element.open), false,
        'Clicking the record must dismiss an overlapping diagnostic popover');
      assert.equal(await page.locator('#work-constraints-details').evaluate(element => element.open), true,
        'Closing a temporary popover must not collapse inline work constraints');
      await assertNoModelActions(page);
    });

    await scenario('detail-direct-retry-and-loading-feedback', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => {
        window.__fixture.holdWork = true;
        window.__fixture.failWorkIds = ['work-fixture-002'];
      });
      await page.locator('#work-list button').nth(1).click();
      await page.waitForFunction(() => window.__fixture.pendingWork.length === 1);
      assert.equal(await page.locator('#work-loading').isVisible(), true);
      await page.evaluate(() => {
        window.__fixture.holdWork = false;
        window.__fixture.pendingWork.splice(0).forEach(resolve => resolve());
      });
      await page.locator('#work-retry').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#work-loading').isVisible(), false);
      assert.equal(await page.locator('#work-detail').isVisible(), false);
      const failedReadCount = await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length);
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      await waitIdle(page);
      assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length), failedReadCount,
        'Automatic foreground refresh must not repeatedly retry a failed detail');
      assert.equal(await page.locator('#work-retry').isVisible(), true);
      const before = await page.evaluate(() => {
        window.__fixture.failWorkIds = [];
        return window.__fixture.calls.length;
      });
      await page.locator('#work-retry').click();
      await waitText(page, '#work-title', '검증 작업 002');
      const newCalls = await page.evaluate(start => window.__fixture.calls.slice(start), before);
      assert.deepEqual(newCalls[0], { method: 'openWork', id: 'work-fixture-002' },
        'Detail retry must directly request the failed selected work');
      assert.equal(await page.locator('#work-retry').isVisible(), false);
      await page.evaluate(() => { window.__fixture.holdOverview = true; });
      await page.locator('#refresh').click();
      await page.waitForFunction(() => window.__fixture.pendingOverview.length === 1);
      assert.equal(await page.locator('#list-loading').isVisible(), true);
      await page.evaluate(() => {
        window.__fixture.holdOverview = false;
        window.__fixture.pendingOverview.splice(0).forEach(resolve => resolve());
      });
      await waitIdle(page);
      assert.equal(await page.locator('#list-loading').isVisible(), false);
      await assertNoModelActions(page);
    });

    const readingRows = [1, 2].map(index => work(index, {
      goal: Array.from({ length: 24 }, (_, line) => `작업 ${index}의 긴 목표 ${line + 1}번째 줄을 읽습니다.`).join('\n'),
      progress: { summary: '긴 기록 아래의 진행 보고입니다.\n'.repeat(12), next_actions: [] },
    }));
    await scenario('same-work-and-refresh-preserve-reading-position', fixture('idle', { rows: readingRows }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      const scroll = await page.locator('.work-panel').evaluate(element => {
        element.scrollTop = 180;
        return element.scrollTop;
      });
      assert.ok(scroll >= 100, 'Fixture must contain enough content to exercise reading position');
      const calls = await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length);
      await page.locator('#work-list button').first().click();
      assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length), calls,
        'Re-selecting the displayed work must not discard it and start another read');
      let actualScroll = await page.locator('.work-panel').evaluate(element => element.scrollTop);
      assert.ok(Math.abs(actualScroll - scroll) <= 1,
        `Same-work click must preserve reading position: expected=${scroll}, actual=${actualScroll}`);
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.snapshot.works.list_revision += 1;
        fixture.rows[0].revision = 2;
        fixture.details['work-fixture-001'].revision = 2;
      });
      await page.locator('#refresh').click();
      await waitText(page, '#work-revision', '2');
      await waitIdle(page);
      actualScroll = await page.locator('.work-panel').evaluate(element => element.scrollTop);
      assert.ok(Math.abs(actualScroll - scroll) <= 1,
        `A newer revision must preserve reading position: expected=${scroll}, actual=${actualScroll}`);
      await page.evaluate(() => {
        const fixture = window.__fixture;
        fixture.holdWork = true;
        fixture.snapshot.works.list_revision += 1;
        fixture.rows[0].revision = 3;
        fixture.details['work-fixture-001'].revision = 3;
      });
      await page.locator('#refresh').click();
      await page.waitForFunction(() => window.__fixture.pendingWork.length === 1);
      const movedWhileLoading = await page.locator('.work-panel').evaluate(element => {
        element.scrollTop = 260;
        return element.scrollTop;
      });
      assert.ok(movedWhileLoading >= 200,
        `Fixture must allow scrolling while the detail is loading: requested=260, actual=${movedWhileLoading}`);
      await page.evaluate(() => {
        window.__fixture.holdWork = false;
        window.__fixture.pendingWork.splice(0).forEach(resolve => resolve());
      });
      await waitText(page, '#work-revision', '3');
      await waitIdle(page);
      actualScroll = await page.locator('.work-panel').evaluate(element => element.scrollTop);
      assert.ok(Math.abs(actualScroll - movedWhileLoading) <= 1,
        `Refresh must retain the user's latest in-flight scroll: expected=${movedWhileLoading}, actual=${actualScroll}`);
      await page.locator('#work-list button').nth(1).click();
      await waitText(page, '#work-title', '검증 작업 002');
      assert.equal(await page.locator('.work-panel').evaluate(element => element.scrollTop), 0,
        'A different work starts at its title');
      await assertNoModelActions(page);
    }, { width: 940, height: 690 });

    await scenario('narrow-document-scroll-resets-for-new-work', fixture('idle', { rows: readingRows }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => window.scrollTo(0, 300));
      await page.waitForFunction(() => window.scrollY >= 200);
      // DOM click avoids Playwright scrolling the navigation on our behalf before dispatch.
      await page.locator('#work-list button').nth(1).evaluate(element => element.click());
      await waitText(page, '#work-title', '검증 작업 002');
      await page.waitForFunction(() => window.scrollY === 0);
      await assertNoModelActions(page);
    }, { width: 650, height: 800 });

    await scenario('search-enter-count-and-ime-guard', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.keyboard.press('Control+f');
      await page.locator('#work-filter').fill('002');
      assert.equal(await page.locator('#work-list button').count(), 1);
      assert.match(await page.locator('#work-count').textContent(), /1\D+3/,
        'Search count must distinguish one match from three loaded works');
      const calls = await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length);
      await page.locator('#work-filter').evaluate(element => {
        for (const key of ['Enter', 'ArrowDown', 'Escape']) {
          element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, isComposing: true }));
        }
      });
      assert.equal(await page.locator('#work-filter').inputValue(), '002', 'IME Escape must not erase unfinished input');
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').length), calls,
        'IME Enter must not activate a work while composition is ongoing');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 002');
      await assertNoModelActions(page);
    });

    const originalProject = fixture();
    const otherProject = fixture('idle', { rows: [work(101), work(102)] });
    otherProject.snapshot.config_path = 'C:\\fixture-only\\second-project.toml';
    otherProject.snapshot.project.project_id = 'project-second';
    otherProject.snapshot.project.project_root = 'C:\\fixture-only\\두 번째 작업 공간';
    const roundTrip = fixture();
    roundTrip.projectQueue = [otherProject, originalProject];
    await scenario('project-selection-filter-round-trip-and-reload', roundTrip, async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.locator('#work-filter').fill('002');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 002');
      await page.locator('#select-project').click();
      await waitText(page, '#project-name', '두 번째 작업 공간');
      await waitText(page, '#work-title', '검증 작업 101');
      assert.equal(await page.locator('#work-filter').inputValue(), '');
      await page.locator('#work-filter').fill('102');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 102');
      await page.locator('#select-project').click();
      await waitText(page, '#work-title', '검증 작업 002');
      assert.equal(await page.locator('#work-filter').inputValue(), '002');
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')));
      assert.ok(Object.values(stored.projects).some(item => item.selectedId === 'work-fixture-002' && item.query === '002'));
      assert.ok(Object.values(stored.projects).some(item => item.selectedId === 'work-fixture-102' && item.query === '102'));
      assert.equal(Object.values(stored.projects).some(item => 'cursor' in item || 'nextCursor' in item), false,
        'Revision-bound pagination cursors must not be saved as durable UI state');
      await page.reload();
      await waitText(page, '#work-title', '검증 작업 002');
      assert.equal(await page.locator('#work-filter').inputValue(), '002');
      assert.deepEqual(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').map(call => call.id)), ['work-fixture-002'],
        'Reload must revalidate the saved selected ID without first selecting the first row');
      await page.locator('#work-filter').fill('현재 선택과 일치하지 않는 검색어');
      await page.locator('#refresh').click();
      await waitIdle(page);
      assert.equal(await page.locator('#work-list button').count(), 0);
      await waitText(page, '#work-title', '검증 작업 002');
      await page.reload();
      await waitText(page, '#work-title', '검증 작업 002');
      assert.equal(await page.locator('#work-filter').inputValue(), '현재 선택과 일치하지 않는 검색어');
      assert.equal(await page.locator('#work-list button').count(), 0,
        'A filter that hides the selected row must not discard its detail');
      await assertNoModelActions(page);
    });

    const outsidePage = fixture('idle', { rows: Array.from({ length: 75 }, (_, index) => work(index + 1)) });
    outsidePage.storageSeed = savedUI(outsidePage, 'work-fixture-055');
    await scenario('restore-selected-work-outside-first-page', outsidePage, async page => {
      await waitText(page, '#work-title', '검증 작업 055');
      assert.equal(await page.locator('#work-list button').first().getAttribute('data-work-id'), 'work-fixture-055',
        'A revalidated off-page selection must be reachable at the front of the loaded list');
      assert.equal(await page.locator('#work-list button').count(), 51);
      assert.deepEqual(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').map(call => call.id)), ['work-fixture-055']);
      assert.equal(await page.locator('#load-more').isVisible(), true);
      await page.locator('#load-more').click();
      await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 75);
      const ids = await page.locator('#work-list button').evaluateAll(elements => elements.map(element => element.dataset.workId));
      assert.equal(new Set(ids).size, 75, 'Later pages must not duplicate the restored row');
      await waitText(page, '#work-title', '검증 작업 055');
      await assertNoModelActions(page);
    });

    const missingSavedWork = fixture();
    missingSavedWork.storageSeed = savedUI(missingSavedWork, 'work-fixture-999');
    await scenario('restore-missing-work-keeps-id-for-inline-retry', missingSavedWork, async page => {
      await page.locator('#work-retry').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#work-detail').isVisible(), false);
      assert.deepEqual(await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').map(call => call.id)), ['work-fixture-999'],
        'An unavailable saved selection must not silently switch to the first work');
      await page.evaluate(item => { window.__fixture.details[item.work_id] = item; }, work(999));
      await page.locator('#work-retry').click();
      await waitText(page, '#work-title', '검증 작업 999');
      const ids = await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'openWork').map(call => call.id));
      assert.deepEqual(ids, ['work-fixture-999', 'work-fixture-999']);
      await assertNoModelActions(page);
    });

    for (const [name, storageSeed] of [
      ['malformed-json', '{unparseable'],
      ['null-state', 'null'],
      ['wrong-field-types', { sidebarCollapsed: 'true', projects: [] }],
    ]) {
      await scenario(`invalid-ui-storage-${name}`, fixture('idle', { storageSeed }), async page => {
        await waitText(page, '#work-title', '검증 작업 001');
        assert.equal(await page.locator('.app-shell').evaluate(element => element.classList.contains('sidebar-collapsed')), false);
        assert.equal(await page.locator('#work-filter').inputValue(), '');
        await page.locator('#sidebar-toggle').click();
        assert.equal(await page.locator('.app-shell').evaluate(element => element.classList.contains('sidebar-collapsed')), true);
        await assertNoModelActions(page);
      });
    }

    await scenario('sidebar-width-keyboard-reset-collapse-and-reload', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await expectSidebarWidth(page, 260, 'Default sidebar');
      const resize = page.locator('#sidebar-resize');
      assert.equal(await resize.getAttribute('role'), 'separator');
      assert.equal(await resize.getAttribute('aria-orientation'), 'vertical');
      await resize.focus();
      await page.keyboard.press('ArrowRight');
      await expectSidebarWidth(page, 276);
      await page.keyboard.press('ArrowLeft');
      await expectSidebarWidth(page, 260);
      await page.keyboard.press('Home');
      await expectSidebarWidth(page, 220);
      await page.keyboard.press('End');
      await expectSidebarWidth(page, 400);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarWidth), 400);
      await page.reload();
      await waitText(page, '#work-title', '검증 작업 001');
      await expectSidebarWidth(page, 400, 'Width after reload');
      await resize.dblclick();
      await expectSidebarWidth(page, 260, 'Double-click reset');
      await resize.focus();
      await page.keyboard.press('Enter');
      await expectSidebarWidth(page, 0, 'Resize handle Enter collapses the entire sidebar');
      assert.equal(await page.locator('#sidebar-toggle').evaluate(element => element === document.activeElement), true);
      assert.equal(await resize.isVisible(), false);
      await page.keyboard.press('Control+b');
      await expectSidebarWidth(page, 260);
      await assertNoModelActions(page);
    });

    await scenario('sidebar-width-pointer-drag-and-bounds', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      const drag = async delta => {
        const box = await page.locator('#sidebar-resize').boundingBox();
        assert.ok(box && box.width > 0 && box.height > 0, 'Resize target must be visible and hit-testable');
        const x = box.x + box.width / 2;
        const y = box.y + Math.min(80, box.height / 2);
        await page.mouse.move(x, y);
        await page.mouse.down();
        await page.mouse.move(x + delta, y, { steps: 8 });
        await page.mouse.up();
      };
      await drag(80);
      await expectSidebarWidth(page, 340, 'Drag right');
      await drag(-40);
      await expectSidebarWidth(page, 300, 'Drag left');
      await drag(300);
      await expectSidebarWidth(page, 400, 'Pointer maximum');
      await drag(-300);
      await expectSidebarWidth(page, 220, 'Pointer minimum');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarWidth), 220);
      await assertNoModelActions(page);
    });

    await scenario('sidebar-width-responsive-clamp-keeps-preference', fixture('idle', {
      storageSeed: { sidebarCollapsed: false, sidebarWidth: 400, projects: {} },
    }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await expectSidebarWidth(page, 400);
      await page.setViewportSize({ width: 760, height: 900 });
      await expectSidebarWidth(page, 280, 'Actual width must reserve 480px for the main view');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarWidth), 400,
        'A temporary narrow window must not overwrite the preferred width');
      await assertNoOverflow(page);
      await page.setViewportSize({ width: 650, height: 800 });
      await expectSidebarWidth(page, 220, 'Actual minimum in a narrow viewport');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarWidth), 400);
      await assertNoOverflow(page);
      await page.setViewportSize({ width: 1280, height: 900 });
      await expectSidebarWidth(page, 400, 'Widening restores the preferred width');
      await assertNoModelActions(page);
    });

    await scenario('sidebar-invalid-width-falls-back-safely', fixture(), async page => {
      for (const width of ['320', null, {}, [], 260.5, 219, 401]) {
        await page.evaluate(sidebarWidth => localStorage.setItem('jev.ui.v1', JSON.stringify({
          sidebarCollapsed: false, sidebarWidth, projects: {},
        })), width);
        await page.reload();
        await waitText(page, '#work-title', '검증 작업 001');
        await expectSidebarWidth(page, 260, `Invalid width ${JSON.stringify(width)}`);
        await assertNoOverflow(page);
        await assertNoModelActions(page);
      }
    });

    await scenario('command-palette-preserves-collapsed-layout-and-focus', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.locator('#sidebar-toggle').click();
      await expectSidebarWidth(page, 0);
      const before = await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')));
      await openCommands(page);
      await expectSidebarWidth(page, 0);
      await waitText(page, '#command-scope', '불러온');
      assert.ok((await command(page, 'work:work-fixture-002').count()) === 1);
      await closeCommands(page);
      assert.equal(await page.locator('#sidebar-toggle').evaluate(element => element === document.activeElement), true);
      assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1'))), before,
        'Opening and dismissing commands must not mutate layout or project preferences');
      await openCommands(page);
      await page.locator('#command-input').fill('검증 작업 002');
      await command(page, 'work:work-fixture-002').click();
      await waitText(page, '#work-title', '검증 작업 002');
      await waitText(page, '#location-work', '검증 작업 002');
      assert.equal(await page.locator('#command-dialog').evaluate(element => element.open), false);
      await expectSidebarWidth(page, 0, 'A palette work selection must preserve collapsed layout');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jev.ui.v1')).sidebarCollapsed), true);
      await assertNoModelActions(page);
    });

    await scenario('command-palette-keyboard-filter-ime-and-activation', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await openCommands(page);
      await page.locator('#command-input').fill('검증 작업');
      const active = async () => page.evaluate(() => {
        const input = document.getElementById('command-input');
        const selected = document.getElementById(input.getAttribute('aria-activedescendant'))
          || document.querySelector('#command-results [role="option"][aria-selected="true"]')
          || document.activeElement;
        return selected?.dataset.commandId;
      });
      await page.keyboard.press('ArrowDown');
      const afterDown = await active();
      assert.match(afterDown || '', /^work:work-fixture-00[123]$/);
      await page.keyboard.press('ArrowDown');
      assert.notEqual(await active(), afterDown, 'ArrowDown must move among matching command options');
      await page.keyboard.press('ArrowUp');
      assert.equal(await active(), afterDown, 'ArrowUp must return to the previous command option');
      await page.locator('#command-input').fill('검증 작업 003');
      const before = await page.evaluate(() => window.__fixture.calls.length);
      await page.locator('#command-input').evaluate(element => {
        for (const key of ['Enter', 'ArrowDown', 'Escape']) {
          element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, isComposing: true }));
        }
      });
      assert.equal(await page.locator('#command-dialog').evaluate(element => element.open), true);
      assert.equal(await page.locator('#command-input').inputValue(), '검증 작업 003');
      assert.equal(await page.evaluate(() => window.__fixture.calls.length), before,
        'IME command entry must not activate a command or close the dialog');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 003');
      await waitText(page, '#location-work', '검증 작업 003');
      await assertNoModelActions(page);
    });

    await scenario('command-disabled-stop-and-live-availability', fixture('shadow'), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.evaluate(() => { window.__fixture.holdOverview = true; });
      await page.locator('#refresh').click();
      await page.waitForFunction(() => window.__fixture.pendingOverview.length === 1);
      await openCommands(page);
      assert.equal(await command(page, 'refresh').getAttribute('aria-disabled'), 'true');
      assert.match(await command(page, 'refresh').locator('.command-item-description').textContent(), /조회 중/,
        'A disabled refresh command must explain the pending overview request');
      await page.evaluate(() => {
        window.__fixture.holdOverview = false;
        window.__fixture.pendingOverview.splice(0).forEach(resolve => resolve());
      });
      await waitIdle(page);
      assert.equal(await command(page, 'refresh').getAttribute('aria-disabled'), 'false');
      assert.notEqual(await command(page, 'stop').getAttribute('aria-disabled'), 'true');
      await page.evaluate(() => {
        Object.assign(window.__fixture.snapshot.engine, { active_requests: 1, can_stop: false });
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await page.waitForFunction(() => document.querySelector('#command-results [data-command-id="stop"]')
        ?.getAttribute('aria-disabled') === 'true');
      await page.locator('#command-input').fill('모델 종료');
      assert.equal(await command(page, 'stop').getAttribute('aria-disabled'), 'true');
      await command(page, 'stop').evaluate(element => element.click());
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('#command-dialog').evaluate(element => element.open), true,
        'A disabled command must not activate or dismiss the palette');
      await closeCommands(page);
      await assertNoModelActions(page);
    });

    const commandProjects = fixture();
    commandProjects.projectQueue = [otherProject];
    await scenario('command-palette-current-project-items', commandProjects, async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await openCommands(page);
      assert.equal(await command(page, 'work:work-fixture-001').count(), 1);
      await command(page, 'project').click();
      await waitText(page, '#project-name', '두 번째 작업 공간');
      await waitText(page, '#work-title', '검증 작업 101');
      await openCommands(page);
      assert.equal(await command(page, 'work:work-fixture-001').count(), 0,
        'Commands must not retain work IDs from a previous project');
      assert.equal(await command(page, 'work:work-fixture-102').count(), 1);
      await page.locator('#command-input').fill('검증 작업 102');
      await page.keyboard.press('Enter');
      await waitText(page, '#work-title', '검증 작업 102');
      await waitText(page, '#location-work', '검증 작업 102');
      await assertNoModelActions(page);
    });

    await scenario('command-navigation-and-read-actions', fixture(), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await openCommands(page);
      for (const id of ['filter', 'sidebar', 'reset-width', 'refresh', 'connection', 'prepare', 'stop', 'shortcuts', 'project']) {
        assert.equal(await command(page, id).count(), 1, `Expected command ${id}`);
      }
      await command(page, 'sidebar').click();
      await expectSidebarWidth(page, 0);
      await openCommands(page);
      await command(page, 'filter').click();
      await expectSidebarWidth(page, 260);
      assert.equal(await page.locator('#work-filter').evaluate(element => element === document.activeElement), true);
      await page.locator('#sidebar-resize').focus();
      await page.keyboard.press('ArrowRight');
      await expectSidebarWidth(page, 276);
      await openCommands(page);
      await command(page, 'reset-width').click();
      await expectSidebarWidth(page, 260);
      const overviewCount = await page.evaluate(() => window.__fixture.calls.filter(call => call.method === 'overview').length);
      await openCommands(page);
      await command(page, 'refresh').click();
      await page.waitForFunction(count => window.__fixture.calls.filter(call => call.method === 'overview').length > count, overviewCount);
      await waitIdle(page);
      await openCommands(page);
      await command(page, 'connection').click();
      await waitText(page, '#connection-mcp', '통신 확인');
      await openCommands(page);
      await command(page, 'shortcuts').click();
      await page.waitForFunction(() => document.getElementById('shortcuts-dialog').open);
      assert.equal(await page.locator('#command-dialog').evaluate(element => element.open), false,
        'Opening help must close the command dialog rather than stack modal surfaces');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('shortcuts-dialog').open);
      await assertNoModelActions(page);
    });

    await scenario('filter-scope-reset-and-load-more-preserve-query', fixture('idle', {
      rows: Array.from({ length: 125 }, (_, index) => work(index + 1)),
    }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.keyboard.press('Control+f');
      await page.locator('#work-filter').fill('055');
      await waitText(page, '#search-scope', '불러온');
      assert.equal(await page.locator('#work-filter').evaluate(element =>
        element.closest('.search-field')?.nextElementSibling?.id), 'search-scope',
      'Loaded-record search scope must sit immediately below the search field');
      assert.ok((await page.locator('#work-filter').getAttribute('aria-describedby') || '')
        .split(/\s+/u).includes('search-scope'));
      assert.equal(await page.locator('#work-list button').count(), 0);
      assert.equal(await page.locator('#filter-reset').isVisible(), true);
      assert.equal(await page.locator('#load-more').isVisible(), true,
        'No match among loaded records must still allow loading the next page');
      await openCommands(page);
      assert.equal(await command(page, 'work:work-fixture-055').count(), 0,
        'Command results must not pretend unloaded records have been searched');
      assert.equal(await command(page, 'load-more').count(), 1);
      await closeCommands(page);
      await page.locator('#load-more').click();
      await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 1);
      assert.equal(await page.locator('#work-filter').inputValue(), '055');
      await waitText(page, '#work-count', '100');
      await openCommands(page);
      assert.equal(await command(page, 'work:work-fixture-055').count(), 1);
      await command(page, 'load-more').click();
      await waitText(page, '#work-count', '125');
      assert.equal(await page.locator('#work-filter').inputValue(), '055');
      await openCommands(page);
      assert.equal(await command(page, 'load-more').count(), 0);
      assert.equal(await command(page, 'work:work-fixture-055').count(), 1);
      await closeCommands(page);
      await page.locator('#work-filter').fill('없는 작업 검색어');
      assert.equal(await page.locator('#work-list button').count(), 0);
      await page.locator('#filter-reset').click();
      assert.equal(await page.locator('#work-filter').inputValue(), '');
      assert.equal(await page.locator('#work-list button').count(), 125);
      await waitText(page, '#work-title', '검증 작업 001');
      await assertNoModelActions(page);
    });

    await scenario('silent-refresh-does-not-flash-list-loading', fixture('idle', {
      rows: Array.from({ length: 75 }, (_, index) => work(index + 1)),
    }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      assert.equal(await page.locator('#load-more').isVisible(), true);
      const loadMoreLabel = await page.locator('#load-more').textContent();
      await page.evaluate(() => {
        window.__fixture.holdOverview = true;
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await page.waitForFunction(() => window.__fixture.pendingOverview.length === 1);
      assert.equal(await page.locator('#list-loading').isVisible(), false,
        'Background polling must not flash a loading label over the existing list');
      assert.equal(await page.locator('#load-more').textContent(), loadMoreLabel,
        'Silent refresh must retain the load-more label while the overview is pending');
      await waitText(page, '#location-work', '검증 작업 001');
      assert.equal(await page.locator('#work-detail').isVisible(), true);
      await page.evaluate(() => {
        window.__fixture.holdOverview = false;
        window.__fixture.pendingOverview.splice(0).forEach(resolve => resolve());
      });
      await waitIdle(page);
      await assertNoModelActions(page);
    });

    await scenario('command-scroll-focus-and-selection-survive-silent-refresh', fixture('idle', {
      rows: Array.from({ length: 75 }, (_, index) => work(index + 1)),
    }), async page => {
      await waitText(page, '#work-title', '검증 작업 001');
      await page.locator('#load-more').click();
      await page.waitForFunction(() => document.querySelectorAll('#work-list button').length === 75);
      await waitIdle(page);
      await openCommands(page);
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('ArrowDown');
      const activeCommand = () => page.evaluate(() => {
        const id = document.getElementById('command-input').getAttribute('aria-activedescendant');
        return document.getElementById(id)?.dataset.commandId;
      });
      const selectedBefore = await activeCommand();
      assert.ok(selectedBefore, 'Keyboard navigation must identify an active command');
      const expectedScroll = await page.locator('.command-result-area').evaluate(element => {
        element.scrollTop = 240;
        return element.scrollTop;
      });
      assert.ok(expectedScroll > 150,
        `Fixture needs a genuinely scrollable command area: requested=240, actual=${expectedScroll}`);
      assert.equal(await page.locator('#command-input').evaluate(element => element === document.activeElement), true);
      await page.evaluate(() => {
        window.__fixture.holdOverview = true;
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await page.waitForFunction(() => window.__fixture.pendingOverview.length === 1);
      await page.evaluate(() => {
        window.__fixture.holdOverview = false;
        window.__fixture.pendingOverview.splice(0).forEach(resolve => resolve());
      });
      await waitIdle(page);
      assert.equal(await page.locator('#command-dialog').evaluate(element => element.open), true);
      assert.equal(await page.locator('#command-input').evaluate(element => element === document.activeElement), true,
        'Silent refresh must keep typing focus in the open command palette');
      assert.equal(await activeCommand(), selectedBefore,
        'Silent refresh must retain the selected available command');
      const actualScroll = await page.locator('.command-result-area').evaluate(element => element.scrollTop);
      assert.ok(Math.abs(actualScroll - expectedScroll) <= 1,
        `Command scrolling must survive silent refresh: expected=${expectedScroll}, actual=${actualScroll}`);
      await page.locator('#command-input').fill('검증 작업');
      assert.equal(await page.locator('#command-results [role="option"]').count(), 75);
      const resetScroll = await page.locator('.command-result-area').evaluate(element => element.scrollTop);
      assert.equal(resetScroll, 0,
        `A new command query must start at its first result: expected=0, actual=${resetScroll}`);
      await closeCommands(page);
      await assertNoModelActions(page);
    });

    assert.deepEqual(hashes(), report.sourceHashes, 'Renderer changed during this run; rerun after edits finish');
    report.passed = report.scenarios.every(item => item.passed) && report.pageErrors.length === 0;
    if (!report.passed) process.exitCode = 1;
  } catch (error) {
    report.passed = false;
    report.error = error.stack;
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
    report.finishedAt = new Date().toISOString();
    save();
    console.log(JSON.stringify({ fixtureRendering: true, passed: report.passed,
      scenarios: report.scenarios.length, failures: report.scenarios.filter(item => !item.passed).map(item => item.name),
      report: reportFile, error: report.error }));
  }
})();
