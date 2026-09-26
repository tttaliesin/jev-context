'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_SIZE = { width: 1220, height: 860 };
const MIN_SIZE = { width: 940, height: 690 };

function validBounds(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && ['x', 'y', 'width', 'height'].every(key => Number.isSafeInteger(value[key]))
    && Math.abs(value.x) <= 1048576 && Math.abs(value.y) <= 1048576
    && value.width > 0 && value.width <= 32768 && value.height > 0 && value.height <= 32768;
}

function intersection(a, b) {
  return Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
    * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
}

function normalizeWindowState(saved, displays, primaryId) {
  const available = (Array.isArray(displays) ? displays : []).filter(display => validBounds(display?.workArea));
  const primary = available.find(display => display.id === primaryId) || available[0]
    || { workArea: { x: 0, y: 0, ...DEFAULT_SIZE } };
  const previous = validBounds(saved?.normalBounds) ? saved.normalBounds : null;
  let target = primary;
  let overlap = 0;
  if (previous) {
    for (const display of available) {
      const area = intersection(previous, display.workArea);
      if (area > overlap) { overlap = area; target = display; }
    }
  }
  const area = target.workArea;
  const minWidth = Math.min(MIN_SIZE.width, area.width);
  const minHeight = Math.min(MIN_SIZE.height, area.height);
  const width = Math.min(area.width, Math.max(minWidth, previous?.width || DEFAULT_SIZE.width));
  const height = Math.min(area.height, Math.max(minHeight, previous?.height || DEFAULT_SIZE.height));
  const x = overlap ? Math.max(area.x, Math.min(previous.x, area.x + area.width - width))
    : area.x + Math.floor((area.width - width) / 2);
  const y = overlap ? Math.max(area.y, Math.min(previous.y, area.y + area.height - height))
    : area.y + Math.floor((area.height - height) / 2);
  return { normalBounds: { x, y, width, height }, maximized: saved?.maximized === true, minWidth, minHeight };
}

function loadWindowState(filename, displays, primaryId) {
  let saved;
  try {
    // A window record is a few hundred bytes; never ingest an unexpectedly large file.
    if (fs.statSync(filename).size <= 16384) {
      const value = JSON.parse(fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, ''));
      if (value?.version === 1) saved = value;
    }
  } catch { /* Missing or corrupt state starts with safe centered defaults. */ }
  return normalizeWindowState(saved, displays, primaryId);
}

function saveWindowState(filename, state) {
  if (!validBounds(state?.normalBounds)) return false;
  const { x, y, width, height } = state.normalBounds;
  const record = { version: 1, normalBounds: { x, y, width, height }, maximized: state.maximized === true };
  const temporary = `${filename}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(temporary, `${JSON.stringify(record, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(temporary, filename);
    return true;
  } catch {
    try { fs.unlinkSync(temporary); } catch { /* A failed save must not prevent normal close. */ }
    return false;
  }
}

module.exports = { normalizeWindowState, loadWindowState, saveWindowState };
