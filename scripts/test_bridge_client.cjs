'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');
const filename = path.resolve(__dirname, '../desktop/bridge-client.cjs');

function load(spawnChild) {
  const context = vm.createContext({
    module: { exports: {} }, process, Buffer, setTimeout, clearTimeout,
    require: name => name === 'node:child_process' ? { spawn: spawnChild } : require(name),
  });
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  return context.module.exports.BridgeClient;
}

function fakeChild() {
  const stream = () => Object.assign(new EventEmitter(), { setEncoding() {} });
  return Object.assign(new EventEmitter(), {
    stdout: stream(), stderr: stream(), stdin: Object.assign(stream(), { write() {}, end() {} }),
    killed: false, exitCode: null, kill() { this.killed = true; },
  });
}

for (const event of ['error', 'exit', 'close', 'stdin', 'malformed', 'oversize']) {
  test(`${event}: retry uses a new child and ignores retired process events`, async t => {
    const children = [];
    const Client = load(() => { const child = fakeChild(); children.push(child); return child; });
    const client = new Client({ pythonPath: 'python', projectRoot: __dirname, configPath: 'project.toml' });
    t.after(() => client.close());
    const first = client.request('overview');
    const rejected = assert.rejects(first);
    const old = children[0];
    if (event === 'stdin') old.stdin.emit('error', new Error('broken pipe'));
    else if (event === 'malformed') old.stdout.emit('data', 'invalid\n');
    else if (event === 'oversize') old.stdout.emit('data', 'x'.repeat(2 * 1024 * 1024 + 1));
    else if (event === 'error') old.emit('error', new Error('ENOENT'));
    else old.emit(event, 1, null);
    await rejected;
    assert.equal(client.child, null);
    const second = client.request('overview');
    assert.equal(children.length, 2);
    const current = children[1];
    for (const name of ['error', 'exit', 'close']) old.emit(name, new Error('late failure'));
    old.stdin.emit('error', new Error('late pipe failure'));
    old.stderr.emit('data', 'obsolete error');
    old.stdout.emit('data', JSON.stringify({ id: '2', result: 'obsolete result' }) + '\n');
    assert.equal(client.child, current);
    assert.equal(client.pending.size, 1);
    assert.equal(client.stderr, '');
    current.stdout.emit('data', JSON.stringify({ id: '2', result: 'recovered' }) + '\n');
    assert.equal(await second, 'recovered');
  });
}

test('real ENOENT can recover immediately without waiting for a timeout', { timeout: 5000 }, async t => {
  const children = [];
  const Client = load((executable, _args, options) => {
    // Use a portable JSON-lines echo process after the genuine spawn failure.
    const child = spawn(executable, ['-e',
      'require("node:readline").createInterface({input:process.stdin}).on("line",line=>{const r=JSON.parse(line);console.log(JSON.stringify({id:r.id,result:"recovered"}));})'], options);
    children.push(child);
    return child;
  });
  const settings = { pythonPath: path.join(__dirname, 'nonexistent-jev-python.exe'), projectRoot: __dirname, configPath: 'unused' };
  const client = new Client(settings);
  t.after(() => client.close());
  await assert.rejects(client.request('overview'), /ENOENT/);
  settings.pythonPath = process.execPath;
  assert.equal(await client.request('overview'), 'recovered');
  assert.equal(children.length, 2);
  assert.notEqual(children[0], children[1]);
});
