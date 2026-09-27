'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const filename = path.resolve(__dirname, '../desktop/main.cjs');
const source = fs.readFileSync(filename, 'utf8');
const localRequire = createRequire(filename);

function harness(t, failure) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jev-switch-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const savedPath = path.join(directory, 'settings.json');
  const old = { language: 'ko', configPath: path.join(directory, 'a.toml'), pythonPath: 'python-a', projectRoot: directory };
  const next = { ...old, configPath: path.join(directory, 'b.toml'), pythonPath: 'python-b' };
  const serialized = JSON.stringify(old);
  fs.writeFileSync(savedPath, serialized);
  const previous = { pending: new Map(), closed: false, close() { this.closed = true; } };
  const candidates = [];
  const actions = new Map();
  let validate = async () => ({ project: 'b' });
  let adopt;
  let clipboardText = null;
  const context = vm.createContext({
    process, URL, console, __dirname: path.dirname(filename),
    require(name) {
      if (name === 'electron') return {
        app: { setName() {}, setAppUserModelId() {}, setPath() {}, getPath: () => directory, getVersion: () => '0.7.0',
          requestSingleInstanceLock: () => false, quit() {} },
        protocol: { registerSchemesAsPrivileged() {} },
        clipboard: { writeText: value => { clipboardText = value; } },
        dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: [next.configPath] }) },
      };
      if (name === 'node:fs') return { ...fs,
        writeFileSync(...args) { if (failure === 'write') throw new Error('injected write failure'); return fs.writeFileSync(...args); },
        renameSync(...args) { if (failure === 'rename') throw new Error('injected rename failure'); return fs.renameSync(...args); },
      };
      if (name === './bridge-client.cjs') return { BridgeClient: class {
        constructor(settings) { this.settings = settings; this.closed = false; candidates.push(this); }
        request(method) { assert.equal(method, 'overview'); return validate(); }
        close() { this.closed = true; }
      } };
      if (name === './setup-client.cjs') return { createSetupController(options) {
        adopt = options.adopt;
        return { run: (_action, params) => adopt(params) };
      } };
      return localRequire(name);
    },
    initial: old, previous, actions,
  });
  // Exercise the actual IPC handlers and onboarding callback without booting a window.
  vm.runInContext(source + '\nsettings = initial; bridge = previous; register = (name, handler) => actions.set(name, handler); registerActions();', context, { filename });
  return {
    old, next, previous, candidates, savedPath, serialized,
    state: () => vm.runInContext('({settings, bridge})', context),
    validate: fn => { validate = fn; },
    reconnect: () => actions.get('jev:reconnect')(),
    diagnostics: () => actions.get('jev:diagnostics:get')(),
    copyDiagnostics: () => actions.get('jev:diagnostics:copy')(),
    clipboard: () => clipboardText,
    language: () => actions.get('jev:language:set')('en'),
    switch: route => route === 'project' ? actions.get('jev:project')()
      : actions.get('jev:setup')({ action: 'create', params: next }),
  };
}

test('reconnect validates a new bridge without rewriting settings', async t => {
  const h = harness(t, 'write');
  await h.reconnect();
  assert.equal(h.previous.closed, true);
  assert.equal(h.state().bridge, h.candidates[0]);
  assert.equal(fs.readFileSync(h.savedPath, 'utf8'), h.serialized);
});

test('failed reconnect keeps the existing bridge and can be retried', async t => {
  const h = harness(t);
  h.validate(async () => { throw new Error('unavailable'); });
  await assert.rejects(h.reconnect(), /unavailable/);
  assert.equal(h.previous.closed, false);
  assert.equal(h.state().bridge, h.previous);
  assert.equal(h.candidates[0].closed, true);
  h.validate(async () => ({ project: 'a' }));
  await h.reconnect();
  assert.equal(h.state().bridge, h.candidates[1]);
});

test('reconnect never interrupts pending requests or another switch', async t => {
  const h = harness(t);
  h.previous.pending.set('request', {});
  assert.throws(h.reconnect, error => error.code === 'connection_busy');
  assert.equal(h.candidates.length, 0);
  h.previous.pending.clear();
  let resolve;
  h.validate(() => new Promise(done => { resolve = done; }));
  const switching = h.reconnect();
  await assert.rejects(h.reconnect(), error => error.code === 'connection_busy');
  resolve({ project: 'a' });
  await switching;
  assert.equal(h.candidates.length, 1);
});

test('diagnostics work without a Python query and copy the exact preview', t => {
  const h = harness(t);
  assert.throws(h.copyDiagnostics, /먼저/);
  const preview = h.diagnostics();
  assert.equal(h.candidates.length, 0);
  h.previous.pending.set('later-change', {});
  h.copyDiagnostics();
  assert.equal(h.clipboard(), preview);
  assert.equal(JSON.parse(preview).manager.pending_requests, 0);
  assert.doesNotMatch(preview, /python-a|a\.toml/);
});

for (const route of ['project', 'onboarding']) {
  for (const failure of ['write', 'rename', 'validation']) {
    test(`${route}: ${failure} failure preserves current connection and saved settings`, async t => {
      const h = harness(t, failure);
      if (failure === 'validation') h.validate(async () => { throw new Error('injected validation failure'); });
      await assert.rejects(h.switch(route), new RegExp(`injected ${failure} failure`));
      assert.equal(h.state().settings, h.old);
      assert.equal(h.state().bridge, h.previous);
      assert.equal(h.previous.closed, false);
      assert.equal(h.candidates[0].closed, true);
      assert.equal(fs.readFileSync(h.savedPath, 'utf8'), h.serialized);
    });
  }
  test(`${route}: commits only after validation and preserves a concurrent language change`, async t => {
    const h = harness(t);
    let validated;
    h.validate(() => new Promise(resolve => { validated = resolve; }));
    const switching = h.switch(route);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.state().bridge, h.previous);
    assert.equal(fs.readFileSync(h.savedPath, 'utf8'), h.serialized);
    h.language();
    validated({ project: 'b' });
    const result = await switching;
    assert.equal(result.project, 'b');
    assert.equal(h.state().bridge, h.candidates[0]);
    assert.equal(h.previous.closed, true);
    assert.equal(h.candidates[0].closed, false);
    const saved = JSON.parse(fs.readFileSync(h.savedPath, 'utf8'));
    assert.equal(saved.configPath, h.next.configPath);
    assert.equal(saved.pythonPath, route === 'project' ? h.old.pythonPath : h.next.pythonPath);
    assert.equal(saved.language, 'en');
    assert.equal(h.state().settings.language, 'en');
  });
}
