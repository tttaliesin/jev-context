'use strict';
// Development-only asset generation. Requires sharp; packaged apps use the saved assets.
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');

const assets = path.resolve(__dirname, '..', 'desktop', 'assets');
const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];

function dib(rgba, size) {
  const maskStride = Math.ceil(size / 32) * 4;
  const pixelBytes = size * size * 4;
  const data = Buffer.alloc(40 + pixelBytes + maskStride * size);
  data.writeUInt32LE(40, 0);
  data.writeInt32LE(size, 4);
  data.writeInt32LE(size * 2, 8);
  data.writeUInt16LE(1, 12);
  data.writeUInt16LE(32, 14);
  data.writeUInt32LE(pixelBytes + maskStride * size, 20);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const src = (y * size + x) * 4;
      const dest = 40 + ((size - 1 - y) * size + x) * 4;
      data[dest] = rgba[src + 2];
      data[dest + 1] = rgba[src + 1];
      data[dest + 2] = rgba[src];
      data[dest + 3] = rgba[src + 3];
      if (!rgba[src + 3]) data[40 + pixelBytes + (size - 1 - y) * maskStride + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return data;
}

(async () => {
  const svg = fs.readFileSync(path.join(assets, 'jev-mark.svg'));
  const frames = [];
  for (const size of sizes) {
    const source = sharp(svg, { density: size * 72 / 32 }).resize(size, size).ensureAlpha();
    const png = await source.clone().png().toBuffer();
    if (size === 256) fs.writeFileSync(path.join(assets, 'jev-icon.png'), png);
    frames.push({ size, data: size === 256 ? png : dib(await source.raw().toBuffer(), size) });
  }
  const directory = Buffer.alloc(6 + 16 * frames.length);
  directory.writeUInt16LE(1, 2);
  directory.writeUInt16LE(frames.length, 4);
  let offset = directory.length;
  frames.forEach(({ size, data }, index) => {
    const entry = 6 + index * 16;
    directory[entry] = directory[entry + 1] = size === 256 ? 0 : size;
    directory.writeUInt16LE(1, entry + 4);
    directory.writeUInt16LE(32, entry + 6);
    directory.writeUInt32LE(data.length, entry + 8);
    directory.writeUInt32LE(offset, entry + 12);
    offset += data.length;
  });
  fs.writeFileSync(path.join(assets, 'jev-icon.ico'), Buffer.concat([directory, ...frames.map(frame => frame.data)]));
  console.log(JSON.stringify({ sizes, output: assets }));
})().catch(error => { console.error(error); process.exitCode = 1; });
