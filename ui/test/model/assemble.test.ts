/** Э9, подшаг 9.32 (ADR 0015 §6–7; ТЗ 7.23): «Собрать из контуров» — the contours in the order clicked (unsaved
 * ones too) and the tie lines between them, the preview a pause of 250 ms after the last change, an older answer
 * dropped, the triangles and the volume on the panel; «Создать каркас» saves the layer and starts `stitch`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { assemblyInputs, assemblyTies, PREVIEW_DELAY_MS, PreviewScheduler, previewBody, stitchParams, type AssemblyInputs } from "../../src/edit/assemble";
import { encodeBfa1 } from "../../src/edit/bfa1";
import { asLoaded } from "../../src/edit/io";
import { emptyLayer, fromArrays, type EditLayer, type Vec3 } from "../../src/edit/model";
import * as ops from "../../src/edit/ops";
import { EditSession } from "../../src/edit/session";
import type { EditPointer } from "../../src/edit/tools";
import { createApp } from "../../src/shell/app";
import { editStore } from "../../src/modules/lines/editState";
import { assembleStore, beginAssembly, cancelAssembly, createAssembled, readPreview } from "../../src/modules/model/assemble";
import type { Started } from "../../src/modules/model/run";
import { createReason, previewText } from "../../src/modules/model/AssembleBar";
import { ortho } from "../edit/testUtil";

const E0 = 512345.678901234;
const N0 = 6789012.345678901;

/** Three square contours on three levels, one above the other (10 m apart), and some open lines. */
function contoursLayer(): EditLayer {
  const sq = (z: number, d = 0): Vec3[] => [[E0 + d, N0 + d, z], [E0 + 60 - d, N0 + d, z], [E0 + 60 - d, N0 + 60 - d, z], [E0 + d, N0 + 60 - d, z]];
  const pts = [...sq(300), ...sq(310, 0.25), ...sq(320, 0.5)];
  const v = Float64Array.from(pts.flat());
  return fromArrays(v, [0, 4, 8, 12], [1, 1, 1], [{}, {}, {}]);
}

/** A session looking down on a 200 x 200 m patch, 400 x 400 px (2 px per metre). */
function session(layer = contoursLayer()): EditSession {
  const s = new EditSession(asLoaded(7, 3, layer), "Контуры", "#ff8800");
  const transform = { matrix: ortho(E0 - 20, E0 + 180, N0 - 20, N0 + 180, -2000, 2000), width: 400, height: 400 };
  s.view = { transform: () => transform, ray: (cx, cy) => ({ origin: [E0 - 20 + cx / 2, N0 + 180 - cy / 2, 1000], dir: [0, 0, -1] }) };
  s.viewChanged();
  return s;
}
/** The screen point of a project point (plan). */
const px = (x: number, y: number) => ({ x: (x - (E0 - 20)) * 2, y: (N0 + 180 - y) * 2 });
const click = (s: EditSession, x: number, y: number) => {
  const p = px(x, y);
  const e: EditPointer = { type: "click", x: p.x, y: p.y, clientX: p.x, clientY: p.y, button: 0, shift: false, ctrl: false };
  s.pointer(e);
};
/** A click on the left edge of contour k (they are inset by 0.25 m each: the nearest segment is its own). */
const clickContour = (s: EditSession, k: number) => click(s, E0 + k * 0.25, N0 + 30);

/** BFA1 `mesh` as `ops/preview.py` writes it. */
function meshAnswer(triangles: number, volume: number | null, closed = volume !== null): ArrayBuffer {
  const vertices = triangles + 2;
  const xyz = new Float32Array(vertices * 3).map((_, i) => i * 0.5);
  const idx = new Uint32Array(triangles * 3).map((_, i) => (i % 3) + Math.floor(i / 3));
  return encodeBfa1({ kind: "mesh", format: 1, preview: true, origin: [512376, 6789042, 310], triangles, vertices, closed, volume, warnings: [], index: "<u4" }, [["xyz", "<f4", xyz], ["idx", "<u4", idx]]);
}

describe("the inputs of the assembly (edit/assemble.ts)", () => {
  it("the contours go in the order clicked; a second click takes one out; float64 as they are", () => {
    const s = session();
    s.startAssemble();
    clickContour(s, 2);
    clickContour(s, 0);
    clickContour(s, 1);
    const ids = s.layer.lines.map((l) => l.id);
    expect(s.assemble?.order).toEqual([ids[2], ids[0], ids[1]]);
    clickContour(s, 0);
    expect(s.assemble?.order).toEqual([ids[2], ids[1]]);
    const body = previewBody(assemblyInputs(s.layer, s.assemble!.order, false));
    expect(body.contours).toHaveLength(2);
    expect(body.contours[0][0]).toEqual([E0 + 0.5, N0 + 0.5, 320]);
    expect(body.contours[1][2]).toEqual([E0 + 60 - 0.25, N0 + 60 - 0.25, 310]);
    expect([body.start_cap, body.end_cap]).toEqual(["none", "none"]);
    expect(previewBody(assemblyInputs(s.layer, s.assemble!.order, true)).start_cap).toBe("flat");
  });

  it("an open line is no contour; a vertex without a height goes as null, never as 0", () => {
    let layer = contoursLayer();
    const open = ops.addLine(layer, [[E0, N0 - 10, 300], [E0 + 10, N0 - 10, 300], [E0 + 20, N0 - 10, 300]], false);
    layer = ops.moveVertex(open.layer, { line: layer.lines[1].id, index: 0 }, [E0 + 0.25, N0 + 0.25, NaN]);
    const inputs = assemblyInputs(layer, [layer.lines[0].id, open.id, layer.lines[1].id], false);
    expect(inputs.contours.map((c) => c.id)).toEqual([layer.lines[0].id, layer.lines[1].id]);
    expect(previewBody(inputs).contours[1][0]).toEqual([E0 + 0.25, N0 + 0.25, null]);
  });

  it("tie lines: marked or of two vertices, both ends on vertices of the picked contours", () => {
    let layer = contoursLayer();
    const [a, b, c] = layer.lines;
    const add = (pts: Vec3[], tie: boolean) => {
      const made = ops.addLine(layer, pts, false, tie ? { ROLE: "TIE" } : {});
      layer = made.layer;
      return made.id;
    };
    const marked = add([a.points[0], b.points[1]], true);
    const plain = add([a.points[2], b.points[2]], false);
    const otherPair = add([b.points[0], c.points[0]], true); // c is not picked
    const off = add([a.points[3], [E0 + 1, N0 + 59, 310]], false); // an end off the vertices
    const three = add([a.points[1], [E0 + 61, N0, 305], b.points[1]], false); // unmarked of three vertices
    const ties = assemblyTies(layer, [a, b]).map((l) => l.id);
    expect(ties).toEqual([marked, plain]);
    expect(ties).not.toContain(otherPair);
    expect(ties).not.toContain(off);
    expect(ties).not.toContain(three);
    const body = previewBody(assemblyInputs(layer, [a.id, b.id], true));
    expect(body.ties).toEqual([[a.points[0], b.points[1]], [a.points[2], b.points[2]]]);
  });

  it("the operation takes the stable numbers: contours in the order clicked, then the ties; unsaved — none", () => {
    const layer = contoursLayer();
    const [a, b, c] = layer.lines;
    const inputs = assemblyInputs(layer, [c.id, a.id, b.id], true);
    expect(stitchParams(7, inputs)).toEqual({ contours: [{ layer_id: 7, lines: [2, 0, 1] }], start_cap: "flat", end_cap: "flat", cap_distance: 0.5 });
    const made = ops.addLine(layer, [[E0, N0 + 100, 330], [E0 + 9, N0 + 100, 330], [E0 + 9, N0 + 109, 330]], true);
    expect(stitchParams(7, assemblyInputs(made.layer, [a.id, made.id], false))).toBeNull();
  });
});

describe("the scheduler of the preview: a pause of 250 ms, older answers dropped", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  const inputsOf = (layer: EditLayer, k: number[], closed = false): AssemblyInputs => assemblyInputs(layer, k.map((i) => layer.lines[i].id), closed);

  it("asks once, 250 ms after the last change; the same inputs ask nothing", async () => {
    const layer = contoursLayer();
    const fetch = vi.fn(async () => 1);
    const results: number[] = [];
    const busy: boolean[] = [];
    const sch = new PreviewScheduler(fetch, { result: (v) => results.push(v), error: () => undefined, busy: (b) => busy.push(b) });
    sch.request(inputsOf(layer, [0]));
    expect(busy).toEqual([]); // one contour: nothing to ask
    sch.request(inputsOf(layer, [0, 1]));
    await vi.advanceTimersByTimeAsync(200);
    sch.request(inputsOf(layer, [0, 1, 2]));
    sch.request(inputsOf(layer, [0, 1, 2])); // the same lines: no restart
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS - 1);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect((fetch.mock.calls[0] as unknown as [{ contours: unknown[] }])[0].contours).toHaveLength(3);
    expect(results).toEqual([1]);
    expect(busy.at(-1)).toBe(false);
  });

  it("a newer request aborts the one in flight; the late answer of the old one is not delivered", async () => {
    const layer = contoursLayer();
    const pending: { resolve: (v: string) => void; signal: AbortSignal; n: number }[] = [];
    const fetch = (body: { contours: unknown[] }, signal: AbortSignal) => new Promise<string>((resolve) => pending.push({ resolve, signal, n: body.contours.length }));
    const results: string[] = [];
    const errors: unknown[] = [];
    const sch = new PreviewScheduler(fetch, { result: (v) => results.push(v), error: (e) => errors.push(e), busy: () => undefined });
    sch.request(inputsOf(layer, [0, 1]));
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    sch.request(inputsOf(layer, [0, 1, 2]));
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(pending.map((p) => [p.n, p.signal.aborted])).toEqual([[2, true], [3, false]]);
    pending[1].resolve("три");
    pending[0].resolve("два");
    await vi.advanceTimersByTimeAsync(0);
    expect(results).toEqual(["три"]);
    // a change of the caps asks again; a failure of the latest is reported
    sch.request(inputsOf(layer, [0, 1, 2], true));
    const failing = new PreviewScheduler<string>(async () => Promise.reject(new Error("нет")), { result: () => undefined, error: (e) => errors.push(e), busy: () => undefined });
    failing.request(inputsOf(layer, [0, 1]));
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(errors.map((e) => (e as Error).message)).toEqual(["нет"]);
    expect(pending.map((p) => p.n)).toEqual([2, 3, 3]); // the caps asked again after their pause
    sch.dispose();
    expect(pending[2].signal.aborted).toBe(true);
    pending[2].resolve("поздно");
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(results).toEqual(["три"]);
  });
});

describe("the preview on the panel: triangles and volume", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cancelAssembly();
    editStore.set({ session: null });
    vi.useRealTimers();
  });

  it("reads BFA1 `mesh`: the whole-metre origin, float32 offsets, the corners", () => {
    const p = readPreview(meshAnswer(12, 1000));
    expect([p.triangles, p.vertices, p.closed, p.volume]).toEqual([12, 14, true, 1000]);
    expect(p.mesh.origin).toEqual({ x: 512376, y: 6789042, z: 310 });
    expect(p.mesh.xyz).toBeInstanceOf(Float32Array);
    expect(p.mesh.idx.length).toBe(36);
    const broken = encodeBfa1({ kind: "mesh", origin: [0.5, 0, 0], triangles: 1, vertices: 3 }, [["xyz", "<f4", new Float32Array(9)], ["idx", "<u4", new Uint32Array([0, 1, 2])]]);
    expect(() => readPreview(broken)).toThrow(/целого начала/);
  });

  it("the picks of the session ask the preview; the panel shows the count, triangles and volume; a refusal shows its reason", async () => {
    const s = session();
    editStore.set({ session: s });
    const shown: (unknown | null)[] = [];
    const bodies: { start_cap: string; contours: unknown[] }[] = [];
    let answer: () => ArrayBuffer = () => meshAnswer(24, null);
    const c = beginAssembly(s, { fetch: async (body) => (bodies.push(body), answer()), show: (m) => shown.push(m) });
    expect(assembleStore.get()).toMatchObject({ active: true, contours: 0 });
    expect(previewText(assembleStore.get())).toBe("Щёлкните первый контур");
    clickContour(s, 0);
    clickContour(s, 1);
    expect(assembleStore.get()).toMatchObject({ contours: 2, busy: true });
    expect(previewText(assembleStore.get())).toBe("Каркас строится…");
    s.hover = { point: null, snap: null, x: 1, y: 1 };
    s.touch(); // a hover: nothing is counted again
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(bodies).toHaveLength(1);
    expect(assembleStore.get().preview).toMatchObject({ triangles: 24, volume: null, closed: false });
    expect(previewText(assembleStore.get())).toBe("треугольников 24 · торцы открыты");
    expect(shown.at(-1)).toMatchObject({ origin: { x: 512376 } });

    answer = () => meshAnswer(1000, 36000.04);
    clickContour(s, 2);
    s.setAssembleClosed(true);
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toMatchObject({ start_cap: "flat" });
    expect(bodies[1].contours).toHaveLength(3);
    expect(previewText(assembleStore.get())).toBe(`треугольников ${(1000).toLocaleString("ru-RU")} · объём ${(36000).toLocaleString("ru-RU")} м³`);
    expect(createReason(assembleStore.get())).toBeNull();

    const { CoreError } = await import("../../src/shell/api");
    answer = () => {
      throw new CoreError(422, "stitch_ties", "Тай-линия 1 должна соединять контуры соседних разрезов.", "Переставьте тай-линию.");
    };
    s.dropLastContour();
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(assembleStore.get().problem).toMatch(/Тай-линия 1/);
    expect(createReason(assembleStore.get())).toMatch(/не сшиваются/);
    expect(shown.at(-1)).toBeNull();

    s.escape(); // Esc leaves the assembly: the preview goes, the panel closes
    expect(c.session.assemble).toBeNull();
    expect(assembleStore.get().active).toBe(false);
  });
});

describe("«Создать каркас»", () => {
  afterEach(() => {
    cancelAssembly();
    editStore.set({ session: null });
  });

  it("an unsaved contour: the layer is saved first, then `stitch` over the contours in the order clicked and the ties", async () => {
    const s = session();
    editStore.set({ session: s });
    const app = createApp();
    beginAssembly(s, { fetch: async () => meshAnswer(8, 1), show: () => undefined, delay: 0 });
    // a new contour drawn in this session, above the others
    const made = ops.addLine(s.layer, [[E0 + 0.75, N0 + 0.75, 330], [E0 + 59.25, N0 + 0.75, 330], [E0 + 59.25, N0 + 59.25, 330], [E0 + 0.75, N0 + 59.25, 330]], true);
    s.edit(made.layer, "Новая линия");
    const [a, b] = s.layer.lines;
    const tie = ops.addLine(s.layer, [a.points[0], b.points[0]], false, { ROLE: "TIE" });
    s.edit(tie.layer, "Тай-линия");
    s.pickContour(b.id);
    s.pickContour(a.id);
    s.pickContour(made.id);
    expect(s.dirty).toBe(true);
    const save = vi.fn(async () => {
      s.saved(asLoaded(s.layerId, 4, s.layer));
      return "Сохранено";
    });
    const start = vi.fn(async (): Promise<Started> => ({ op: "stitch", title: "Сшить контуры", job: "geometry.operation", queue: "quick", size: 0, sources: [], warning: null, source: "local", id: 31 }));
    const after = vi.fn(async () => undefined);
    const text = await createAssembled(app, { save, start, after });
    expect(save).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledWith({ op: "stitch", params: { contours: [{ layer_id: 7, lines: [1, 0, 3, 4] }], start_cap: "none", end_cap: "none", cap_distance: 0.5 } });
    expect(after).toHaveBeenCalledTimes(1);
    expect(text).toMatch(/Каркас из 3 контуров слоя «Контуры» строится: задание №31/);
    expect(s.assemble).toBeNull();
    expect(assembleStore.get().active).toBe(false);
    expect(app.store.get().workspace).toBeTruthy();
  });

  it("a save that did not happen (conflict left, or cancelled) starts nothing", async () => {
    const s = session();
    editStore.set({ session: s });
    beginAssembly(s, { fetch: async () => meshAnswer(8, 1), show: () => undefined, delay: 0 });
    s.pickContour(s.layer.lines[0].id);
    s.pickContour(s.layer.lines[1].id);
    s.edit(ops.moveVertex(s.layer, { line: s.layer.lines[0].id, index: 0 }, [E0 - 1, N0 - 1, 300]), "Перемещение");
    const start = vi.fn();
    await expect(createAssembled(createApp(), { save: async () => "Отмена", start })).rejects.toThrow(/слой не сохранён/);
    expect(start).not.toHaveBeenCalled();
    expect(s.assemble).not.toBeNull();
    expect(assembleStore.get().creating).toBe(false);
  });

  it("fewer than two contours — a refusal with a hint", async () => {
    const s = session(emptyLayer());
    editStore.set({ session: s });
    beginAssembly(s, { fetch: async () => meshAnswer(1, null), show: () => undefined });
    await expect(createAssembled(createApp())).rejects.toThrow(/не меньше двух/);
  });
});
