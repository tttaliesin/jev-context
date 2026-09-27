'use strict';

const { spawn } = require('node:child_process');
const path = require('node:path');

class BridgeClient {
  constructor(settings) {
    this.settings = settings;
    this.child = null;
    this.pending = new Map();
    this.counter = 0;
    this.stderr = '';
    this.closed = false;
  }

  start() {
    if (this.closed) throw new Error('프로젝트 연결이 닫혔습니다.');
    if (this.child) return;
    const { pythonPath, projectRoot, configPath } = this.settings;
    const env = { ...process.env, PYTHONUTF8: '1', PYTHONPATH: path.join(projectRoot, 'src') };
    // Only this owned, fixed Python module is executable through the bridge.
    const child = spawn(pythonPath, ['-u', '-X', 'utf8', '-m', 'jev_context.desktop_bridge',
      '--config', configPath], { cwd: projectRoot, env, shell: false, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    this.stderr = '';
    // A retired process may emit close/exit/stream events after a retry starts.
    const retire = error => {
      if (this.child !== child) return;
      this.child = null;
      this.fail(error);
      child.kill();
    };
    let buffer = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', data => { if (this.child === child) this.stderr = (this.stderr + data).slice(-4000); });
    child.stdout.on('data', chunk => {
      if (this.child !== child) return;
      buffer += chunk;
      if (Buffer.byteLength(buffer, 'utf8') > 2 * 1024 * 1024) {
        retire(Object.assign(new Error('서버 응답 크기 제한을 초과했습니다.'), { code: 'bridge_invalid_response' }));
        return;
      }
      let split;
      while ((split = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, split).trim();
        buffer = buffer.slice(split + 1);
        if (!line) continue;
        let response;
        try { response = JSON.parse(line); }
        catch { retire(Object.assign(new Error('서버가 올바른 응답을 보내지 않았습니다.'), { code: 'bridge_invalid_response' })); return; }
        const pending = this.pending.get(response.id);
        if (!pending) continue;
        this.pending.delete(response.id);
        clearTimeout(pending.timer);
        if (response.error) {
          const error = new Error(response.error.message || '요청을 처리하지 못했습니다.');
          error.code = response.error.code;
          pending.reject(error);
        } else pending.resolve(response.result);
      }
    });
    child.on('error', error => retire(Object.assign(new Error(`Python 서버를 시작하지 못했습니다: ${error.message}`), { code: 'bridge_start_failed' })));
    const exited = (code, signal) => retire(Object.assign(new Error(`프로젝트 서버가 종료됐습니다 (${signal || code}). ${this.stderr.trim()}`), { code: 'bridge_exited' }));
    child.on('exit', exited);
    child.on('close', exited);
    child.stdin.on('error', error => retire(Object.assign(new Error(`서버 연결 오류: ${error.message}`), { code: 'bridge_io_error' })));
  }

  request(method, params = {}) {
    this.start();
    const id = String(++this.counter);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Object.assign(new Error('서버 응답을 기다리는 시간이 초과됐습니다. 상태를 다시 확인해 주세요.'), { code: 'bridge_timeout' }));
      }, method === 'connection_check' ? 30000 : 12000);
      this.pending.set(id, { resolve, reject, timer });
      try { this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n'); }
      catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(error);
      }
    });
  }

  fail(error) {
    for (const entry of this.pending.values()) { clearTimeout(entry.timer); entry.reject(error); }
    this.pending.clear();
  }

  close() {
    this.closed = true;
    this.fail(new Error('프로젝트 연결을 전환했습니다.'));
    const child = this.child;
    this.child = null;
    if (!child) return;
    child.stdin.end();
    const timer = setTimeout(() => { if (child.exitCode === null) child.kill(); }, 1500);
    timer.unref();
    child.once('exit', () => clearTimeout(timer));
    child.once('close', () => clearTimeout(timer));
    // The shared model broker is deliberately not owned by this GUI connection.
  }
}

module.exports = { BridgeClient };
