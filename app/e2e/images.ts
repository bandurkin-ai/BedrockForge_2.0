// Images for the E2E written by hand (no image library in the tests).
import zlib from "node:zlib";

function crc32(data: Buffer): number {
  let c = ~0;
  for (const b of data) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

/**
 * An 8-bit RGB PNG whose colour tells the place in the image: red grows to the right (0…255 over the width),
 * green — downwards (0…255 over the height), blue is 160.
 */
export function gradientPng(w: number, h: number): Buffer {
  const row = w * 3 + 1;
  const raw = Buffer.alloc(row * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) raw.set([(x * 255) / w, (y * 255) / h, 160], y * row + 1 + x * 3);
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out = Buffer.alloc(8 + body.length);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), 4 + body.length);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(w, 0);
  head.writeUInt32BE(h, 4);
  head.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
