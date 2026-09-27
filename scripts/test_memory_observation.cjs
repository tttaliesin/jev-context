'use strict';
// Real MCP -> sidecar -> Python bridge -> packaged Electron. No model or user DB.
const { _electron } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local/memory-validation', String(Date.now()));
const project = path.join(output, 'project');
const config = path.join(output, 'project.toml');
fs.mkdirSync(project, { recursive: true });
const seed = String.raw`
import json, sys, anyio
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
from jev_context.cli import write_config
from jev_context.policy import Config
from jev_context.service import Service
from jev_context.common import uid
path = write_config(sys.argv[1], sys.argv[2], sys.argv[3], ['*.md'])
path.write_text('contract_version = "2.0"\n' + path.read_text('utf-8'), encoding='utf-8')
service = Service(Config.load(path))
def call(name, **args):
    return service.call(name, dict(contract_version='2.0', request_id=uid('req'), mutation_id=uid('mut'), **args))
created = call('work_open', create=dict(title='실제 기억 복원 검증', goal='변경된 제약을 보존',
    scope=dict(mode='design', constraints=['배포 금지']), origin=dict(quote='설계만 진행')))
wid = created['data']['work_id']
call('work_record', work_id=wid, expected_revision=1, event=dict(kind='progress_reported',
    summary='저장된 현재 진행', next_actions=['원문 확인'], source_refs=[]))
service.close()
async def run():
    params = StdioServerParameters(command=sys.executable, args=['-m','jev_context','serve','--config',str(path)])
    async with stdio_client(params) as streams, ClientSession(*streams, client_info=Implementation(name='codex-validation', version='1')) as client:
        await client.initialize()
        await client.call_tool('workspace_status', dict(contract_version='2.0', request_id='status'))
        result = await client.call_tool('context_prepare', dict(contract_version='2.0', request_id='context', work_id=wid,
            query='현재 목표와 제약', judge_mode='off', budget_bytes=18000))
        assert result.structuredContent['outcome'] == 'ok', result
        print(json.dumps(dict(work_id=wid, packet_id=result.structuredContent['data']['packet_id'])))
anyio.run(run)
`;
const generated = JSON.parse(execFileSync(path.join(root, '.venv/Scripts/python.exe'),
  ['-X', 'utf8', '-c', seed, config, project, path.join(output, 'data')],
  { cwd: root, windowsHide: true, encoding: 'utf8', env: { ...process.env, PYTHONPATH: path.join(root, 'src') } }));
const receiptPath = path.join(project, '.local/jev-runtime/observation.json');
const before = fs.readFileSync(receiptPath, 'utf8');
const report = { output, generated, pageErrors: [], observations: [], modelRequested: false };
const save = () => fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
let app;
(async () => {
  try {
    app = await _electron.launch({ executablePath: path.join(root, 'dist/JevContext/JevContext.exe'),
      args: ['--project-config', config], cwd: root,
      env: { ...process.env, JEV_MANAGER_USER_DATA: path.join(output, 'user-data'), JEV_MANAGER_TEST_HIDDEN: '1' } });
    const page = await app.firstWindow();
    report.observations.push('packaged Electron opened'); save();
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => report.pageErrors.push(error.message));
    await page.waitForFunction(() => document.getElementById('project-name')?.textContent === 'project');
    if (await page.locator('#setup-dialog').isVisible()) {
      await page.waitForFunction(() => !document.getElementById('setup-close').disabled);
      await page.locator('#setup-close').click();
    }
    await page.waitForFunction(() => document.getElementById('work-title').textContent === '실제 기억 복원 검증');
    await page.locator('#connection-details > summary').click();
    assert.match(await page.locator('#connection-runtime').textContent(), /설치 파일과 일치/);
    assert.match(await page.locator('#connection-host-runtime').textContent(), /설치 파일과 일치/);
    assert.match(await page.locator('#work-restore').textContent(), /저장된 작업 버전과 일치/);
    assert.equal(await page.locator('#connection-desktop').textContent(), '직접 관측 안 함');
    assert.match(await page.locator('#work-constraints').textContent(), /배포 금지/);
    report.observations.push('real MCP response metadata visible; no current-host-use claim');
    save();
    await page.locator('#check-connection').click();
    await page.waitForFunction(() => document.getElementById('connection-mcp').textContent === '통신 확인');
    assert.equal(fs.readFileSync(receiptPath, 'utf8'), before);
    report.observations.push('independent health probe preserves host observation');
    save();
    await page.locator('.topbar [data-language-select]').selectOption('en');
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    assert.match(await page.locator('#connection-memory').textContent(), /Response prepared/);
    assert.match(await page.locator('#work-restore').textContent(), /Matches stored work revision/);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1080, 900));
    const capture = await app.evaluate(async ({ BrowserWindow }) =>
      (await BrowserWindow.getAllWindows()[0].capturePage()).toPNG().toString('base64'));
    fs.writeFileSync(path.join(output, 'memory-en.png'), Buffer.from(capture, 'base64'));
    const snapshot = await page.evaluate(() => window.jev.overview());
    assert.equal(snapshot.engine.worker_pid, null);
    assert.equal(snapshot.engine.broker_pid, null);
    assert.equal(snapshot.connection.host_observation.last_context.packet_id, generated.packet_id);
    report.observations.push('English UI; real packet ID; no worker or broker');
    assert.deepEqual(report.pageErrors, []);
    report.passed = true;
  } catch (error) { report.passed = false; report.error = error.stack; process.exitCode = 1; }
  finally {
    save();
    if (app) {
      const timeout = setTimeout(() => app.process().kill(), 10000);
      try { await app.close(); } finally { clearTimeout(timeout); }
    }
    console.log(JSON.stringify(report, null, 2));
  }
})();
