'use strict';

const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const locale = require('./locales.js');

function requestSetup(settings, action, values = {}) {
  if (!fs.existsSync(settings.pythonPath)) return Promise.reject(new Error('Python 실행 환경을 찾지 못했습니다. Python 선택에서 Jev 의존성이 설치된 실행기를 선택하세요.'));
  return new Promise((resolve, reject) => {
    const child = spawn(settings.pythonPath, ['-X', 'utf8', '-m', 'jev_context.onboarding'], {
      cwd: settings.projectRoot, shell: false, windowsHide: true,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONPATH: path.join(settings.projectRoot, 'src') },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let output = '', error = '', settled = false;
    const finish = (failure, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (failure) reject(failure); else resolve(value);
    };
    const timer = setTimeout(() => {
      child.kill(); finish(new Error('설정 작업 응답이 지연됐습니다. 상태를 다시 확인하세요. 중단된 설치는 설정 되돌리기로 복구할 수 있습니다.'));
    }, 30000);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', data => {
      output += data;
      if (Buffer.byteLength(output) > 1024 * 1024) { child.kill(); finish(new Error('설정 응답 크기를 초과했습니다.')); }
    });
    child.stderr.on('data', data => { error = (error + data).slice(-1500); });
    child.on('error', err => finish(new Error(`Python을 시작하지 못했습니다: ${err.message}`)));
    child.stdin.on('error', err => finish(err));
    child.on('exit', () => {
      try {
        const response = JSON.parse(output);
        if (response.error) finish(Object.assign(new Error(response.error.message), { code: response.error.code }));
        else finish(null, response.result);
      } catch { finish(new Error(`설정 환경을 읽지 못했습니다. Jev 의존성이 설치된 Python 3.12를 선택하세요. ${error}`)); }
    });
    child.stdin.end(JSON.stringify({ action, config_path: settings.configPath, expected_root: settings.selectedRoot || null, ...values }));
  });
}

function createSetupController({ dialog, clipboard, getWindow, getSettings, adopt }) {
  const t = key => locale.translate(getSettings().language, key);
  let draft = null, busy = false, lastPrompt = '';
  async function run(action, params = {}) {
    if (busy) throw new Error('설정 작업이 진행 중입니다. 잠시 기다려 주세요.');
    if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('잘못된 설정 요청입니다.');
    const permitted = { status: [], begin: [], folder: [], python: [], create: ['allowed_paths'],
      preview: [], install: ['fingerprint'], restore: [], challenge: [], copy: [] };
    if (!Object.hasOwn(permitted, action) || Object.keys(params).some(key => !permitted[action].includes(key))) throw new Error('허용되지 않은 설정 요청입니다.');
    busy = true;
    const requestedAction = action, previousDraft = draft;
    try {
      if (!draft || action === 'begin') draft = { ...getSettings() };
      if (action === 'copy') { if (!lastPrompt) throw new Error('먼저 확인 문구를 만들어 주세요.'); clipboard.writeText(locale.prompt(getSettings().language, lastPrompt)); return { copied: true }; }
      if (action === 'folder' || action === 'python') {
        const choice = await dialog.showOpenDialog(getWindow(), action === 'folder'
          ? { title: t('작업할 프로젝트 폴더 선택'), properties: ['openDirectory'] }
          : { title: t('Jev 환경의 Python 3.12 선택'), properties: ['openFile'], filters: [{ name: 'Python', extensions: ['exe'] }] });
        if (choice.canceled || !choice.filePaths[0]) return null;
        if (action === 'folder') {
          draft = { ...draft, selectedRoot: path.resolve(choice.filePaths[0]), configPath: path.join(path.resolve(choice.filePaths[0]), '.local', 'project.toml') };
          lastPrompt = '';
        } else draft.pythonPath = path.resolve(choice.filePaths[0]);
        action = 'status';
      }
      const existing = action === 'create' && fs.existsSync(draft.configPath);
      const data = action === 'create'
        ? { project_root: draft.selectedRoot || (await requestSetup(draft, 'status')).project_root, allowed_paths: params.allowed_paths }
        : params;
      if (action === 'create' && !data.project_root) throw new Error('먼저 프로젝트 폴더를 선택하세요.');
      const result = await requestSetup(draft, existing ? 'initialize' : action === 'begin' ? 'status' : action, data);
      if (action === 'create') { draft.configPath = result.config_path; await adopt(draft); }
      if (['install', 'restore'].includes(action)) lastPrompt = '';
      if (result.confirmation?.prompt) lastPrompt = result.confirmation.prompt;
      return { ...result, selected_root: draft.selectedRoot || result.project_root || null };
    } catch (error) {
      if (['folder', 'python'].includes(requestedAction)) draft = previousDraft;
      throw error;
    } finally { busy = false; }
  }
  return { run };
}

module.exports = { requestSetup, createSetupController };
