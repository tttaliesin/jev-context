'use strict';

window.createJevOnboarding = ({ refresh, selectExisting, beforeOpen }) => {
  const $ = id => document.getElementById(id);
  const api = window.jev;
  const i18n = window.jevI18n;
  const { t } = i18n;
  let step = 1, state = null, plan = null, busy = false, timer = null, returnFocus = null;
  let restoring = false, transport = false;
  let pollingRequest = null;
  const text = (id, value) => { $(id).textContent = value; };
  function render() {
    for (let n = 1; n <= 4; n++) {
      $(`setup-step-${n}`).hidden = step !== n;
      const item = $('setup-steps').children[n - 1];
      item.classList.toggle('active', n === step);
      if (n === step) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    }
    text('setup-title', [t("작업할 프로젝트를 연결하세요"), t("변경 내용을 확인하세요"), t("Codex에서 호출을 확인하세요"), t("이제 작업을 이어 가세요")][step - 1]);
    text('setup-project', state?.selected_root || state?.project_root || t("아직 선택하지 않음"));
    text('setup-environment', state?.environment ? `Python ${state.environment.version} · ${state.project_ready ? t("기존 프로젝트 준비됨") : t("새 프로젝트 준비 가능")}` : t("Python 3.12와 Jev 의존성이 설치된 실행 환경이 필요합니다."));
    $('setup-allow').hidden = Boolean(state?.project_ready);
    $('setup-back').hidden = step === 1 || step === 4;
    $('setup-restore').hidden = !state?.can_restore;
    text('setup-restore', restoring ? t("백업으로 되돌리기 확인") : t("설정 되돌리기"));
    text('setup-next', [t("프로젝트 준비"), plan?.installed ? t("연결 확인으로") : t("설정 적용"), state?.confirmation?.state === 'received' ? t("확인 완료 · 계속") : t("확인은 나중에 · 계속"), t("작업 화면으로")][step - 1]);
    $('setup-later').hidden = step === 4;
    for (const button of $('setup-dialog').querySelectorAll('button')) button.disabled = busy;
    $('setup-next').disabled = busy || (step === 1 && !state?.selected_root && !state?.project_root) || (step === 2 && !plan);
    $('setup-copy').disabled = busy || !state?.confirmation?.prompt || !['waiting', 'received'].includes(state.confirmation.state);
    text('setup-config-status', state?.installed ? t("설정 적용됨") : t("설정 필요"));
    text('setup-transport', transport ? t("통신 확인") : t("아직 점검하지 않음"));
    $('setup-config-status').classList.toggle('confirmed', Boolean(state?.installed));
    $('setup-transport').classList.toggle('confirmed', transport);
    const confirmation = state?.confirmation || {};
    const labels = { waiting: t("확인 요청 기다리는 중"), received: t("요청 수신 · {0}", confirmation.received_at ? new Intl.DateTimeFormat(i18n.locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(confirmation.received_at)) : ''), expired: t("만료됨 · 확인 문구를 다시 만드세요"), settings_changed: t("설정 변경됨 · 다시 확인하세요") };
    text('setup-receipt', labels[confirmation.state] || t("아직 받지 않음"));
    $('setup-receipt').classList.toggle('confirmed', confirmation.state === 'received');
    $('setup-prompt').value = ['waiting', 'received'].includes(confirmation.state) ? i18n.prompt(confirmation.prompt) : '';
    text('setup-result-title', confirmation.state === 'received' ? t("Codex 확인 요청을 받았습니다") : t("프로젝트 설정을 마쳤습니다"));
    text('setup-result', confirmation.state === 'received' ? t("설정된 서버에 확인 요청이 도착했습니다. 저장된 작업 화면으로 이동합니다.") : t("Codex의 실제 호출은 아직 확인되지 않았습니다. 상단 Codex 연결에서 이어서 확인할 수 있습니다."));
  }
  function showPlan(value) {
    plan = value;
    $('setup-files').replaceChildren(...value.files.map(file => {
      const li = document.createElement('li');
      const label = document.createElement('strong'); label.textContent = { keep: t("유지"), create: t("새로 설치"), update: t("백업 후 갱신") }[file.action];
      const name = document.createElement('span'); name.textContent = file.path;
      li.append(label, name); return li;
    }));
    text('setup-entry', value.entry_preview); text('setup-skill', value.skill_preview);
  }
  async function run(message, operation) {
    if (busy) return;
    busy = true; $('setup-error').hidden = true; text('setup-progress', message); render();
    try { if (pollingRequest) await pollingRequest; await operation(); text('setup-progress', ''); }
    catch (error) { text('setup-error', i18n.message(error.message || String(error))); $('setup-error').hidden = false; text('setup-progress', t("같은 단계에서 다시 시도할 수 있습니다.")); }
    finally { busy = false; render(); }
  }
  async function load() {
    state = await api.setup('status');
    if (state.configuration_error) throw new Error(state.configuration_error);
  }
  function polling() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!$('setup-dialog').open) return;
      if (!busy && step === 3 && !document.hidden && state?.confirmation?.state === 'waiting') {
        pollingRequest = api.setup('status').then(value => {
          state = value;
          if (state.confirmation?.state !== 'waiting') render();
        }).catch(() => { text('setup-receipt', t("상태 조회 지연 · 잠시 후 다시 확인합니다")); }).finally(() => { pollingRequest = null; });
        await pollingRequest;
      }
      polling();
    }, 3000);
  }
  async function open() {
    if ($('setup-dialog').open) return;
    beforeOpen(); returnFocus = document.activeElement;
    step = 1; state = null; plan = null; restoring = false; transport = false;
    text('setup-subtitle', t("프로젝트 준비부터 Codex 연결까지 한곳에서 진행합니다."));
    $('setup-dialog').showModal(); render();
    await run(t("실행 환경과 프로젝트 확인 중…"), async () => { state = await api.setup('begin'); });
    $('setup-folder').focus(); polling();
  }
  function close() {
    if (busy) return;
    clearTimeout(timer); $('setup-dialog').close();
    if (returnFocus?.isConnected) returnFocus.focus();
    void refresh();
  }
  $('setup-open').addEventListener('click', () => { void open(); });
  $('setup-close').addEventListener('click', close);
  $('setup-later').addEventListener('click', close);
  $('setup-dialog').addEventListener('cancel', event => { event.preventDefault(); close(); });
  $('setup-folder').addEventListener('click', () => { void run(t("프로젝트 폴더 확인 중…"), async () => { const value = await api.setup('folder'); if (value) { state = value; plan = null; transport = false; } }); });
  $('setup-python').addEventListener('click', () => { void run(t("Python 환경 확인 중…"), async () => { const value = await api.setup('python'); if (value) state = value; }); });
  $('setup-existing').addEventListener('click', () => { void run(t("기존 프로젝트 열기…"), async () => { if (await selectExisting()) state = await api.setup('begin'); }); });
  $('setup-preview').addEventListener('click', () => { void run(t("변경 내용 다시 읽는 중…"), async () => { showPlan(await api.setup('preview')); await load(); }); });
  $('setup-back').addEventListener('click', () => { restoring = false; step = Math.max(1, step - 1); render(); });
  $('setup-next').addEventListener('click', () => {
    if (step === 4) { close(); return; }
    void run([t("프로젝트 준비 중…"), t("연결 설정 적용 중…"), t("상태 확인 중…")][step - 1], async () => {
      restoring = false;
      if (step === 1) {
        state = await api.setup('create', { allowed_paths: $('setup-paths').value.split(/\r?\n/).map(v => v.trim()).filter(Boolean) });
        step = 2; plan = null; showPlan(await api.setup('preview')); await refresh();
      } else if (step === 2) {
        state = await api.setup('install', { fingerprint: plan.fingerprint }); step = 3;
      } else {
        await load();
        if (!state.installed) { step = 2; plan = null; showPlan(await api.setup('preview')); throw new Error(t("연결 설정이 변경됐습니다. 변경 내용을 다시 확인하세요.")); }
        step = 4;
      }
      $('setup-body').scrollTo(0, 0);
    });
  });
  $('setup-check').addEventListener('click', () => { void run(t("MCP 서버 통신 점검 중…"), async () => {
    const snapshot = await api.checkConnection(); transport = snapshot.connection?.mcp_stdio === 'verified';
    if (!transport) throw new Error(snapshot.connection?.error?.message || t("통신을 확인하지 못했습니다. 프로젝트와 Python 환경을 확인한 뒤 재시도하세요."));
  }); });
  $('setup-challenge').addEventListener('click', () => { void run(t("확인 문구 만드는 중…"), async () => { state = await api.setup('challenge'); }); });
  $('setup-copy').addEventListener('click', () => { void run(t("복사 중…"), async () => { await api.setup('copy'); text('setup-subtitle', t("복사했습니다. 같은 프로젝트의 Codex 대화에 붙여넣으세요.")); }); });
  $('setup-restore').addEventListener('click', () => {
    if (!restoring) { restoring = true; text('setup-progress', t("앱이 마지막으로 바꾼 연결 파일을 백업으로 되돌립니다. 작업 기록은 유지합니다. 한 번 더 눌러 적용하세요.")); render(); return; }
    void run(t("연결 설정 되돌리는 중…"), async () => { state = await api.setup('restore'); plan = null; transport = false; restoring = false; step = 2; showPlan(await api.setup('preview')); });
  });
  window.addEventListener('jev:languagechange', () => {
    for (const id of ['setup-subtitle', 'setup-progress', 'setup-error']) text(id, i18n.message($(id).textContent));
    if (plan) showPlan(plan);
    render();
  });
  return { open, isOpen: () => $('setup-dialog').open, start: async () => {
    if (!api?.setup) return;
    try { state = await api.setup('begin'); if (!state.project_ready || !state.installed) await open(); }
    catch { await open(); }
  } };
};
