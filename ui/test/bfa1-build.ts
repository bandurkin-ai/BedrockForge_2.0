/** BFA1 written by hand after ADR 0007 §1 (shared by the tests). */
type Typed = Float64Array | Float32Array | Int32Array | Uint32Array | Uint16Array | Uint8Array;
export const align = (n: number) => Math.ceil(n / 8) * 8;

/**
 * A BFA1 container written by hand after its description (ADR 0007 §1): "BFA1", u32 LE header length,
 * JSON header, zeros to 8, arrays at absolute offsets that are multiples of 8.
 */
export function build(header: Record<string, unknown>, arrays: [string, string, Typed][], patch?: (table: unknown[][]) => void): ArrayBuffer {
  let start = 0;
  for (;;) {
    const table: unknown[][] = [];
    let at = start;
    for (const [name, dtype, a] of arrays) {
      table.push([name, dtype, a.length, at]);
      at = align(at + a.byteLength);
    }
    patch?.(table);
    const head = new TextEncoder().encode(JSON.stringify({ ...header, arrays: table }));
    const need = align(8 + head.length);
    if (need <= start) {
      const out = new ArrayBuffer(Math.max(at, need));
      const bytes = new Uint8Array(out);
      bytes.set([0x42, 0x46, 0x41, 0x31]);
      new DataView(out).setUint32(4, head.length, true);
      bytes.set(head, 8);
      arrays.forEach(([, , a], i) => {
        const off = table[i][3] as number;
        if (off + a.byteLength <= out.byteLength) bytes.set(new Uint8Array(a.buffer, a.byteOffset, a.byteLength), off);
      });
      return out;
    }
    start = need;
  }
}

