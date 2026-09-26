'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { normalizeWindowState, loadWindowState, saveWindowState } = require('../desktop/window-state.cjs');

const primary = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
const secondary = { id: 2, workArea: { x: -1600, y: 30, width: 1600, height: 900 } };

test('normal bounds and maximized state survive while the same display exists', () => {
  const normalBounds = { x: -1500, y: 80, width: 1100, height: 780 };
  const restored = normalizeWindowState({ normalBounds, maximized: true }, [primary, secondary], 1);
  assert.deepEqual(restored.normalBounds, normalBounds);
  assert.equal(restored.maximized, true);
});

test('a disconnected screen falls back to a centered current primary screen', () => {
  const restored = normalizeWindowState({ normalBounds: { x: -1500, y: 80, width: 1100, height: 780 } }, [primary], 1);
  assert.deepEqual(restored.normalBounds, { x: 410, y: 130, width: 1100, height: 780 });
});

test('resolution shrink clamps bounds and relaxes the minimum on a small work area', () => {
  const small = { id: 1, workArea: { x: 40, y: 25, width: 800, height: 600 } };
  const restored = normalizeWindowState({ normalBounds: { x: 300, y: 150, width: 1220, height: 860 } }, [small], 1);
  assert.deepEqual(restored.normalBounds, small.workArea);
  assert.equal(restored.minWidth, 800);
  assert.equal(restored.minHeight, 600);
});

test('partly visible bounds fit fully in the display with the greatest overlap', () => {
  const restored = normalizeWindowState({ normalBounds: { x: -100, y: 800, width: 1000, height: 700 } }, [primary, secondary], 1);
  assert.deepEqual(restored.normalBounds, { x: 0, y: 340, width: 1000, height: 700 });
});

test('malformed, nonfinite and huge coordinates use centered defaults', () => {
  const normalBounds = { x: 200, y: 100, width: 1100, height: 780 };
  for (const invalid of [NaN, Infinity, -Infinity, 1e100, Number.MAX_SAFE_INTEGER, '1220', null, 1.5]) {
    for (const field of ['x', 'y', 'width', 'height']) {
      const restored = normalizeWindowState({ normalBounds: { ...normalBounds, [field]: invalid }, maximized: 'true' }, [primary], 1);
      assert.deepEqual(restored.normalBounds, { x: 350, y: 90, width: 1220, height: 860 });
      assert.equal(restored.maximized, false);
    }
  }
  assert.deepEqual(normalizeWindowState({ normalBounds: [] }, [primary], 1).normalBounds,
    { x: 350, y: 90, width: 1220, height: 860 });
});

test('window file round-trip is independent from project settings and corrupt state recovers', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jev-window-state-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filename = path.join(directory, 'window-state.json');
  const settings = path.join(directory, 'settings.json');
  fs.writeFileSync(settings, JSON.stringify({ configPath: 'first/project.toml' }));
  const state = { normalBounds: { x: 100, y: 80, width: 1000, height: 750 }, maximized: true };
  assert.equal(saveWindowState(filename, state), true);
  const first = fs.readFileSync(filename, 'utf8');
  fs.writeFileSync(settings, JSON.stringify({ configPath: 'second/project.toml' }));
  assert.equal(fs.readFileSync(filename, 'utf8'), first);
  const restored = loadWindowState(filename, [primary], 1);
  assert.deepEqual(restored.normalBounds, state.normalBounds);
  assert.equal(restored.maximized, true);
  assert.equal(saveWindowState(filename, { normalBounds: { ...state.normalBounds, width: 1e100 } }), false);
  assert.equal(fs.readFileSync(filename, 'utf8'), first);
  for (const invalid of ['{broken', 'x'.repeat(16385), '{"version":2}', 'null']) {
    fs.writeFileSync(filename, invalid);
    assert.deepEqual(loadWindowState(filename, [primary], 1).normalBounds,
      { x: 350, y: 90, width: 1220, height: 860 });
  }
});
