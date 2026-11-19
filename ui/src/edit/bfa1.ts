/**
 * BFA1 encoder for the bodies the editor sends to the core (ADR 0007 §1; the reader is
 * `viz/format/bfa1.ts`, the reference is core/bf_core/viz/binary.py):
 *
 *     "BFA1"  u32 LE L  header (L bytes of UTF-8 JSON)  zeros up to a multiple of 8  arrays…
 *
 * `arrays: [[name, dtype, count, offset], …]` in the header; every offset is absolute and a multiple of 8.
 * The bytes of the arrays are copied as they are (little-endian platforms only, as the reader).
 */
import type { Bfa1Array, Bfa1Dtype } from "../viz/format/bfa1";

export type Bfa1Entry = [name: string, dtype: Bfa1Dtype, data: Bfa1Array];

const align8 = (n: number): number => Math.ceil(n / 8) * 8;

export function encodeBfa1(header: Record<string, unknown>, arrays: readonly Bfa1Entry[]): ArrayBuffer {
  const encoder = new TextEncoder();
  // The offsets are written into the header, whose length moves the start: grow the start until it fits.
  let start = 0;
  for (;;) {
    const table: [string, Bfa1Dtype, number, number][] = [];
    let at = start;
    for (const [name, dtype, data] of arrays) {
      table.push([name, dtype, data.length, at]);
      at = align8(at + data.byteLength);
    }
    const head = encoder.encode(JSON.stringify({ ...header, arrays: table }));
    const need = align8(8 + head.length);
    if (need <= start) {
      const out = new ArrayBuffer(Math.max(at, need));
      const bytes = new Uint8Array(out);
      bytes.set([0x42, 0x46, 0x41, 0x31]);
      new DataView(out).setUint32(4, head.length, true);
      bytes.set(head, 8);
      arrays.forEach(([, , data], i) => {
        bytes.set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), table[i][3]);
      });
      return out;
    }
    start = need;
  }
}
