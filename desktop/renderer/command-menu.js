"use strict";

(() => {
  let instance = 0;

  window.createJevCommandMenu = ({ getItems, onBeforeOpen } = {}) => {
    if (typeof getItems !== "function") throw new TypeError("getItems must be a function");
    const dialog = document.getElementById("command-dialog");
    const input = document.getElementById("command-input");
    const results = document.getElementById("command-results");
    const scrollArea = results?.closest(".command-result-area") || results;
    const empty = document.getElementById("command-empty");
    const count = document.getElementById("command-count");
    const closeButton = document.getElementById("command-close");
    if (![dialog, input, results, empty, count, closeButton].every(Boolean)) {
      throw new Error("Command menu markup is incomplete");
    }
    const current = document.getElementById("command-current");
    const submitLabel = document.getElementById("command-submit-label");
    const prefix = `jev-command-${++instance}`;
    let returnFocus = null;
    let selectedId = null;
    let visibleItems = [];
    let options = [];
    let composing = false;

    const searchText = value => String(value ?? "").normalize("NFKC").toLocaleLowerCase("ko");
    const enabled = item => item && !item.disabled && typeof item.run === "function";

    function readItems() {
      const items = getItems();
      if (!Array.isArray(items)) return [];
      const seen = new Set();
      return items.filter(item => {
        if (!item || typeof item.id !== "string" || !item.id || seen.has(item.id)
          || typeof item.label !== "string") return false;
        seen.add(item.id);
        return true;
      });
    }

    function restoreFocus(target) {
      const usable = element => element instanceof HTMLElement && element.isConnected
        && !element.disabled && !element.closest("[inert]") && element.getClientRects().length > 0
        && getComputedStyle(element).visibility !== "hidden"
        && element.matches("button, input, select, textarea, a[href], [tabindex], [contenteditable]");
      const destination = usable(target) ? target : document.getElementById("command-open");
      if (usable(destination)) destination.focus({ preventScroll: true });
    }

    function finishClose() {
      const target = returnFocus;
      returnFocus = null;
      input.setAttribute("aria-expanded", "false");
      if (target) restoreFocus(target);
    }

    function close() {
      if (dialog.open) dialog.close();
      // Restore synchronously. An old queued native close event cannot steal focus
      // after the menu has already opened again.
      finishClose();
    }

    function select(id, scroll = false) {
      const selected = visibleItems.find(item => item.id === id && enabled(item));
      selectedId = selected?.id || null;
      for (let index = 0; index < options.length; index += 1) {
        const active = visibleItems[index].id === selectedId;
        options[index].setAttribute("aria-selected", String(active));
        if (active) {
          input.setAttribute("aria-activedescendant", options[index].id);
          if (scroll) options[index].scrollIntoView({ block: "nearest" });
        }
      }
      if (!selected) input.removeAttribute("aria-activedescendant");
      if (current) current.textContent = selected
        ? `${selected.label}${selected.description ? ` · ${selected.description}` : ""}`
        : "실행 가능한 항목이 없습니다.";
      if (submitLabel) submitLabel.textContent = selected ? "실행" : "실행 불가";
    }

    function execute(id) {
      if (!dialog.open) return;
      // Re-check the current state before dispatch, even if a displayed item was
      // enabled before a model/status update arrived.
      const item = readItems().find(candidate => candidate.id === id);
      if (!enabled(item)) { refresh(); input.focus(); return; }
      close();
      try {
        // Application actions own their user-facing error handling.
        Promise.resolve(item.run()).catch(error => console.error("빠른 실행 실패", error));
      } catch (error) { console.error("빠른 실행 실패", error); }
    }

    function render(resetSelection = false) {
      if (!dialog.open) return;
      const scrollTop = scrollArea.scrollTop;
      const terms = searchText(input.value).trim().split(/\s+/u).filter(Boolean);
      visibleItems = readItems().filter(item => {
        const keywords = Array.isArray(item.keywords) ? item.keywords.join(" ") : item.keywords || "";
        const searchable = searchText(`${item.label} ${item.description || ""} ${keywords}`);
        return terms.every(term => searchable.includes(term));
      });
      const fragment = document.createDocumentFragment();
      options = [];
      let group;
      let groupName;
      visibleItems.forEach((item, index) => {
        const name = String(item.group || "명령");
        if (!group || name !== groupName) {
          groupName = name;
          group = document.createElement("div");
          group.className = "command-group";
          group.setAttribute("role", "group");
          const heading = document.createElement("div");
          heading.className = "command-group-title";
          heading.id = `${prefix}-group-${index}`;
          heading.setAttribute("role", "presentation");
          heading.textContent = name;
          group.setAttribute("aria-labelledby", heading.id);
          group.append(heading);
          fragment.append(group);
        }
        const option = document.createElement("div");
        option.className = "command-item";
        option.id = `${prefix}-option-${index}`;
        option.dataset.commandId = item.id;
        option.tabIndex = -1;
        option.setAttribute("role", "option");
        option.setAttribute("aria-disabled", String(!enabled(item)));
        const main = document.createElement("div");
        main.className = "command-item-main";
        const label = document.createElement("span");
        label.className = "command-item-label";
        label.textContent = item.label;
        main.append(label);
        if (item.description || !enabled(item)) {
          const description = document.createElement("span");
          description.className = "command-item-description";
          description.textContent = item.description || "현재 사용할 수 없습니다.";
          main.append(description);
        }
        option.append(main);
        if (item.shortcut) {
          const shortcut = document.createElement("kbd");
          shortcut.className = "command-item-shortcut";
          shortcut.textContent = String(item.shortcut);
          option.append(shortcut);
        }
        option.addEventListener("mousedown", event => event.preventDefault());
        option.addEventListener("click", () => execute(item.id));
        group.append(option);
        options.push(option);
      });
      results.replaceChildren(fragment);
      count.textContent = `${visibleItems.length}개 결과`;
      empty.hidden = visibleItems.length > 0;
      empty.textContent = terms.length ? "검색 결과가 없습니다. 다른 검색어로 찾아보세요."
        : "현재 표시할 명령이나 작업이 없습니다.";
      const previous = !resetSelection && visibleItems.find(item => item.id === selectedId && enabled(item));
      select(previous?.id || visibleItems.find(enabled)?.id, resetSelection || !previous);
      if (previous) scrollArea.scrollTop = scrollTop;
      else if (resetSelection) scrollArea.scrollTop = 0;
    }

    function refresh() { if (dialog.open) render(); }

    function open() {
      if (dialog.open) { input.focus(); return; }
      onBeforeOpen?.();
      returnFocus = document.activeElement;
      composing = false;
      selectedId = null;
      input.value = "";
      dialog.showModal();
      input.setAttribute("aria-expanded", "true");
      render(true);
      input.focus();
    }

    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; render(true); });
    input.addEventListener("input", () => render(true));
    dialog.addEventListener("keydown", event => {
      if (composing || event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); close();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); event.stopPropagation();
        const active = visibleItems.filter(enabled);
        const previous = active.findIndex(item => item.id === selectedId);
        const index = event.key === "ArrowDown" ? Math.min(previous + 1, active.length - 1)
          : Math.max(0, previous < 0 ? active.length - 1 : previous - 1);
        select(active[index]?.id, true);
        input.focus();
      } else if (event.key === "Enter" && event.target === input) {
        event.preventDefault(); event.stopPropagation(); execute(selectedId);
      }
    });
    dialog.addEventListener("cancel", event => { event.preventDefault(); if (!composing) close(); });
    dialog.addEventListener("close", () => { if (!dialog.open) finishClose(); });
    closeButton.addEventListener("click", close);
    dialog.addEventListener("click", event => {
      const bounds = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom)) close();
    });
    input.setAttribute("aria-expanded", "false");
    return Object.freeze({ open, close, refresh, isOpen: () => dialog.open });
  };
})();
