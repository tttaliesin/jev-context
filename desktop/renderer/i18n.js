"use strict";

(() => {
  const catalog = window.JevLocale;
  let language = "ko";
  let saving = false;
  const t = (key, ...args) => catalog.translate(language, key, ...args);
  const message = value => catalog.message(language, value);
  function applyStatic() {
    document.documentElement.lang = language;
    for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
    for (const attribute of ['title', 'aria-label', 'aria-valuetext', 'placeholder']) {
      for (const node of document.querySelectorAll(`[data-i18n-${attribute}]`)) {
        node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`)));
      }
    }
    for (const select of document.querySelectorAll('[data-language-select]')) select.value = language;
  }
  async function setLanguage(next) {
    if (saving || !['ko', 'en'].includes(next)) return;
    saving = true;
    const selects = [...document.querySelectorAll('[data-language-select]')];
    for (const select of selects) select.disabled = true;
    try {
      if (window.jev?.setLanguage) await window.jev.setLanguage(next);
      else localStorage.setItem('jev.language', next); // Browser fixtures only.
      language = next;
      for (const node of document.querySelectorAll('[data-language-error]')) node.textContent = '';
      applyStatic();
      window.dispatchEvent(new Event('jev:languagechange'));
    } catch {
      for (const node of document.querySelectorAll('[data-language-error]')) node.textContent = t('언어 설정을 저장하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      saving = false;
      for (const select of selects) { select.disabled = false; select.value = language; }
    }
  }
  const ready = (async () => {
    try { language = catalog.normalize(window.jev?.getLanguage ? await window.jev.getLanguage() : localStorage.getItem('jev.language')); }
    catch { language = 'ko'; }
    applyStatic();
    for (const select of document.querySelectorAll('[data-language-select]')) {
      select.addEventListener('change', () => { void setLanguage(select.value); });
    }
  })();
  window.jevI18n = Object.freeze({ t, message, ready, setLanguage,
    prompt: value => catalog.prompt(language, value),
    get language() { return language; },
    get locale() { return language === 'en' ? 'en-US' : 'ko-KR'; },
  });
})();
