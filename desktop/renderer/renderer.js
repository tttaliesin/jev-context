"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const api = window.jev;
  let onboarding;
  const preferenceStorage = "jev.ui.v1";
  const validWorkId = (value) => typeof value === "string" && /^work-[a-zA-Z0-9-]{1,100}$/.test(value);
  function readPreferences() {
    const result = { sidebarCollapsed: false, sidebarWidth: 260, projects: Object.create(null) };
    try {
      const saved = JSON.parse(localStorage.getItem(preferenceStorage));
      result.sidebarCollapsed = saved?.sidebarCollapsed === true;
      if (Number.isInteger(saved?.sidebarWidth) && saved.sidebarWidth >= 220 && saved.sidebarWidth <= 400) result.sidebarWidth = saved.sidebarWidth;
      if (saved?.projects && typeof saved.projects === "object" && !Array.isArray(saved.projects)) {
        for (const [key, value] of Object.entries(saved.projects).slice(-20)) {
          if (key.length > 2048 || !value || typeof value !== "object") continue;
          result.projects[key] = { selectedId: validWorkId(value.selectedId) ? value.selectedId : null,
            query: typeof value.query === "string" ? value.query.slice(0, 200) : "" };
        }
      }
    } catch { /* Missing/corrupt/unavailable storage must not stop the manager. */ }
    return result;
  }
  const preferences = readPreferences();
  const state = {
    snapshot: null,
    works: [],
    nextCursor: null,
    listRevision: null,
    selectedId: null,
    selectedWork: null,
    filter: "",
    action: null,
    loading: false,
    loadingVisible: false,
    stale: false,
    projectGeneration: 0,
    overviewGeneration: 0,
    workRequest: 0,
    workLoadingId: null,
    timer: null,
    noticeTimer: null,
    preferenceKey: null,
    detailError: null,
    errorKind: null,
    helpReturnFocus: null,
    commandMenu: null,
    sidebarDrag: null,
  };

  const text = (value, fallback = "미확인") =>
    typeof value === "string" && value.trim() ? value : fallback;
  const number = (value) => typeof value === "number" && Number.isFinite(value);
  const setText = (id, value) => { $(id).textContent = String(value); };
  const announce = (message) => { setText("announcement", message); };
  const projectName = (path) => text(path, "프로젝트 미선택").split(/[\\/]/).filter(Boolean).pop() || "프로젝트";
  const projectKey = (snapshot) => `${snapshot.project?.project_id || ""}|${snapshot.config_path || snapshot.project?.project_root || ""}`.replaceAll("\\", "/").toLowerCase();

  function savePreferences() {
    try { localStorage.setItem(preferenceStorage, JSON.stringify(preferences)); }
    catch { /* UI preferences are optional; record access and model control remain available. */ }
  }

  function rememberProject() {
    if (!state.preferenceKey) return;
    delete preferences.projects[state.preferenceKey];
    preferences.projects[state.preferenceKey] = { selectedId: validWorkId(state.selectedId) ? state.selectedId : null,
      query: $("work-filter").value.slice(0, 200) };
    for (const key of Object.keys(preferences.projects).slice(0, -20)) delete preferences.projects[key];
    savePreferences();
  }

  function setSidebarCollapsed(collapsed, persist = true) {
    if (collapsed && ($("sidebar").contains(document.activeElement) || document.activeElement === $("sidebar-resize"))) $("sidebar-toggle").focus({ preventScroll: true });
    $("app-shell").classList.toggle("sidebar-collapsed", collapsed);
    $("sidebar").inert = collapsed;
    $("sidebar").setAttribute("aria-hidden", String(collapsed));
    $("sidebar-toggle").setAttribute("aria-expanded", String(!collapsed));
    const label = collapsed ? "작업 목록 펼치기" : "작업 목록 접기";
    $("sidebar-toggle").setAttribute("aria-label", label);
    $("sidebar-toggle").title = `${label} · Ctrl+B`;
    preferences.sidebarCollapsed = collapsed;
    if (persist) savePreferences();
    applySidebarWidth();
    state.commandMenu?.refresh();
  }

  const sidebarMaximum = () => Math.max(220, Math.min(400, Math.floor(window.innerWidth - 480)));

  function applySidebarWidth(width = preferences.sidebarWidth) {
    const maximum = sidebarMaximum();
    const actual = Math.max(220, Math.min(maximum, Math.round(width)));
    $("app-shell").style.setProperty("--sidebar-expanded-width", `${actual}px`);
    $("sidebar-resize").setAttribute("aria-valuemax", String(maximum));
    $("sidebar-resize").setAttribute("aria-valuenow", String(actual));
    $("sidebar-resize").setAttribute("aria-valuetext", `${actual}픽셀`);
    return actual;
  }

  function setSidebarWidth(width) {
    preferences.sidebarWidth = applySidebarWidth(width);
    savePreferences();
  }

  function resetSidebarWidth() {
    preferences.sidebarWidth = 260;
    applySidebarWidth();
    savePreferences();
    announce("작업 목록 너비를 기본값으로 복원했습니다.");
  }

  function focusSearch() {
    setSidebarCollapsed(false);
    $("work-filter").focus();
    $("work-filter").select();
  }

  function closeDiagnostics(restoreFocus = false) {
    const details = $("model-diagnostics");
    if (!details.open) return false;
    details.open = false;
    if (restoreFocus) details.querySelector("summary").focus({ preventScroll: true });
    return true;
  }

  function showShortcuts() {
    if ($("shortcuts-dialog").open) return;
    state.commandMenu?.close();
    closeDiagnostics();
    state.helpReturnFocus = document.activeElement;
    $("shortcuts-dialog").showModal();
    $("shortcuts-close").focus();
  }

  function closeShortcuts() {
    if (!$("shortcuts-dialog").open) return;
    const previous = state.helpReturnFocus;
    state.helpReturnFocus = null;
    $("shortcuts-dialog").close();
    const target = previous?.isConnected && previous.getClientRects().length && !previous.disabled
      ? previous : $("shortcuts-open");
    target.focus({ preventScroll: true });
  }

  function commandItems() {
    const action = (id, label, description, shortcut, button, run) => {
      let unavailable = button?.title || "현재 사용할 수 없습니다.";
      if (!api) unavailable = "관리 도구 연결이 필요합니다.";
      else if (state.action) unavailable = "다른 요청을 처리 중입니다. 완료 후 사용할 수 있습니다.";
      else if (state.loading && ["refresh", "load-more"].includes(id)) unavailable = "프로젝트 상태를 조회 중입니다.";
      else if (id === "connection" && !state.snapshot?.project) unavailable = "프로젝트를 먼저 열어 주세요.";
      return { id, group: "화면과 연결", label, description: button?.disabled ? unavailable : description,
        shortcut, disabled: button?.disabled === true, run };
    };
    const items = [
      action("filter", "불러온 작업 필터", "현재 목록의 제목과 목표에서 찾기", "Ctrl F", null, focusSearch),
      action("sidebar", preferences.sidebarCollapsed ? "작업 목록 펼치기" : "작업 목록 접기", "기록을 읽을 공간 조절", "Ctrl B", null,
        () => setSidebarCollapsed(!preferences.sidebarCollapsed)),
      action("reset-width", "목록 너비 기본값으로", "기본 너비 260픽셀 복원", "", null, resetSidebarWidth),
      action("refresh", "프로젝트 상태 새로고침", "현재 상태와 작업 목록 다시 읽기", "F5", $("refresh"), () => { void refresh(); }),
      action("connection", "연결 점검", "별도 MCP 통신 확인", "", $("check-connection"), () => { void perform("checkConnection"); }),
      action("setup", "Codex 연결 설정", "프로젝트 준비 · 설치 · 확인 · 되돌리기", "", $("setup-open"), () => { void onboarding?.open(); }),
      action("project", "프로젝트 설정 열기", "다른 프로젝트 설정 선택", "", $("select-project"), () => { void perform("selectProject"); }),
      action("shortcuts", "키보드 단축키", "사용할 수 있는 단축키 안내", "F1", null, showShortcuts),
    ];
    if (state.nextCursor) items.push(action("load-more", "작업 더 불러오기", "이전 기록을 추가한 뒤 필터로 찾기", "", $("load-more"), () => { void refresh({ append: true }); }));
    for (const [id, label, method] of [["prepare", "모델 준비", "prepareModel"], ["stop", "모델 종료", "stopModel"]]) {
      const button = $(id === "prepare" ? "prepare-model" : "stop-model");
      items.push({ ...action(id, label, button.title, "", button, () => { void perform(method); }), group: "모델" });
    }
    for (const work of state.works) {
      if (!work.work_id || work.redacted) continue;
      items.push({ id: `work:${work.work_id}`, group: "불러온 작업", label: text(work.title, "제목 없는 작업"),
        description: `${work.work_id === state.selectedId ? "현재 기록 · " : ""}${text(work.goal, "목표 없음").slice(0, 160)}`,
        keywords: `${work.work_id} ${work.goal || ""}`, run: () => { void selectWork(work.work_id); } });
    }
    return items;
  }

  function dateLabel(value, short = false) {
    if (!value) return "갱신 시각 미확인";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "갱신 시각 미확인";
    return new Intl.DateTimeFormat("ko-KR", short
      ? { month: "short", day: "numeric" }
      : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parsed);
  }

  function errorMessage(error) {
    if (typeof error === "string") return error;
    return text(error?.message, text(error?.error?.message, "요청을 처리하지 못했습니다. 다시 시도해 주세요."));
  }

  function showError(title, error, kind = "action") {
    state.errorKind = kind;
    setText("error-title", title);
    setText("error-message", errorMessage(error));
    $("error-banner").hidden = false;
    announce(title);
  }

  function clearError(kind) {
    if (kind && state.errorKind !== kind) return;
    state.errorKind = null;
    $("error-banner").hidden = true;
  }

  function notify(message) {
    clearTimeout(state.noticeTimer);
    setText("action-message", message);
    $("action-notice").hidden = !message;
    if (message) state.noticeTimer = setTimeout(() => { $("action-notice").hidden = true; }, 6500);
  }

  async function invoke(method, ...args) {
    if (!api || typeof api[method] !== "function") {
      throw new Error("관리 도구 API에 연결할 수 없습니다. JEV 데스크톱 앱에서 이 화면을 열어 주세요.");
    }
    const response = await api[method](...args);
    if (response?.ok === false) throw new Error(errorMessage(response.error));
    return response;
  }

  function badge(id, label, tone = "neutral") {
    setText(id, label);
    $(id).className = `badge badge-${tone}`;
  }

  function connectionValue(id, label, tone = "") {
    setText(id, label);
    $(id).className = `connection-value ${tone}`.trim();
  }

  function updateButtons() {
    const engine = state.snapshot?.engine || {};
    const available = !!api;
    const busy = !!state.action;
    $("select-project").disabled = !available || busy;
    $("refresh").disabled = !available || busy || state.loading;
    $("retry").disabled = !available || busy || state.loading;
    $("prepare-model").disabled = !available || busy || state.stale || engine.can_prepare !== true;
    $("stop-model").disabled = !available || busy || state.stale || engine.can_stop !== true || !engine.worker_pid;
    $("check-connection").disabled = !available || busy || !state.snapshot?.project;
    $("load-more").disabled = !available || busy || state.loading;
    $("load-more").textContent = state.loadingVisible && state.nextCursor ? "불러오는 중…" : "작업 더 보기";
    $("list-loading").hidden = !state.loadingVisible;
    $("work-list").setAttribute("aria-busy", String(state.loading));
    $("work-loading").hidden = !state.workLoadingId;
    $("work-panel").setAttribute("aria-busy", String(!!state.workLoadingId));
    $("work-retry").disabled = !available || !!state.workLoadingId;
    $("prepare-model").querySelector("span").textContent = state.action === "prepareModel"
      ? "요청 중…" : engine.state === "preparing" ? "준비 중" : "모델 준비";
    $("stop-model").querySelector("span").textContent = state.action === "stopModel" ? "종료 중…" : "모델 종료";
    $("check-connection").textContent = state.action === "checkConnection" ? "점검 중…" : "연결 점검";
    $("prepare-model").title = state.stale ? "현재 상태를 새로고침한 뒤 준비할 수 있습니다."
      : engine.can_prepare ? "로컬 모델을 준비합니다." : "현재 상태에서는 모델을 준비할 수 없습니다.";
    $("stop-model").title = state.stale ? "현재 상태를 새로고침한 뒤 종료할 수 있습니다."
      : engine.state === "preparing" ? "모델 준비가 끝난 뒤 종료할 수 있습니다."
      : Number(engine.active_requests) > 0 ? "판단 요청을 처리 중입니다. 끝난 뒤 종료할 수 있습니다."
      : engine.can_stop && engine.worker_pid ? "공유 모델을 종료하고 메모리를 해제합니다." : "종료할 모델이 없습니다.";
    state.commandMenu?.refresh();
  }

  function renderModel(engine) {
    const revision = text(engine.model_revision, "모델 프로필 미확인");
    setText("model-identity", `${revision.split("@")[0]}${engine.device ? ` · ${engine.device}` : ""}`);
    $("model-identity").title = revision;
    const active = Number(engine.active_requests) > 0;
    const statuses = {
      idle: ["대기", "필요할 때 모델을 준비합니다. 현재 모델 메모리는 해제되어 있습니다.", ""],
      preparing: ["준비 중", "모델을 메모리에 올리고 있습니다. 준비가 끝나면 상태가 갱신됩니다.", "working"],
      shadow: [active ? "판단 중" : "준비 완료", active ? "판단을 처리하고 있습니다. 완료 후 종료할 수 있습니다." : "판단 요청을 받을 수 있습니다. 사용하지 않으면 자동으로 해제됩니다.", "positive"],
      active: [active ? "판단 중" : "준비 완료", active ? "판단을 처리하고 있습니다. 완료 후 종료할 수 있습니다." : "판단 요청을 받을 수 있습니다. 사용하지 않으면 자동으로 해제됩니다.", "positive"],
      disabled: ["모델 사용 안 함", "이 프로젝트 설정에서 모델 판단이 비활성화되어 있습니다. 저장된 작업 기록은 계속 볼 수 있습니다.", ""],
      unavailable: ["모델 준비 불가", "모델 프로필과 실행 환경을 확인해 주세요. 설정을 확인한 뒤 다시 준비할 수 있습니다.", "warning"],
      degraded: ["모델 확인 필요", "모델 실행 중 문제가 발생했습니다. 오류 내용을 확인한 뒤 다시 준비해 주세요.", "error"],
      unconfigured: ["모델 설정 필요", "이 프로젝트에 사용할 모델 프로필이 없습니다. 프로젝트 설정을 확인해 주세요.", "warning"],
    };
    const [label, description, tone] = statuses[engine.state] || ["상태 미확인", "모델의 현재 실행 상태를 확인하지 못했습니다.", ""];
    setText("model-state", label);
    setText("model-description", engine.profile_error
      ? "모델 프로필을 사용할 수 없습니다. 프로젝트 설정을 확인해 주세요. 저장된 작업 기록은 계속 볼 수 있습니다."
      : description);
    $("model-dot").className = `status-dot large ${tone}`.trim();
    $("preparing-indicator").hidden = engine.state !== "preparing";
    const mode = engine.configured_mode;
    badge("model-mode", mode === "shadow" ? "관찰 모드" : mode === "active" ? "활성 모드" : mode === "disabled" ? "비활성" : "설정 미확인");
    setText("model-memory", engine.state === "preparing" ? "로드 중" : engine.worker_pid ? "모델 로드됨" : engine.state === "idle" || engine.state === "disabled" ? "해제됨" : "미확인");
    setText("model-idle", number(engine.idle_timeout_seconds) ? `${engine.idle_timeout_seconds}초 후` : "미확인");
    const startup = engine.startup;
    setText("model-startup", number(startup?.total_seconds) ? `${startup.total_seconds.toFixed(1)}초` : "기록 없음");
    const details = [];
    if (engine.profile_error) details.push(errorMessage(engine.profile_error));
    if (engine.preparation_error) details.push(`준비 오류: ${errorMessage(engine.preparation_error)}`);
    if (startup?.fallback) details.push("캐시를 다시 만들어 준비했습니다.");
    else if (startup?.loaded_from_cache === true) details.push("최근 준비에서 캐시를 사용했습니다.");
    if (!details.length) details.push("상태 조회는 모델을 시작하지 않습니다.");
    setText("model-detail", details.join(" · "));
  }

  function renderConnection(connection) {
    const connected = connection.manager_bridge === "connected";
    connectionValue("connection-manager", connected ? "연결됨" : "연결 미확인", connected ? "positive" : "");
    $("bridge-dot").className = `status-dot ${connected ? "positive" : ""}`.trim();
    setText("bridge-label", connected ? "관리 도구 연결됨" : "관리 도구 연결 미확인");
    const mcp = connection.mcp_stdio;
    connectionValue("connection-mcp", mcp === "verified" ? "통신 확인" : mcp === "failed" ? "점검 실패" : "아직 점검 안 함", mcp === "verified" ? "positive" : mcp === "failed" ? "error" : "");
    const observed = ["observed", "verified"].includes(connection.desktop_current_session);
    connectionValue("connection-desktop", observed ? "연결 관측됨" : "직접 관측 안 함", observed ? "positive" : "");
    setText("connection-description", connection.error
      ? errorMessage(connection.error)
      : "MCP 통신 점검과 현재 Codex 대화의 연결은 별도로 확인합니다.");
    setText("connection-checked", connection.checked_at
      ? `마지막 점검 ${dateLabel(connection.checked_at)}`
      : "연결 점검은 모델을 로드하지 않습니다.");
  }

  function renderWorkList() {
    const listScroll = $("work-list").scrollTop;
    const focusedId = $("work-list").contains(document.activeElement) ? document.activeElement.dataset.workId : null;
    const filtered = state.works.filter((work) => `${work.title || ""} ${work.goal || ""}`.toLocaleLowerCase("ko").includes(state.filter));
    const tabStop = filtered.find((work) => work.work_id === focusedId && !work.redacted)
      || filtered.find((work) => work.work_id === state.selectedId && !work.redacted)
      || filtered.find((work) => work.work_id && !work.redacted);
    const fragment = document.createDocumentFragment();
    for (const work of filtered) {
      const item = document.createElement("div");
      item.className = "work-list-item";
      item.setAttribute("role", "listitem");
      const button = document.createElement("button");
      button.className = "work-item";
      button.type = "button";
      button.dataset.workId = work.work_id || "";
      button.setAttribute("aria-current", String(work.work_id === state.selectedId));
      button.disabled = !work.work_id || work.redacted === true;
      button.tabIndex = work.work_id === tabStop?.work_id ? 0 : -1;
      const title = document.createElement("span");
      title.className = "work-item-title";
      title.textContent = work.redacted ? "접근이 제한된 기록" : text(work.title, "제목 없는 작업");
      button.title = title.textContent;
      const meta = document.createElement("span");
      meta.className = "work-item-meta";
      const conflict = Number(work.unresolved_conflicts) > 0 ? ` · 충돌 ${work.unresolved_conflicts}건` : "";
      meta.textContent = `${dateLabel(work.updated_at, true)}${number(work.revision) ? ` · r${work.revision}` : ""}${conflict}`;
      button.append(title, meta);
      button.addEventListener("click", () => { void selectWork(work.work_id); });
      item.append(button);
      fragment.append(item);
    }
    $("work-list").replaceChildren(fragment);
    $("work-list").scrollTop = listScroll;
    if (focusedId) {
      const button = [...$("work-list").querySelectorAll("button")].find((item) => item.dataset.workId === focusedId);
      button?.focus({ preventScroll: true });
    }
    setText("work-count", `${state.filter ? `${filtered.length} / ` : ""}${state.works.length}${state.nextCursor ? "+" : ""}`);
    $("work-count").title = state.filter ? `검색 결과 ${filtered.length}개 · 불러온 작업 ${state.works.length}개` : `불러온 작업 ${state.works.length}개`;
    setText("search-scope", `불러온 ${state.works.length}개에서 찾기${state.nextCursor ? " · 더 있음" : ""}`);
    setText("command-scope", `기록 검색은 불러온 ${state.works.length}개 기준${state.nextCursor ? " · 이전 기록은 더 불러오기" : ""}`);
    $("work-list-empty").hidden = filtered.length > 0;
    setText("work-list-empty", state.filter ? "불러온 작업 중 검색 결과가 없습니다." : "저장된 작업이 없습니다. Codex에서 작업을 시작하면 여기에 표시됩니다.");
    $("load-more").hidden = !state.nextCursor;
    $("search-clear").hidden = !state.filter;
    $("search-shortcut").hidden = !!state.filter;
    $("filter-reset").hidden = !state.filter || filtered.length > 0;
    state.commandMenu?.refresh();
  }

  function listText(value) {
    return typeof value === "string" ? value : text(value?.summary, text(value?.description, text(value?.text, text(value?.action, ""))));
  }

  function renderList(id, items, fallback = null) {
    const rows = Array.isArray(items) ? items.map(listText).filter(Boolean) : [];
    if (!rows.length && fallback) rows.push(fallback);
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
      const item = document.createElement("li");
      item.textContent = row;
      fragment.append(item);
    }
    $(id).replaceChildren(fragment);
    return rows.length;
  }

  function renderWork(work) {
    setText("location-work", work ? text(work.title, "제목 없는 작업") : "작업 기록");
    $("location-work").title = work ? text(work.title, "제목 없는 작업") : "저장된 작업 기록";
    $("work-empty").hidden = !!work;
    $("work-detail").hidden = !work;
    $("work-status").hidden = !work;
    $("work-retry").hidden = true;
    if (!work) {
      $("work-empty").querySelector("h3").textContent = state.works.length ? "작업 기록을 선택하세요" : "아직 저장된 작업이 없습니다";
      $("work-empty").querySelector("p").textContent = state.works.length ? "목표와 최근 진행 내용을 여기에서 확인합니다." : "Codex에서 작업을 시작하면 기록을 여기에서 볼 수 있습니다.";
      return;
    }
    const completed = work.completion_coverage === "reported_complete";
    badge("work-status", completed ? "완료 보고됨" : work.status === "active" ? "진행 중인 기록" : "저장된 기록", completed ? "positive" : "neutral");
    setText("work-title", text(work.title, "제목 없는 작업"));
    const modes = { implement: "구현", investigate: "조사", design: "설계", plan: "계획", review: "검토" };
    const mode = modes[work.scope?.mode];
    setText("work-meta", `${dateLabel(work.updated_at)} 갱신${mode ? ` · ${mode}` : ""}`);
    setText("work-revision", number(work.revision) ? `기록 r${work.revision}` : "기록 버전 미확인");
    setText("work-provenance", work.provenance === "agent_reported"
      ? "에이전트가 남긴 작업 기록 · 현재 모델 상태와 별도입니다."
      : "저장된 작업 기록입니다. 기록의 출처와 현재 검증 여부는 확인되지 않았습니다.");
    setText("work-goal", text(work.goal, "등록된 목표가 없습니다."));
    renderList("work-constraints", work.scope?.constraints, "등록된 제약이 없습니다.");
    setText("work-progress", text(work.progress?.summary, text(work.completion_summary, "아직 진행 보고가 없습니다.")));
    renderList("work-next-actions", work.progress?.next_actions);
    const openIssues = (Array.isArray(work.issues) ? work.issues : []).filter((issue) => issue.status === "open");
    const issues = [...(Array.isArray(work.open_items) ? work.open_items : []), ...openIssues];
    $("work-issues-section").hidden = renderList("work-issues", issues) === 0;
  }

  async function selectWork(id, { preserveDetail = false, force = false } = {}) {
    if (!id) return;
    if (state.workLoadingId === id) return;
    const sameSelection = state.selectedId === id;
    if (sameSelection && state.selectedWork && !preserveDetail && !force) return;
    preserveDetail = sameSelection && preserveDetail && !!state.selectedWork;
    const request = ++state.workRequest;
    const generation = state.projectGeneration;
    state.workLoadingId = id;
    state.selectedId = id;
    state.detailError = null;
    rememberProject();
    if (!preserveDetail) state.selectedWork = null;
    renderWorkList();
    if (!preserveDetail) {
      renderWork(null);
      $("work-empty").querySelector("h3").textContent = "작업 기록을 불러오는 중";
      $("work-empty").querySelector("p").textContent = "저장된 목표와 최근 진행 내용을 확인합니다.";
    }
    updateButtons();
    try {
      const work = await invoke("openWork", id);
      if (request !== state.workRequest || generation !== state.projectGeneration) return;
      if (!work || work.redacted || work.work_id !== id) throw new Error("이 작업 기록을 확인할 수 없습니다. 프로젝트와 접근 설정을 확인해 주세요.");
      state.selectedWork = work;
      // A restored selection can be outside the latest page. Add it only after a fresh read.
      if (!state.works.some((item) => item.work_id === id)) {
        state.works.unshift({ work_id: work.work_id, title: work.title, goal: work.goal,
          revision: work.revision, updated_at: work.updated_at });
        renderWorkList();
      }
      const readingPosition = { panel: $("work-panel").scrollTop, main: $("main").scrollTop, document: window.scrollY };
      renderWork(work);
      if (preserveDetail) {
        $("work-panel").scrollTop = readingPosition.panel;
        $("main").scrollTop = readingPosition.main;
        window.scrollTo(0, readingPosition.document);
      } else {
        $("work-panel").scrollTop = 0;
        $("main").scrollTop = 0;
        window.scrollTo(0, 0);
      }
      announce(`${text(work.title, "작업")} 기록을 불러왔습니다.`);
    } catch (error) {
      if (request !== state.workRequest || generation !== state.projectGeneration) return;
      state.selectedWork = null;
      state.detailError = { id, message: errorMessage(error) };
      renderWork(null);
      $("work-empty").querySelector("h3").textContent = "작업 기록을 불러오지 못했습니다";
      $("work-empty").querySelector("p").textContent = state.detailError.message;
      $("work-retry").hidden = false;
      announce("작업 기록 조회 실패. 이 작업을 다시 불러올 수 있습니다.");
    } finally {
      if (request === state.workRequest) {
        state.workLoadingId = null;
        updateButtons();
      }
    }
  }

  function applyOverview(snapshot, appendWorks = false) {
    if (!snapshot || typeof snapshot !== "object" || !snapshot.project) throw new Error("프로젝트 상태 응답을 확인할 수 없습니다.");
    const key = projectKey(snapshot);
    if (key !== state.preferenceKey) {
      rememberProject();
      state.preferenceKey = key;
      state.projectGeneration += 1;
      state.workRequest += 1;
      state.workLoadingId = null;
      state.selectedWork = null;
      state.detailError = null;
      state.works = [];
      state.listRevision = null;
      state.nextCursor = null;
      const saved = preferences.projects[key];
      state.selectedId = saved?.selectedId || null;
      $("work-filter").value = saved?.query || "";
      state.filter = $("work-filter").value.trim().toLocaleLowerCase("ko");
      $("work-list").scrollTop = 0;
      closeDiagnostics();
      renderWork(null);
    }
    state.snapshot = snapshot;
    state.stale = false;
    const project = snapshot.project;
    const root = text(project.project_root, "프로젝트 경로 미확인");
    const name = projectName(project.project_root);
    setText("sidebar-project", name);
    $("sidebar-project").title = root;
    setText("project-name", name);
    setText("project-path", root);
    $("project-path").title = root;
    badge("project-access", project.read_only ? "기록 읽기 전용" : "로컬 프로젝트");
    renderModel(snapshot.engine || {});
    renderConnection(snapshot.connection || {});
    const rows = snapshot.workspace_error ? [] : Array.isArray(snapshot.works?.items) ? snapshot.works.items : [];
    const listChanged = state.listRevision !== snapshot.works?.list_revision;
    const retainPages = !appendWorks && state.listRevision === snapshot.works?.list_revision && state.works.length > rows.length;
    if (!snapshot.workspace_error && (appendWorks || retainPages)) {
      const byId = new Map(state.works.map((work) => [work.work_id, work]));
      for (const work of rows) byId.set(work.work_id, work);
      state.works = [...byId.values()];
    } else {
      state.works = rows;
    }
    if (!retainPages) state.nextCursor = snapshot.works?.next_cursor || null;
    state.listRevision = snapshot.works?.list_revision ?? null;
    renderWorkList();
    if (snapshot.workspace_error) {
      state.workRequest += 1;
      state.workLoadingId = null;
      state.selectedId = null;
      state.selectedWork = null;
      state.detailError = null;
      state.nextCursor = null;
      renderWork(null);
      $("load-more").hidden = true;
      showError("작업 저장소를 확인해 주세요", snapshot.workspace_error, "overview");
      setText("work-list-empty", "작업 저장소를 읽지 못했습니다. 위의 오류 내용을 확인해 주세요.");
      $("work-empty").querySelector("h3").textContent = "작업 기록을 확인할 수 없습니다";
      $("work-empty").querySelector("p").textContent = "저장소 연결을 확인한 뒤 다시 시도해 주세요.";
    }
    const count = snapshot.sources?.count;
    const stale = snapshot.sources?.stale_sources?.count;
    setText("source-summary", number(count)
      ? `등록 자료 ${count}개${number(stale) ? ` · 현재 파일과 차이 ${stale}개` : ""}`
      : "등록 자료 수 미확인");
    setText("refreshed-at", `${new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())} 조회`);
    updateButtons();
    if (snapshot.workspace_error) return;
    if (!state.selectedId) {
      const first = state.works.find((work) => work.work_id && !work.redacted);
      if (first) void selectWork(first.work_id);
      else renderWork(null);
    } else {
      const selected = state.works.find((work) => work.work_id === state.selectedId);
      if (selected?.redacted) {
        state.workRequest += 1;
        state.workLoadingId = null;
        state.selectedWork = null;
        state.selectedId = null;
        state.detailError = null;
        rememberProject();
        renderWork(null);
      } else if (state.workLoadingId !== state.selectedId
        && state.detailError?.id !== state.selectedId
        && (!state.selectedWork || (selected ? selected.revision !== state.selectedWork.revision : listChanged))) {
        void selectWork(state.selectedId, { preserveDetail: !!state.selectedWork });
      }
    }
  }

  async function refresh({ append = false, silent = false } = {}) {
    if (silent && onboarding?.isOpen()) return;
    if (state.loading || state.action) return;
    const generation = state.overviewGeneration;
    state.loading = true;
    state.loadingVisible = !silent || !state.snapshot;
    updateButtons();
    if (!silent) {
      clearError();
      state.detailError = null;
    }
    try {
      const snapshot = await invoke("overview", append ? { cursor: state.nextCursor, limit: 50 } : { limit: 50 });
      if (generation !== state.overviewGeneration) return;
      clearError("overview");
      applyOverview(snapshot, append);
      if (!silent) announce("프로젝트 상태를 새로고침했습니다.");
    } catch (error) {
      if (generation !== state.overviewGeneration) return;
      state.stale = true;
      showError(state.snapshot ? "상태를 갱신하지 못했습니다" : "프로젝트 설정을 확인해 주세요", error, "overview");
      if (state.snapshot) {
        badge("model-mode", "이전 조회", "warning");
        setText("model-state", "현재 상태 미확인");
        setText("model-description", "마지막 조회 이후 상태를 확인하지 못했습니다. 새로고침 후 제어할 수 있습니다.");
        setText("model-memory", "미확인");
        $("model-dot").className = "status-dot large warning";
        $("preparing-indicator").hidden = true;
        connectionValue("connection-manager", "연결 미확인");
        $("bridge-dot").className = "status-dot warning";
        setText("bridge-label", "현재 연결 미확인");
        setText("model-detail", "마지막 조회 결과입니다. 현재 모델 상태는 미확인이며, 새로고침 후 제어할 수 있습니다.");
      }
      if (!state.snapshot) {
        setText("sidebar-project", "프로젝트 미선택");
        setText("project-name", "프로젝트를 열어 주세요");
        setText("project-path", "상단의 프로젝트 열기에서 설정을 선택할 수 있습니다.");
        setText("model-state", "프로젝트 미연결");
        setText("model-description", "프로젝트를 선택하면 모델 상태와 저장된 작업 기록을 확인할 수 있습니다.");
        setText("bridge-label", "프로젝트 연결 필요");
        setText("work-list-empty", "프로젝트를 선택하면 작업 기록이 표시됩니다.");
        setText("source-summary", "프로젝트 미선택");
        renderWork(null);
      }
    } finally {
      state.loading = false;
      state.loadingVisible = false;
      updateButtons();
    }
  }

  async function perform(method) {
    if (state.action) return;
    state.action = method;
    clearError();
    notify("");
    updateButtons();
    const generation = ++state.overviewGeneration;
    try {
      const snapshot = await invoke(method);
      if (!snapshot) {
        if (state.selectedId && !state.selectedWork && state.workLoadingId !== state.selectedId) void selectWork(state.selectedId);
        return; // A dismissed project chooser leaves the current project intact.
      }
      if (generation !== state.overviewGeneration) return;
      applyOverview(snapshot);
      const messages = {
        prepareModel: snapshot.engine?.state === "preparing" ? "모델 준비를 요청했습니다." : "모델 상태를 확인했습니다.",
        stopModel: snapshot.engine?.worker_pid ? "모델 종료 요청 후 상태를 확인합니다." : "모델 메모리가 해제되었습니다.",
        checkConnection: snapshot.connection?.mcp_stdio === "verified" ? "별도 MCP 통신을 확인했습니다. Codex 대화 연결은 직접 관측하지 않습니다." : "연결 점검 결과를 확인해 주세요.",
        selectProject: "프로젝트를 열었습니다.",
      };
      notify(messages[method] || "상태를 갱신했습니다.");
    } catch (error) {
      const titles = { prepareModel: "모델 준비 요청 실패", stopModel: "모델 종료 요청 실패", checkConnection: "연결 점검 실패", selectProject: "프로젝트 열기 실패" };
      showError(titles[method] || "요청 실패", error);
      if (state.selectedId && !state.selectedWork && state.workLoadingId !== state.selectedId) void selectWork(state.selectedId);
    } finally {
      state.action = null;
      updateButtons();
      scheduleRefresh();
    }
  }

  function scheduleRefresh() {
    clearTimeout(state.timer);
    const delay = state.snapshot?.engine?.state === "preparing" ? 2500 : 12000;
    state.timer = setTimeout(async () => {
      if (!document.hidden && api) await refresh({ silent: true });
      scheduleRefresh();
    }, delay);
  }

  $("select-project").addEventListener("click", () => { void perform("selectProject"); });
  $("prepare-model").addEventListener("click", () => { void perform("prepareModel"); });
  $("stop-model").addEventListener("click", () => { void perform("stopModel"); });
  $("check-connection").addEventListener("click", () => { void perform("checkConnection"); });
  $("refresh").addEventListener("click", () => { void refresh(); });
  $("retry").addEventListener("click", () => { void refresh(); });
  $("load-more").addEventListener("click", () => { void refresh({ append: true }); });
  $("work-retry").addEventListener("click", () => { void selectWork(state.selectedId, { force: true }); });
  $("sidebar-toggle").addEventListener("click", () => setSidebarCollapsed(!preferences.sidebarCollapsed));
  $("command-open").addEventListener("click", () => state.commandMenu.open());
  const resizeHandle = $("sidebar-resize");
  resizeHandle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || preferences.sidebarCollapsed) return;
    event.preventDefault();
    resizeHandle.focus({ preventScroll: true });
    state.sidebarDrag = { pointer: event.pointerId, startX: event.clientX,
      width: Number(resizeHandle.getAttribute("aria-valuenow")), nextWidth: null };
    resizeHandle.setPointerCapture(event.pointerId);
    $("app-shell").classList.add("sidebar-resizing");
  });
  resizeHandle.addEventListener("pointermove", (event) => {
    if (state.sidebarDrag?.pointer !== event.pointerId) return;
    state.sidebarDrag.nextWidth = applySidebarWidth(state.sidebarDrag.width + event.clientX - state.sidebarDrag.startX);
  });
  function finishSidebarResize(cancel = false) {
    const drag = state.sidebarDrag;
    if (!drag) return;
    state.sidebarDrag = null;
    $("app-shell").classList.remove("sidebar-resizing");
    if (resizeHandle.hasPointerCapture(drag.pointer)) resizeHandle.releasePointerCapture(drag.pointer);
    if (!cancel && drag.nextWidth !== null) setSidebarWidth(drag.nextWidth);
    else applySidebarWidth();
  }
  resizeHandle.addEventListener("pointerup", () => finishSidebarResize());
  resizeHandle.addEventListener("pointercancel", () => finishSidebarResize(true));
  resizeHandle.addEventListener("lostpointercapture", () => finishSidebarResize());
  resizeHandle.addEventListener("dblclick", resetSidebarWidth);
  resizeHandle.addEventListener("keydown", (event) => {
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
    const width = Number(resizeHandle.getAttribute("aria-valuenow"));
    if (event.key === "ArrowLeft") setSidebarWidth(width - 16);
    else if (event.key === "ArrowRight") setSidebarWidth(width + 16);
    else if (event.key === "Home") setSidebarWidth(220);
    else if (event.key === "End") setSidebarWidth(sidebarMaximum());
    else if (event.key === "Enter") setSidebarCollapsed(true);
    else return;
    event.preventDefault();
    event.stopPropagation();
  });
  window.addEventListener("resize", () => { finishSidebarResize(true); applySidebarWidth(); });
  $("shortcuts-open").addEventListener("click", showShortcuts);
  $("shortcuts-close").addEventListener("click", closeShortcuts);
  $("shortcuts-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeShortcuts();
  });
  $("shortcuts-dialog").addEventListener("click", (event) => {
    const bounds = $("shortcuts-dialog").getBoundingClientRect();
    if (event.target === $("shortcuts-dialog") && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom)) closeShortcuts();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!$("model-diagnostics").contains(event.target)) closeDiagnostics();
  });
  function clearSearch() {
    state.filter = "";
    $("work-filter").value = "";
    $("work-list").scrollTop = 0;
    rememberProject();
    renderWorkList();
    $("work-filter").focus();
  }
  $("search-clear").addEventListener("click", clearSearch);
  $("filter-reset").addEventListener("click", clearSearch);
  $("action-dismiss").addEventListener("click", () => notify(""));
  $("search-shortcut").textContent = "Ctrl F";
  $("work-filter").setAttribute("aria-keyshortcuts", "Control+F");
  $("work-filter").maxLength = 200;
  $("sidebar-toggle").setAttribute("aria-keyshortcuts", "Control+B");
  $("shortcuts-open").setAttribute("aria-keyshortcuts", "F1");
  $("command-open").setAttribute("aria-keyshortcuts", "Control+K");
  $("work-filter").addEventListener("input", (event) => {
    state.filter = event.target.value.trim().toLocaleLowerCase("ko");
    $("work-list").scrollTop = 0;
    rememberProject();
    renderWorkList();
  });
  document.addEventListener("keydown", (event) => {
    if (onboarding?.isOpen()) return;
    if (event.isComposing) return;
    const key = event.key.toLowerCase();
    const control = event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey;
    if (control && key === "k") {
      event.preventDefault();
      if (!event.repeat) {
        if (state.commandMenu.isOpen()) state.commandMenu.close();
        else state.commandMenu.open();
      }
      return;
    }
    if (state.commandMenu.isOpen()) {
      if (event.key === "F5" || (control && ["f", "r", "b"].includes(key))) event.preventDefault();
      return;
    }
    if ($("shortcuts-dialog").open) {
      if (event.key === "F1") { event.preventDefault(); closeShortcuts(); }
      else if (event.key === "F5" || (control && key === "r")) event.preventDefault();
      return;
    }
    if (event.key === "F1" && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      showShortcuts();
      return;
    }
    if (event.key === "F5" || (control && key === "r")) {
      event.preventDefault();
      void refresh();
      return;
    }
    const editing = document.activeElement?.matches("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
    if (control && key === "b" && !editing) {
      event.preventDefault();
      if (!event.repeat) setSidebarCollapsed(!preferences.sidebarCollapsed);
      return;
    }
    if (control && key === "f") {
      event.preventDefault();
      focusSearch();
      return;
    }
    if (event.key === "Escape" && closeDiagnostics(true)) {
      event.preventDefault();
      return;
    }
    const searchFocused = document.activeElement === $("work-filter");
    if (searchFocused && event.key === "Escape") {
      event.preventDefault();
      clearSearch();
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
    const inList = $("work-list").contains(document.activeElement);
    if (!inList && !searchFocused) return;
    const buttons = [...$("work-list").querySelectorAll("button:not(:disabled)")];
    if (!buttons.length) return;
    if (searchFocused && event.key === "Enter") {
      event.preventDefault();
      buttons[0].click();
      return;
    }
    let index = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown") index = Math.min(index + 1, buttons.length - 1);
    else if (event.key === "ArrowUp") index = index < 0 ? buttons.length - 1 : Math.max(0, index - 1);
    else if (inList && event.key === "Home") index = 0;
    else if (inList && event.key === "End") index = buttons.length - 1;
    else return;
    event.preventDefault();
    buttons.forEach((button, n) => { button.tabIndex = n === index ? 0 : -1; });
    buttons[index].focus();
    buttons[index].scrollIntoView({ block: "nearest" });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refresh({ silent: true });
  });
  window.addEventListener("beforeunload", () => {
    rememberProject();
    clearTimeout(state.timer);
    clearTimeout(state.noticeTimer);
  });
  state.commandMenu = window.createJevCommandMenu({ getItems: commandItems, onBeforeOpen: () => {
    closeShortcuts();
    closeDiagnostics();
  } });
  setSidebarCollapsed(preferences.sidebarCollapsed, false);
  updateButtons();
  onboarding = window.createJevOnboarding?.({ refresh, selectExisting: async () => {
    ++state.overviewGeneration;
    const snapshot = await invoke("selectProject");
    if (!snapshot) return false;
    applyOverview(snapshot); return true;
  },
    beforeOpen: () => { state.commandMenu?.close(); closeShortcuts(); closeDiagnostics(); } });
  void refresh().finally(() => { scheduleRefresh(); void onboarding?.start(); });
})();
