'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const locale = require('../desktop/locales.js');
const root = path.resolve(__dirname, '..');

test('every renderer lookup and static label has an English translation', () => {
  const keys = new Set();
  for (const name of ['renderer.js', 'onboarding.js', 'command-menu.js', 'i18n.js']) {
    const source = fs.readFileSync(path.join(root, 'desktop/renderer', name), 'utf8');
    for (const match of source.matchAll(/\bt\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g)) {
      keys.add(match[1][0] === '"' ? JSON.parse(match[1]) : match[1].slice(1, -1));
    }
  }
  const html = fs.readFileSync(path.join(root, 'desktop/renderer/index.html'), 'utf8');
  for (const match of html.matchAll(/data-i18n(?:-[a-z-]+)?="([^"]*)"/g)) {
    keys.add(match[1].replaceAll('&quot;', '"').replaceAll('&#x27;', "'").replaceAll('&amp;', '&'));
  }
  assert.ok(keys.size > 300);
  for (const key of keys) assert.ok(Object.hasOwn(locale.en, key), `Missing English: ${key}`);
});

test('catalog preserves interpolation slots and supplies English text', () => {
  const slots = value => [...value.matchAll(/\{\d+\}/g)].map(m => m[0]).sort();
  for (const [key, value] of Object.entries(locale.en)) {
    assert.deepEqual(slots(value), slots(key), key);
    assert.ok(value.trim());
    assert.doesNotMatch(value, /[가-힣]/u, key);
  }
  assert.equal(locale.translate('en', '검색 결과 {0}개 · 불러온 작업 {1}개', 1, 12), 'Matches: 1 · Loaded: 12');
  assert.equal(locale.translate('ko', '{0} 기록을 불러왔습니다.', '$& <script>한국어</script>'), '$& <script>한국어</script> 기록을 불러왔습니다.');
});

test('invalid preferences and missing keys fall back without losing original content', () => {
  for (const value of [null, undefined, {}, 'fr', 'EN']) assert.equal(locale.normalize(value), 'ko');
  assert.equal(locale.translate('en', 'A user title 한국어'), 'A user title 한국어');
  assert.equal(locale.translate('en', '__proto__'), '__proto__');
});

test('legacy app errors translate in both directions with raw diagnostics intact', () => {
  const raw = 'Python을 시작하지 못했습니다: C:\\한글 폴더\\python.exe EACCES';
  const translated = locale.message('en', raw);
  assert.equal(translated, 'Could not start Python: C:\\한글 폴더\\python.exe EACCES');
  assert.equal(locale.message('ko', translated), raw);
  assert.equal(locale.message('en', 'ENOENT /a/b'), 'ENOENT /a/b');
  assert.equal(locale.message('en', '설정이 변경됐습니다. 변경 내용 다시 보기를 눌러 주세요.'), 'Settings changed. Click Refresh preview.');
});

test('verification prompt keeps the exact request arguments and nonce in both languages', () => {
  const korean = '이 프로젝트의 jev_context MCP workspace_status 도구를 다음 인자로 직접 호출해 줘. 셸이나 CLI로 대신 실행하지 말고, 도구가 없으면 연결이 필요하다고 알려줘.\n{"request_id":"jev-connect-example","contract_version":"2.0"}';
  const english = locale.prompt('en', korean);
  assert.ok(english.startsWith('Call this project'));
  assert.equal(english.slice(english.indexOf('\n')), korean.slice(korean.indexOf('\n')));
  assert.equal(locale.prompt('ko', english), korean);
  assert.equal(locale.prompt('en', 'arbitrary\n한국어 data'), 'arbitrary\n한국어 data');
});
