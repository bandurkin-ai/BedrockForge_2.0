// Э18, 18.27в — a gap of an answer of the core is never turned into 0 (8.0.1): the readers of the checks and of the preview give null or
// refuse with `bad_answer` ({code, message, hint} in Russian) instead of [0, 0, 0], `value ?? 0`, `status ?? 0`, `ang ?? 0`.
// The answers are the real ones (`contract_answers.json`) with one field taken out.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { CoreError } from "../../src/shell/api";
import { loadQq, readGlobal, readKna, readQq } from "../../src/modules/estimation/checksApi";
import { readPreview } from "../../src/modules/estimation/previewApi";
import { A, installCore, newApp, teardown, type Core } from "./harness";

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

let core: Core;
beforeEach(() => {
  core = installCore();
  newApp();
});
afterEach(teardown);

describe("the preview", () => {
  it("the real answer is read as it is", () => {
    const p = readPreview(A.preview.body);
    expect(p.at).toEqual(A.preview.body.at);
    expect(p.status).toBe(0);
    expect(p.ellipsoid).toMatchObject({ r1: 60, r3: 30, pass: 1 });
  });
  it("no status, no row or no counts is not an answer: `bad_answer` with a hint, not «оценён» of the block 0", () => {
    for (const cut of [(a: Record<string, unknown>) => delete a.status, (a: Record<string, unknown>) => delete (a.at as Record<string, unknown>).row, (a: Record<string, unknown>) => delete a.counts]) {
      const a = clone(A.preview.body);
      cut(a);
      let e: unknown = null;
      try {
        readPreview(a);
      } catch (x) {
        e = x;
      }
      expect(e).toBeInstanceOf(CoreError);
      expect(e).toMatchObject({ code: "bad_answer", message: "Ядро ответило предпросмотру блока в непонятном виде." });
      expect((e as CoreError).hint).toContain("Повторите");
    }
  });
  it("a sample without a number, a pass without a number: refused; the ellipsoid with a radius missing is not drawn (null), not a radius 0", () => {
    const s = clone(A.preview.body);
    delete s.samples[0].i;
    expect(() => readPreview(s)).toThrow("непонятном виде");
    const p = clone(A.preview.body);
    delete p.passes[0].n;
    expect(() => readPreview(p)).toThrow("непонятном виде");
    const e = clone(A.preview.body);
    delete e.ellipsoid.r2;
    expect(readPreview(e).ellipsoid).toBeNull();
    const n = clone(A.preview.body);
    delete n.ellipsoid;
    expect(readPreview(n).ellipsoid).toBeNull();
  });
  it("a gap of a value stays null", () => {
    const a = clone(A.preview.body);
    a.samples[0].z = null;
    a.samples[0].lambda = null;
    delete a.samples[0].dist;
    const r = readPreview(a);
    expect(r.samples[0]).toMatchObject({ z: null, lambda: null, dist: null });
    expect(r.at.ijk).toEqual([4, 4, 7]);
    delete a.at.ijk;
    expect(readPreview(a).at.ijk).toBeNull();
  });
});

describe("the analysis of the neighbourhood", () => {
  it("the real answer is read: the places, the sizes and the variants are numbers", () => {
    const k = readKna(A.kna.body)!;
    expect(k.blocks[0].at.ijk).toEqual([4, 4, 7]);
    expect(k.blocks[0].centre).toHaveLength(3);
    expect(Object.values(k.blocks[0].vary).flat().every((v) => Number.isFinite(v!.value))).toBe(true);
  });
  it("a block without a place, a size, a centre, a count of the samples or a status is not an answer (null → «ядро вернуло анализ без итогов»)", () => {
    const cuts: ((b: Record<string, unknown>) => void)[] = [
      (b) => delete (b.at as Record<string, unknown>).ijk,
      (b) => delete (b.at as Record<string, unknown>).row,
      (b) => delete b.centre,
      (b) => delete b.size,
      (b) => delete b.n_r1,
      (b) => delete (b.base as Record<string, unknown>).status,
    ];
    for (const cut of cuts) {
      const a = clone(A.kna.body);
      cut(a.blocks[0]);
      expect(readKna(a)).toBeNull();
    }
  });
  it("a variant without a value or a status is not an answer, not a variant «0»", () => {
    const a = clone(A.kna.body);
    const key = Object.keys(a.blocks[0].vary)[0];
    delete a.blocks[0].vary[key][0].value;
    expect(readKna(a)).toBeNull();
    const b = clone(A.kna.body);
    delete b.blocks[0].vary[key][0].status;
    expect(readKna(b)).toBeNull();
  });
  it("a gap of an indicator stays null", () => {
    const a = clone(A.kna.body);
    a.blocks[0].base.KE = null;
    delete a.blocks[0].base.SLOPE;
    expect(readKna(a)!.blocks[0].base).toMatchObject({ KE: null, SLOPE: null, status: 0 });
  });
});

describe("the global check and the Q-Q", () => {
  it("the real answer is read; a count of the blocks that is missing is not «0 блоков»", () => {
    expect(readGlobal(A.global.body)!.blocks.rows).toBe(A.global.body.blocks.rows);
    const a = clone(A.global.body);
    delete a.blocks.used;
    expect(readGlobal(a)).toBeNull();
  });
  it("the Q-Q without points is `bad_answer` of the core with a hint (not a bare Error)", async () => {
    expect(readQq({ ...A.qq.body, points: undefined })).toBeNull();
    core.force("POST /geostat/estimate/qq", { status: 200, body: { estimation_id: 4 } });
    const e = await loadQq(4, true).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(CoreError);
    expect(e).toMatchObject({ code: "bad_answer", message: "Ядро вернуло Q-Q без точек." });
    expect((e as CoreError).hint).toContain("Повторите");
  });
});

describe("the rows of the project", () => {
  it("a row changed since its details were read: the new read of the details that fails is not an unhandled rejection", async () => {
    const rejected: unknown[] = [];
    const on = (e: unknown) => rejected.push(e);
    process.on("unhandledRejection", on);
    try {
      await loadRows();
      const row = estimationStore.get().rows[0];
      const old = { ...(A.estimation.body as Record<string, unknown>), id: row.id, version: row.version - 1 };
      estimationStore.set({ details: { [row.id]: old as never } });
      core.force(`GET /geostat/estimations/${row.id}`, { status: 500, body: { code: "boom", message: "Ядро не ответило.", hint: "Повторите." } });
      await loadRows();
      await new Promise((r) => setTimeout(r, 50));
      expect(core.sent.filter((x) => x.path === `/geostat/estimations/${row.id}`)).toHaveLength(1);
      expect(rejected).toEqual([]);
      expect(estimationStore.get().details[row.id]).toBeUndefined();
    } finally {
      process.off("unhandledRejection", on);
    }
  });
});
