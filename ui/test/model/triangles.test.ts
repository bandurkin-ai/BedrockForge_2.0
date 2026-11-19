/** Э9, подшаг 9.33 (ADR 0015 §6–7; ТЗ 7.23): «Править треугольники» — a triangle is marked to be removed by the
 * exact pick of the core, a new one is added by three vertices snapped to the vertices of the wireframe (exact
 * float64), «Применить» starts `edit_triangles`: a new wireframe. */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Vec3 } from "../../src/edit/model";
import { createApp } from "../../src/shell/app";
import { editStore } from "../../src/modules/lines/editState";
import { applyReason, countText } from "../../src/modules/model/TrianglesBar";
import type { Started } from "../../src/modules/model/run";
import {
  addCorner,
  applyTriangles,
  cancelTriangles,
  dropLast,
  editParams,
  markData,
  markPick,
  newEdit,
  snapCorner,
  startTriangles,
  trianglesController,
  trianglesStore,
  type Corners,
  type TriEdit,
} from "../../src/modules/model/triangles";
import { sceneObjects } from "../../src/modules/scene/objects";
import type { WireframeInfo } from "../../src/modules/wireframes/state";
import { markPoints, pathEdges, triangleEdges } from "../../src/viz/edit/trianglesMark";
import type { ExactPick, PickRequest } from "../../src/viz/mesh/exact";
import type { Scene } from "../../src/viz/Scene";
import { ortho } from "../edit/testUtil";
import { EditSession } from "../../src/edit/session";
import { asLoaded } from "../../src/edit/io";
import { emptyLayer } from "../../src/edit/model";

const E0 = 512345.678901234;
const N0 = 6789012.345678901;
const A: Vec3 = [E0, N0, 301.123456789];
const B: Vec3 = [E0 + 10, N0, 302.5];
const C: Vec3 = [E0, N0 + 10, 303.25];
const D: Vec3 = [E0 + 10, N0 + 10, 304.0625];

const pick = (triangle: number, vertices: Corners, point: Vec3, wireframeId = 5): ExactPick => ({ wireframeId, name: "Тело", triangle, distance: 1, point, vertices, normal: [0, 0, 1], area: 50, dip: 3, dipDirection: 90 });
const answer = (p: ExactPick | null) => ({
  hit: p && { wireframe_id: p.wireframeId, name: p.name, triangle: p.triangle, distance: p.distance, point: p.point, vertices: p.vertices, normal: p.normal, area: p.area, dip: p.dip, dip_direction: p.dipDirection },
});

const wf = (id: number, name: string): WireframeInfo => ({ id, name, version: 1, color: null, point_count: 4, triangle_count: 2, extent: [E0, N0, 300, E0 + 10, N0 + 10, 305], dtm: null, folder: null, attrs: [], stats: { closed: false, open_edges: 4 } });

/** A plan view of 20 x 20 m on 400 x 400 px: (x, y) px ↔ project, the ray straight down. */
function fakeScene() {
  const toPx = (p: Vec3) => ({ x: (p[0] - (E0 - 5)) * 20, y: (N0 + 15 - p[1]) * 20 });
  const scene = {
    tool: null as { pointer: (e: unknown) => boolean } | null,
    setTool: vi.fn(function (this: void, t: { pointer: (e: unknown) => boolean } | null) {
      scene.tool = t;
    }),
    cursorRay: (x: number, y: number) => ({ origin: [E0 - 5 + x / 20, N0 + 15 - y / 20, 1000] as Vec3, dir: [0, 0, -1] as Vec3 }),
    projectView: () => ({ matrix: ortho(E0 - 5, E0 + 15, N0 - 5, N0 + 15, -2000, 2000), width: 400, height: 400 }),
  };
  return { scene, toPx, asScene: scene as unknown as Scene };
}
const clickAt = (scene: ReturnType<typeof fakeScene>["scene"], x: number, y: number, button = 0) => scene.tool!.pointer({ type: "click", x, y, clientX: x, clientY: y, button, shift: false, ctrl: false });
const flush = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  cancelTriangles();
  editStore.set({ session: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
});

describe("the edit of triangles (pure)", () => {
  it("a mark by the exact pick: the number of the triangle as stored; a second click takes it off; another wireframe — nothing", () => {
    let t = newEdit(5, "Тело");
    t = markPick(t, pick(17, [A, B, C], [E0 + 2, N0 + 2, 302])).edit;
    t = markPick(t, pick(3, [B, D, C], [E0 + 8, N0 + 8, 303])).edit;
    expect([...t.remove.keys()]).toEqual([17, 3]);
    expect(t.remove.get(17)).toEqual([A, B, C]);
    const off = markPick(t, pick(17, [A, B, C], A));
    expect([...off.edit.remove.keys()]).toEqual([3]);
    expect(off.message).toMatch(/снята.*№18/);
    expect(markPick(t, pick(9, [A, B, C], A, 6)).edit).toBe(t);
    expect(markPick(t, null).edit).toBe(t);
    expect(editParams(t)).toEqual({ wireframe_id: 5, remove: [3, 17], add: [] });
  });

  it("a vertex snaps to the nearest corner of the hit triangle on screen (else in 3D), exact to the bit", () => {
    const { scene, toPx } = fakeScene();
    const view = scene.projectView();
    const screen = (p: Vec3): [number, number] => {
      const m = view.matrix;
      return [((m[0] * p[0] + m[12]) + 1) * 0.5 * 400, (1 - (m[5] * p[1] + m[13])) * 0.5 * 400];
    };
    const p = pick(0, [A, B, C], [E0 + 1, N0 + 8, 303]);
    const nearC = toPx([E0 + 1, N0 + 8, 0]);
    const corner = snapCorner(p, nearC, screen);
    expect(corner).toBe(C);
    expect(snapCorner(pick(0, [A, B, C], [E0 + 9, N0 + 0.5, 302]))).toBe(B);
  });

  it("three vertices make a triangle; the same vertex twice is refused; «Убрать последнее» steps back", () => {
    let t: TriEdit = { ...newEdit(5, "Тело"), mode: "add" };
    t = addCorner(t, A).edit;
    const twice = addCorner(t, A);
    expect(twice.edit).toBe(t);
    expect(twice.message).toMatch(/уже выбрана/);
    t = addCorner(addCorner(t, B).edit, D).edit;
    expect(t.pending).toEqual([]);
    expect(t.add).toEqual([[A, B, D]]);
    expect(countText(t)).toBe("к удалению 0 · новых 1");
    t = addCorner(t, C).edit;
    expect(countText(t)).toBe("к удалению 0 · новых 1 · вершин 1 из 3");
    t = dropLast(t);
    expect(t.pending).toEqual([]);
    t = dropLast(t);
    expect(t.add).toEqual([]);
    expect(applyReason(t, false)).toMatch(/Отметьте/);
    const params = editParams(addCorner(addCorner(addCorner(t, D).edit, C).edit, A).edit);
    expect(params.add[0][2][2]).toBe(301.123456789);
  });

  it("the marks: float64 corners for the layer; edges of triangles and of the path", () => {
    let t = markPick(newEdit(5, "Тело"), pick(1, [A, B, C], A)).edit;
    t = { ...t, mode: "add", pending: [A, D] };
    const m = markData(t);
    expect(Array.from(m.remove)).toEqual([...A, ...B, ...C]);
    expect(m.pending.length).toBe(6);
    expect(markPoints(m).length).toBe(15);
    expect(Array.from(triangleEdges(Float32Array.from([0, 0, 0, 1, 0, 0, 0, 1, 0])))).toEqual([0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0]);
    expect(Array.from(pathEdges(Float32Array.from([0, 0, 0, 1, 1, 1, 2, 2, 2])))).toEqual([0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]);
  });
});

describe("«Править треугольники» in the 3D window", () => {
  it("clicks go as rays to the exact pick of this wireframe; marks follow; a stale answer is dropped", async () => {
    sceneObjects.set({ wireframes: [wf(5, "Тело")], loaded: true });
    const app = createApp();
    app.store.set({ selected: { kind: "wireframe", id: 5 } });
    const { scene, asScene } = fakeScene();
    const bodies: PickRequest[] = [];
    const waiting: ((v: unknown) => void)[] = [];
    const shown: (ReturnType<typeof markData> | null)[] = [];
    const text = startTriangles(app, null, { scene: () => asScene, pick: (b) => (bodies.push(b), new Promise((r) => waiting.push(r))), show: (_s, d) => shown.push(d) });
    expect(text).toMatch(/Правка треугольников «Тело»/);
    expect(scene.setTool).toHaveBeenCalledTimes(1);
    expect(scene.tool!.pointer({ type: "down", x: 1, y: 1, clientX: 1, clientY: 1, button: 0, shift: false, ctrl: false })).toBe(false); // a drag still turns the view

    clickAt(scene, 100, 100);
    clickAt(scene, 200, 200);
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toMatchObject({ wireframe_ids: [5], clip: null, direction: [0, 0, -1] });
    expect(bodies[0].origin[0]).toBeCloseTo(E0, 6);
    waiting[1](answer(pick(4, [B, D, C], D)));
    await flush();
    waiting[0](answer(pick(9, [A, B, C], A))); // the first click, answered late
    await flush();
    expect([...trianglesController()!.edit!.remove.keys()]).toEqual([4]);
    expect(Array.from(shown.at(-1)!.remove)).toEqual([...B, ...D, ...C]);

    // «Добавить»: three clicks, each takes the nearest vertex of the hit triangle
    trianglesController()!.setMode("add");
    const { toPx } = fakeScene();
    for (const [v, tri] of [[A, 0], [B, 0], [D, 1]] as [Vec3, number][]) {
      const at = toPx(v);
      clickAt(scene, at.x + 3, at.y - 3);
      waiting.at(-1)!(answer(pick(tri, tri ? [B, D, C] : [A, B, C], [v[0] + 0.1, v[1] + 0.1, v[2]])));
      await flush();
    }
    const t = trianglesController()!.edit!;
    expect(t.add).toEqual([[A, B, D]]);
    expect(Object.is(t.add[0][0][2], A[2])).toBe(true);
    // a click into a hole: no triangle under it, no vertex
    clickAt(scene, 5, 5);
    waiting.at(-1)!(answer(null));
    await flush();
    expect(trianglesStore.get().message).toMatch(/рядом с нужной вершиной/);
    // a right click steps back
    clickAt(scene, 5, 5, 2);
    expect(trianglesController()!.edit!.add).toEqual([]);
    clickAt(scene, toPx(A).x + 2, toPx(A).y + 2);
    waiting.at(-1)!(answer(pick(0, [A, B, C], A)));
    await flush();
    expect(trianglesController()!.edit!.pending).toEqual([A]);
  });

  it("«Применить» starts `edit_triangles` with the stored numbers and the exact corners; the tool leaves the view", async () => {
    sceneObjects.set({ wireframes: [wf(5, "Тело")], loaded: true });
    const app = createApp();
    const { scene, asScene, toPx } = fakeScene();
    const replies: ExactPick[] = [pick(7, [A, B, C], A), pick(2, [B, D, C], D)];
    startTriangles(app, 5, { scene: () => asScene, pick: async () => answer(replies.shift() ?? null), show: () => undefined });
    clickAt(scene, 10, 10);
    await flush();
    clickAt(scene, 20, 20);
    await flush();
    const c = trianglesController()!;
    c.setMode("add");
    const add: Corners = [A, B, D];
    for (const v of add) {
      replies.push(pick(1, [v, v === A ? B : A, C], v));
      clickAt(scene, toPx(v).x - 2, toPx(v).y + 1);
      await flush();
    }
    const start = vi.fn(async (): Promise<Started> => ({ op: "edit_triangles", title: "Править треугольники", job: "geometry.operation", queue: "quick", size: 2, sources: [], warning: null, source: "local", id: 44 }));
    const after = vi.fn(async () => undefined);
    const text = await applyTriangles(app, { start, after });
    expect(start).toHaveBeenCalledWith({ op: "edit_triangles", params: { wireframe_id: 5, remove: [2, 7], add: [add] } });
    expect(text).toMatch(/удаляется треугольников 2, добавляется 1 — новый каркас строится \(задание №44/);
    expect(scene.setTool).toHaveBeenLastCalledWith(null);
    expect(trianglesController()).toBeNull();
    expect(trianglesStore.get().edit).toBeNull();
  });

  it("refusals: no wireframe chosen, the string editor open, an empty edit", async () => {
    const app = createApp();
    expect(() => startTriangles(app)).toThrow(/Не выбран каркас/);
    sceneObjects.set({ wireframes: [wf(5, "Тело")], loaded: true });
    editStore.set({ session: new EditSession(asLoaded(1, 1, emptyLayer()), "Контуры") });
    expect(() => startTriangles(app, 5)).toThrow(/Открыт редактор стрингов/);
    editStore.set({ session: null });
    const { asScene } = fakeScene();
    startTriangles(app, 5, { scene: () => asScene, pick: async () => answer(null), show: () => undefined });
    await expect(applyTriangles(app)).rejects.toThrow(/Правка пуста/);
    // the string editor opened meanwhile: the fix ends
    editStore.set({ session: new EditSession(asLoaded(1, 1, emptyLayer()), "Контуры") });
    expect(trianglesController()).toBeNull();
    expect(trianglesStore.get().message).toMatch(/редактор стрингов/);
  });

  it("`model.triangles` and `model.assemble` are bound: a panel, not a window", async () => {
    const app = createApp();
    const { bindModel } = await import("../../src/modules/model/bind");
    bindModel(app);
    expect(app.registry.get("model.triangles")!.stage).toBeUndefined();
    expect(app.registry.get("model.triangles")!.params).toHaveProperty("wireframe_id");
    expect(app.registry.get("model.assemble")!.params).toHaveProperty("layer_id");
  });
});
