'use strict';

const knownCodes = new Set(['ENOENT', 'EACCES', 'EPERM', 'EISDIR', 'ENOSPC', 'EBUSY',
  'bridge_start_failed', 'bridge_exited', 'bridge_io_error', 'bridge_invalid_response',
  'bridge_timeout', 'connection_busy', 'manager_error', 'engine_unavailable', 'storage_error',
  'invalid_argument', 'cursor_stale', 'read_only', 'project_changed']);
const choose = (value, choices) => choices.includes(value) ? value : 'unknown';
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const date = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT[\d:.]+Z$/.test(value) ? value : null;
const version = value => typeof value === 'string' && /^[\d.]+(?:[-+][a-zA-Z0-9.-]+)?$/.test(value) ? value : 'unknown';
const errorCode = value => knownCodes.has(value) ? value : 'manager_error';

function diagnosticReport({ appVersion, versions = {}, platform, arch, files = {}, bridge,
  snapshot, observedAt, lastFailure, now = new Date().toISOString() }) {
  const engine = snapshot?.engine || {};
  const connection = snapshot?.connection || {};
  // Construct an allowlist, never redact arbitrary logs or spread project data.
  return JSON.stringify({
    format: 'jev-diagnostics-v1', generated_at: now,
    app: { version: version(appVersion), electron: version(versions.electron), node: version(versions.node),
      platform: choose(platform, ['win32', 'darwin', 'linux']), arch: choose(arch, ['x64', 'arm64', 'ia32']) },
    files: { python_exists: files.python === true, config_exists: files.config === true, bridge_module_exists: files.module === true },
    manager: { process_present: !!bridge?.child, pending_requests: count(bridge?.pending?.size),
      last_overview_at: date(observedAt), snapshot_is_current: false },
    last_observed: snapshot ? {
      engine: choose(engine.state, ['idle', 'disabled', 'preparing', 'shadow', 'active', 'unavailable']),
      worker_present: !!engine.worker_pid, idle_timeout_seconds: count(engine.idle_timeout_seconds),
      mcp_transport: choose(connection.mcp_stdio, ['verified', 'failed', 'not_checked']),
      mcp_checked_at: date(connection.checked_at), host_session: 'not_observed',
      workspace_error: snapshot.workspace_error ? errorCode(snapshot.workspace_error.code) : null,
    } : null,
    last_failure: lastFailure ? {
      at: date(lastFailure.at), operation: choose(lastFailure.operation,
        ['overview', 'work', 'prepare', 'stop', 'connection', 'reconnect', 'project', 'setup', 'language:set', 'language:get']),
      code: errorCode(lastFailure.code),
    } : null,
    privacy: 'No paths, work content, environment variables or raw logs. No upload.',
    scope: 'Last observed state only. App reconnect does not restart a host MCP server or shared model.',
  }, null, 2);
}

module.exports = { diagnosticReport, errorCode };
