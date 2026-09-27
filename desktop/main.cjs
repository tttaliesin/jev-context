'use strict';

const { app, BrowserWindow, dialog, clipboard, ipcMain, protocol, net, session, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { BridgeClient } = require('./bridge-client.cjs');
const { diagnosticReport, errorCode } = require('./diagnostics.cjs');
const { normalizeWindowState, loadWindowState, saveWindowState } = require('./window-state.cjs');
const { createSetupController } = require('./setup-client.cjs');
const locale = require('./locales.js');
const t = (key, ...args) => locale.translate(settings?.language, key, ...args);

app.setName('Jev Context');
const appId = 'com.jev.context.manager';
if (process.platform === 'win32') app.setAppUserModelId(appId);
if (process.env.JEV_MANAGER_USER_DATA) app.setPath('userData', path.resolve(process.env.JEV_MANAGER_USER_DATA));
protocol.registerSchemesAsPrivileged([{ scheme: 'jev', privileges: {
  standard: true, secure: true, supportFetchAPI: true,
} }]);

const pages = new Map([
  ['/', 'index.html'], ['/index.html', 'index.html'],
  ['/style.css', 'style.css'], ['/renderer.js', 'renderer.js'], ['/command-menu.js', 'command-menu.js'],
  ['/onboarding.js', 'onboarding.js'],
  ['/i18n.js', 'i18n.js'], ['/locales.js', '../locales.js'],
  ['/jev-mark.svg', '../assets/jev-mark.svg'],
]);
let window;
let bridge;
let settings;
let switching = false;
let diagnosticPreview = null;
let lastOverview = null;
let lastOverviewAt = null;
let lastFailure = null;
const entryURL = 'jev://app/index.html';

function readJSON(filename) {
  try { return JSON.parse(fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, '')); }
  catch { return null; }
}

function loadSettings() {
  const launchFile = app.isPackaged ? path.join(path.dirname(process.execPath), 'launch-config.json')
    : path.join(__dirname, 'launch-config.json');
  const base = path.dirname(launchFile);
  const launch = readJSON(launchFile) || {};
  const projectRoot = path.resolve(base, launch.projectRoot || (app.isPackaged ? '../..' : '..'));
  const saved = readJSON(path.join(app.getPath('userData'), 'settings.json')) || {};
  const override = process.argv.indexOf('--project-config');
  return {
    language: locale.normalize(saved.language),
    projectRoot,
    pythonPath: saved.pythonPath || path.resolve(base, launch.pythonPath || path.join(projectRoot, '.venv', 'Scripts', 'python.exe')),
    configPath: override >= 0 && process.argv[override + 1] ? path.resolve(process.argv[override + 1])
      : saved.configPath || path.resolve(base, launch.configPath || path.join(projectRoot, '.local', 'project.toml')),
  };
}

function saveSettings(next) {
  const filename = path.join(app.getPath('userData'), 'settings.json');
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const pending = `${filename}.pending`;
  fs.writeFileSync(pending, JSON.stringify({ configPath: next.configPath, pythonPath: next.pythonPath, language: next.language }, null, 2), 'utf8');
  fs.renameSync(pending, filename);
}

async function switchProject(next, { persist = true } = {}) {
  if (switching) throw Object.assign(new Error('연결 변경이 진행 중입니다. 완료 후 다시 시도해 주세요.'), { code: 'connection_busy' });
  switching = true;
  const candidate = new BridgeClient(next);
  let snapshot;
  let committed;
  try {
    snapshot = await candidate.request('overview');
    // Language may have changed while the candidate was being checked.
    committed = { ...settings, configPath: next.configPath, pythonPath: next.pythonPath };
    if (persist) saveSettings(committed);
  } catch (error) {
    candidate.close();
    throw error;
  } finally {
    switching = false;
  }
  const previous = bridge;
  settings = committed;
  bridge = candidate;
  lastOverview = snapshot;
  lastOverviewAt = new Date().toISOString();
  previous?.close();
  return snapshot;
}

function client() {
  if (switching) throw Object.assign(new Error('연결 변경이 진행 중입니다. 완료 후 다시 시도해 주세요.'), { code: 'connection_busy' });
  if (!fs.existsSync(settings.pythonPath) || !fs.existsSync(path.join(settings.projectRoot, 'src', 'jev_context', 'desktop_bridge.py')))
    throw new Error('Python 실행 환경을 찾지 못했습니다. 실행 파일 옆 launch-config.json의 프로젝트 경로를 확인해 주세요.');
  if (!fs.existsSync(settings.configPath)) throw new Error('프로젝트 설정을 찾지 못했습니다. ‘프로젝트 열기’에서 project.toml을 선택해 주세요.');
  if (!bridge) bridge = new BridgeClient(settings);
  return bridge;
}

function trusted(event) {
  if (!window || event.sender !== window.webContents
    || event.senderFrame !== window.webContents.mainFrame) return false;
  const url = new URL(event.senderFrame.url);
  url.hash = ''; // The keyboard skip link is an allowed same-document navigation.
  return url.href === entryURL;
}

function register(channel, handler) {
  ipcMain.handle(channel, async (event, payload) => {
    if (!trusted(event)) return { ok: false, error: { code: 'untrusted_sender', message: '허용되지 않은 화면 요청입니다.' } };
    try {
      const value = await handler(payload);
      if (value?.project && value?.engine && value.config_path === settings.configPath) {
        lastOverview = value;
        lastOverviewAt = new Date().toISOString();
      }
      return { ok: true, value };
    } catch (error) {
      if (!channel.startsWith('jev:diagnostics:')) lastFailure = {
        at: new Date().toISOString(), operation: channel.slice(4), code: errorCode(error.code),
      };
      return { ok: false, error: { code: error.code || 'manager_error', message: error.message } };
    }
  });
}

function registerActions() {
  register('jev:diagnostics:get', () => {
    diagnosticPreview = diagnosticReport({ appVersion: app.getVersion(), versions: process.versions,
      platform: process.platform, arch: process.arch, bridge, snapshot: lastOverview,
      observedAt: lastOverviewAt, lastFailure,
      files: { python: fs.existsSync(settings.pythonPath), config: fs.existsSync(settings.configPath),
        module: fs.existsSync(path.join(settings.projectRoot, 'src', 'jev_context', 'desktop_bridge.py')) } });
    return diagnosticPreview;
  });
  register('jev:diagnostics:copy', () => {
    if (!diagnosticPreview) throw new Error('먼저 진단 요약을 열어 주세요.');
    clipboard.writeText(diagnosticPreview);
    return { copied: true };
  });
  register('jev:reconnect', () => {
    if (bridge?.pending.size) throw Object.assign(new Error('요청을 처리 중입니다. 완료 후 다시 연결해 주세요.'), { code: 'connection_busy' });
    return switchProject(settings, { persist: false });
  });
  register('jev:language:get', () => settings.language);
  register('jev:language:set', language => {
    if (!['ko', 'en'].includes(language)) throw new Error('지원하지 않는 언어입니다.');
    const next = { ...settings, language };
    saveSettings(next);
    settings = next;
    return language;
  });
  const setup = createSetupController({ dialog, clipboard, getWindow: () => window, getSettings: () => settings,
    adopt: switchProject });
  register('jev:setup', payload => {
    if (!payload || typeof payload.action !== 'string' || Object.keys(payload).some(key => !['action', 'params'].includes(key))) throw new Error('잘못된 설정 요청입니다.');
    return setup.run(payload.action, payload.params);
  });
  register('jev:overview', options => {
    const params = {};
    if (options !== undefined && (options === null || typeof options !== 'object' || Array.isArray(options))) throw new Error('잘못된 목록 요청입니다.');
    if (options?.cursor !== undefined) {
      if (typeof options.cursor !== 'string' || options.cursor.length > 2048) throw new Error('잘못된 목록 위치입니다.');
      params.cursor = options.cursor;
    }
    if (options?.limit !== undefined) {
      if (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 50) throw new Error('잘못된 목록 크기입니다.');
      params.limit = options.limit;
    }
    return client().request('overview', params);
  });
  register('jev:work', workId => {
    if (typeof workId !== 'string' || !/^work-[a-zA-Z0-9-]{1,100}$/.test(workId)) throw new Error('잘못된 작업 ID입니다.');
    return client().request('work_open', { work_id: workId });
  });
  register('jev:prepare', () => client().request('model_prepare'));
  register('jev:stop', () => client().request('model_stop'));
  register('jev:connection', () => client().request('connection_check'));
  register('jev:project', async () => {
    const selected = await dialog.showOpenDialog(window, {
      title: t('프로젝트 설정 열기'), defaultPath: settings.configPath,
      properties: ['openFile'], filters: [{ name: t('Jev 프로젝트 설정'), extensions: ['toml'] }],
    });
    if (selected.canceled || !selected.filePaths[0]) return null;
    const next = { ...settings, configPath: path.resolve(selected.filePaths[0]) };
    return switchProject(next);
  });
}

async function createWindow() {
  const statePath = path.join(app.getPath('userData'), 'window-state.json');
  const state = loadWindowState(statePath, screen.getAllDisplays(), screen.getPrimaryDisplay().id);
  const hidden = Boolean(process.env.JEV_MANAGER_TEST_HIDDEN);
  let maximized = state.maximized;
  window = new BrowserWindow({ ...state.normalBounds, minWidth: state.minWidth, minHeight: state.minHeight,
    title: 'Jev Context', backgroundColor: '#101210', show: false,
    icon: path.join(__dirname, 'assets', process.platform === 'win32' ? 'jev-icon.ico' : 'jev-icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true, spellcheck: false } });
  window.removeMenu();
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  const fitToDisplays = () => {
    if (window.isDestroyed()) return;
    const fitted = normalizeWindowState({ normalBounds: window.getNormalBounds() },
      screen.getAllDisplays(), screen.getPrimaryDisplay().id);
    window.setMinimumSize(fitted.minWidth, fitted.minHeight);
    if (!window.isMaximized() && !window.isMinimized()) window.setBounds(fitted.normalBounds);
  };
  window.on('maximize', () => { maximized = true; });
  window.on('unmaximize', () => { maximized = false; setImmediate(fitToDisplays); });
  screen.on('display-removed', fitToDisplays);
  screen.on('display-metrics-changed', fitToDisplays);
  window.on('close', () => {
    saveWindowState(statePath, {
      normalBounds: window.getNormalBounds(),
      maximized: hidden || window.isMinimized() ? maximized : window.isMaximized(),
    });
  });
  window.on('closed', () => {
    screen.removeListener('display-removed', fitToDisplays);
    screen.removeListener('display-metrics-changed', fitToDisplays);
  });
  window.once('ready-to-show', () => {
    // maximize() also shows a hidden Electron window. Preserve the saved intent in
    // test mode without displaying a native window or overwriting it on close.
    if (!hidden) { if (maximized) window.maximize(); window.show(); }
  });
  await window.loadURL(entryURL);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    settings = loadSettings();
    protocol.handle('jev', request => {
      const url = new URL(request.url);
      const file = url.host === 'app' && !url.search && !url.hash && pages.get(url.pathname);
      return file ? net.fetch(pathToFileURL(path.join(__dirname, 'renderer', file)).href)
        : new Response('Not found', { status: 404 });
    });
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    registerActions();
    await createWindow();
  }).catch(error => { dialog.showErrorBox(t('Jev Context 실행 오류'), locale.message(settings?.language, error.message)); app.quit(); });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => bridge?.close());
}
