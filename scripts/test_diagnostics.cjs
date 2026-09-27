'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { diagnosticReport } = require('../desktop/diagnostics.cjs');

test('only explicit diagnostic fields are included; raw state cannot leak', () => {
  const privateText = 'PRIVATE-user-title-token-path';
  const report = diagnosticReport({ appVersion: '0.7.0', versions: { electron: '44.4.5', node: '24.0.0' },
    platform: 'win32', arch: 'x64', files: { python: true, config: true, module: true, raw: privateText },
    bridge: { child: { pid: 12 }, pending: new Map(), stderr: privateText },
    observedAt: '2026-09-27T13:00:00.000Z',
    snapshot: { project: { project_root: privateText }, works: { items: [{ title: privateText }] },
      engine: { state: 'idle', profile_error: privateText, idle_timeout_seconds: 120 },
      connection: { mcp_stdio: 'verified', error: privateText }, workspace_error: { code: privateText, message: privateText } },
    lastFailure: { operation: 'overview', code: 'bridge_timeout', message: privateText, at: '2026-09-27T13:01:00.000Z' },
  });
  assert.ok(!report.includes(privateText));
  const data = JSON.parse(report);
  assert.equal(data.last_failure.code, 'bridge_timeout');
  assert.equal(data.last_observed.engine, 'idle');
  assert.equal(data.last_observed.workspace_error, 'manager_error');
  assert.equal(data.manager.snapshot_is_current, false);
  assert.equal(data.last_observed.host_session, 'not_observed');
});

test('malicious strings in otherwise allowed scalar fields fall back safely', () => {
  const value = '/private/user/token';
  const report = diagnosticReport({ appVersion: value, versions: { electron: value }, platform: value,
    arch: value, observedAt: value, snapshot: { engine: { state: value, idle_timeout_seconds: value },
      connection: { mcp_stdio: value, checked_at: value } }, lastFailure: { operation: value, at: value, code: value } });
  assert.ok(!report.includes(value));
  assert.equal(JSON.parse(report).app.version, 'unknown');
});
