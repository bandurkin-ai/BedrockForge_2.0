import { describe, expect, it } from "vitest";
import { VizError } from "../src/viz/error";
import { bfa1Array, decodeBfa1 } from "../src/viz/format/bfa1";
import { align, build } from "./bfa1-build";

function code(fn: () => unknown): string {
  try {
    fn();
  } catch (e) {
    expect(e).toBeInstanceOf(VizError);
    const err = e as VizError;
    expect(err.message).toMatch(/[А-Яа-я]/);
    expect(err.hint.length).toBeGreaterThan(0);
    return err.code;
  }
  throw new Error("no error");
}

describe("BFA1 decoder", () => {
  it("decodes a minimal container written byte by byte", () => {
    // 39 bytes of header end at 47, padded to 48; the array is placed further, at 56 (any multiple of 8 after).
    const head = '{"kind":"t","arrays":[["a","<u2",3,56]]}';
    expect(align(8 + head.length)).toBeLessThanOrEqual(56);
    const buf = new ArrayBuffer(64);
    const b = new Uint8Array(buf);
    b.set([66, 70, 65, 49]);
    new DataView(buf).setUint32(4, head.length, true);
    b.set(new TextEncoder().encode(head), 8);
    new DataView(buf).setUint16(56, 7, true);
    new DataView(buf).setUint16(58, 65535, true);
    new DataView(buf).setUint16(60, 0, true);
    const doc = decodeBfa1(buf);
    expect(doc.header).toEqual({ kind: "t" });
    expect([...bfa1Array(doc, "a", "<u2", 3)]).toEqual([7, 65535, 0]);
  });

  it("gives views of the same buffer for every dtype, NaN kept", () => {
    const n = 3;
    const collar = new Float64Array([512345.678901, 7012345.123456, 250.75, NaN, -1e-12, 1e300, 0, 0, 0]);
    const az = new Float32Array([37.5, NaN, 359.9]);
    const buf = build({ kind: "holes", version: 7, names: ["Скв-1", "A", "B"] }, [
      ["id", "<u4", new Uint32Array([1, 2, 4294967295])],
      ["collar", "<f8", collar],
      ["plan_az", "<f4", az],
      ["code", "<i4", new Int32Array([-1, 0, 2147483647])],
      ["part", "<u2", new Uint16Array([0, 1, 65535])],
      ["kind", "<u1", new Uint8Array([0, 1, 2])],
      ["empty", "<f8", new Float64Array(0)],
    ]);
    const doc = decodeBfa1<{ kind: string; version: number; names: string[] }>(buf);
    expect(doc.header).toEqual({ kind: "holes", version: 7, names: ["Скв-1", "A", "B"] });
    const c = bfa1Array(doc, "collar", "<f8", 3 * n);
    expect(c).toBeInstanceOf(Float64Array);
    expect(c.buffer).toBe(buf); // no copy
    expect(c[0]).toBe(512345.678901);
    expect(c[1]).toBe(7012345.123456);
    expect(Number.isNaN(c[3])).toBe(true);
    expect(c[5]).toBe(1e300);
    const a = bfa1Array(doc, "plan_az", "<f4");
    expect(a[0]).toBe(37.5);
    expect(Number.isNaN(a[1])).toBe(true);
    expect([...bfa1Array(doc, "id", "<u4")]).toEqual([1, 2, 4294967295]);
    expect([...bfa1Array(doc, "code", "<i4")]).toEqual([-1, 0, 2147483647]);
    expect([...bfa1Array(doc, "part", "<u2")]).toEqual([0, 1, 65535]);
    expect([...bfa1Array(doc, "kind", "<u1")]).toEqual([0, 1, 2]);
    expect(bfa1Array(doc, "empty", "<f8", 0).length).toBe(0);
    for (const arr of Object.values(doc.arrays)) expect(arr.byteOffset % 8).toBe(0);
  });

  it("reads a view aligned to 8 in a bigger buffer without copying, a misaligned one with one copy", () => {
    const inner = build({ k: 1 }, [["v", "<f8", new Float64Array([1.5, 2.5])]]);
    const big = new ArrayBuffer(inner.byteLength + 16);
    new Uint8Array(big).set(new Uint8Array(inner), 8);
    const doc = decodeBfa1(new Uint8Array(big, 8, inner.byteLength));
    expect(doc.arrays.v.buffer).toBe(big);
    expect([...doc.arrays.v]).toEqual([1.5, 2.5]);
    const odd = new ArrayBuffer(inner.byteLength + 3);
    new Uint8Array(odd).set(new Uint8Array(inner), 3);
    const copy = decodeBfa1(new Uint8Array(odd, 3, inner.byteLength));
    expect([...copy.arrays.v]).toEqual([1.5, 2.5]);
  });

  it("refuses damaged containers with clear errors", () => {
    const good = build({}, [["v", "<f8", new Float64Array([1, 2])]]);
    expect(code(() => decodeBfa1(new ArrayBuffer(4)))).toBe("bfa1_truncated");
    const wrong = good.slice(0);
    new Uint8Array(wrong)[0] = 0x58;
    expect(code(() => decodeBfa1(wrong))).toBe("bfa1_magic");
    const long = good.slice(0);
    new DataView(long).setUint32(4, 1 << 30, true);
    expect(code(() => decodeBfa1(long))).toBe("bfa1_header_length");
    expect(code(() => decodeBfa1(good.slice(0, good.byteLength - 8)))).toBe("bfa1_bounds");
    expect(code(() => decodeBfa1(build({}, [["v", "<f8", new Float64Array([1])]], (t) => (t[0][1] = ">f8"))))).toBe("bfa1_dtype");
    expect(code(() => decodeBfa1(build({}, [["v", "<f8", new Float64Array([1])]], (t) => ((t[0][3] as number) += 4))))).toBe("bfa1_offset");
    expect(code(() => decodeBfa1(build({}, [["v", "<f8", new Float64Array([1])], ["v", "<u1", new Uint8Array([1])]])))).toBe("bfa1_table");
    expect(code(() => decodeBfa1(build({}, [["a", "<f8", new Float64Array([1, 2])], ["b", "<f8", new Float64Array([3])]], (t) => (t[1][3] = t[0][3]))))).toBe("bfa1_overlap");
    expect(code(() => decodeBfa1(build({}, [["v", "<f8", new Float64Array([1])]], (t) => (t[0][2] = -1))))).toBe("bfa1_table");
    let pad = new ArrayBuffer(0);
    for (let k = 0; k < 8; k++) {
      pad = build({ p: "x".repeat(k) }, [["v", "<f8", new Float64Array([1])]]);
      if ((8 + new DataView(pad).getUint32(4, true)) % 8 !== 0) break;
    }
    new Uint8Array(pad)[8 + new DataView(pad).getUint32(4, true)] = 1;
    expect(code(() => decodeBfa1(pad))).toBe("bfa1_padding");
    const noTable = new ArrayBuffer(16);
    new Uint8Array(noTable).set([66, 70, 65, 49]);
    new DataView(noTable).setUint32(4, 2, true);
    new Uint8Array(noTable).set(new TextEncoder().encode("{}"), 8);
    expect(code(() => decodeBfa1(noTable))).toBe("bfa1_header");
    const badJson = noTable.slice(0);
    new Uint8Array(badJson)[8] = 0x5b; // "[}"
    expect(code(() => decodeBfa1(badJson))).toBe("bfa1_header_json");
  });

  it("passes on the core's JSON error given instead of data", () => {
    const body = new TextEncoder().encode(JSON.stringify({ code: "set_not_found", message: "Набор не найден.", hint: "Обновите дерево." }));
    try {
      decodeBfa1(body);
      throw new Error("no error");
    } catch (e) {
      expect((e as VizError).code).toBe("set_not_found");
      expect((e as VizError).hint).toBe("Обновите дерево.");
    }
  });

  it("checks the expected array", () => {
    const doc = decodeBfa1(build({}, [["v", "<f4", new Float32Array([1, 2])]]));
    expect(code(() => bfa1Array(doc, "w", "<f4"))).toBe("bfa1_missing");
    expect(code(() => bfa1Array(doc, "v", "<f8"))).toBe("bfa1_dtype");
    expect(code(() => bfa1Array(doc, "v", "<f4", 3))).toBe("bfa1_count");
  });
});
