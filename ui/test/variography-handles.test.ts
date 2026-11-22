// The ellipsoid of a window «Вариография» in the main 3D (ТЗ 7.35 Д-14; ADR 0022 §9, решения 17–19; Э16 16.19): the
// layer follows the working model of the window (one store), the handles of the structure chosen drag only their own
// parameter into that store (steps 1°, ranges ≥ L / 2, `Ctrl` — one ratio, «общие оси» — every structure turns), a
// cancel brings the press back, the frame of an edit is measured (`vario:edit` → `vario:drawn`, ≤ 50 ms), the commands
// `variography.ellipsoid` / `variography.show` open the window of the model and show its ellipsoid. A fake scene.
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DefaultsAnswer } from "../src/modules/variography/api";
import { bindVariography, ellipsoidPatch } from "../src/modules/variography/bind";
import { commands } from "../src/modules/variography/commands";
import type { VModel } from "../src/modules/variography/model";
import { ellipsoidLayerKey, frameTimes, VarioScene, viewsOf } from "../src/modules/variography/scene";
import { chosenStructure, ellipsoidCentre, leastRange, newWork, setEllipsoid, setWork, variographyStore, windowOfModel, withStructure, workOf } from "../src/modules/variography/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeWindow, openWindow, windowStore } from "../src/shell/windows";
import { projectMatrix, projectRay, type ToolPointer } from "../src/viz/core/toolInput";
import type { Vec3 } from "../src/viz/core/vec3";
import { lookFrom } from "../src/viz/core/views";
import { handlesOf, pointAt, type ArcHandle, type KnobHandle } from "../src/viz/ellipsoid/drag";
import { EllipsoidHandles } from "../src/viz/ellipsoid/handle";
import { EllipsoidLayer } from "../src/viz/ellipsoid/layer";
import { projectToScreen, viewProjection, type Mat4 } from "../src/viz/pick/ray";
import type { Scene } from "../src/viz/Scene";
import type { SceneLayer } from "../src/viz/sceneTypes";

const W = 800;
const H = 600;
const ORIGIN = { x: 512_000, y: 7_100_000, z: 0 };
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
// from above over the ellipsoid (centre 512 000, 7 100 000, 250 — UTM)
const cam = lookFrom([0, -1e-4, 1], { min: [-200, -200, 150], max: [200, 200, 350] }, opts);
const matrix: Mat4 = projectMatrix(viewProjection(cam, opts), ORIGIN, 1);
const view = { matrix, width: W, height: H };
const screen = (p: Vec3) => {
  const s = projectToScreen(matrix, p[0], p[1], p[2], W, H)!;
  return { x: s[0], y: s[1] };
};
const CENTRE: Vec3 = [512_000, 7_100_000, 250];
const MODEL: VModel = {
  type: "semivariogram",
  c0: 0.2,
  common_axes: true,
  structures: [
    { type: "spherical", c: 0.5, ang1: 30, ang2: 0, ang3: 0, a1: 60, a2: 40, a3: 10 },
    { type: "spherical", c: 1, ang1: 30, ang2: 0, ang3: 0, a1: 120, a2: 80, a3: 20 },
  ],
};
const DEFAULTS = { extent: { min: [511_900, 7_099_900, 200], max: [512_100, 7_100_100, 300] }, defaults: { lag: 10 } } as unknown as DefaultsAnswer;

let layers: Map<string, SceneLayer>;
let frameFns: (() => void)[];
let slot: unknown;
let scene: Scene;
const canvas = { style: { cursor: "" }, getBoundingClientRect: () => ({ left: 0, top: 0 }) };
const at = (type: ToolPointer["type"], p: { x: number; y: number }, o: { shift?: boolean; ctrl?: boolean } = {}): ToolPointer => ({ type, x: p.x, y: p.y, clientX: p.x, clientY: p.y, button: 0, shift: !!o.shift, ctrl: !!o.ctrl });
const hover = (p: { x: number; y: number }): ToolPointer => ({ ...at("move", p), button: -1 });

function openModelWindow(model: VModel = MODEL): string {
  const win = openWindow("variography", "Вариография · AU", "normal", { args: { n: Math.random() } });
  setWork(win, newWork({ model, defaults: DEFAULTS }));
  return win;
}

const arc = (kind: "ang1" | "ang2" | "ang3", win: string) => {
  const s = workOf(win)!.model.structures[1];
  return handlesOf({ ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 }, { a1: s.a1, a2: s.a2, a3: s.a3 }, CENTRE).find((h) => h.kind === kind) as ArcHandle;
};

beforeEach(() => {
  layers = new Map();
  frameFns = [];
  canvas.style.cursor = "";
  windowStore.set({ windows: [] });
  variographyStore.set({ works: {}, models: [], modelsLoaded: false, dragging: null });
  scene = {
    clipPlanes: [],
    resolution: new THREE.Vector2(W, H),
    canvas,
    addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
    removeLayer: (k: string) => void layers.delete(k),
    invalidate: () => undefined,
    layerChanged: vi.fn(),
    onRestored: () => () => true,
    onFrame: (fn: () => void) => (frameFns.push(fn), () => true),
    setHandles: (h: unknown) => void (slot = h),
    cursorRay: (px: number, py: number) => projectRay(cam, opts, { w: W, h: H }, ORIGIN, 1, px, py),
    projectView: () => view,
  } as unknown as Scene;
});

describe("the store of the model and the ellipsoid", () => {
  it("the structure chosen, the centre, L / 2; «общие оси» turn every structure, the ranges stay its own", () => {
    const w = newWork({ model: MODEL, defaults: DEFAULTS });
    expect(chosenStructure(w)).toBe(1); // the largest a1
    expect(chosenStructure({ ...w, ellipsoid: { visible: true, structure: 0, centre: null } })).toBe(0);
    expect(chosenStructure({ ...w, ellipsoid: { visible: true, structure: 5, centre: null } })).toBe(1);
    expect(ellipsoidCentre(w)).toEqual(CENTRE);
    expect(ellipsoidCentre({ ...w, ellipsoid: { visible: true, structure: null, centre: [1, 2, 3] } })).toEqual([1, 2, 3]);
    expect(ellipsoidCentre(newWork())).toBeNull();
    expect(leastRange(w)).toBe(5);
    const m = withStructure(MODEL, 1, { ang1: 75, a1: 150 });
    expect(m.structures.map((s) => [s.ang1, s.a1])).toEqual([[75, 60], [75, 150]]);
    const own = withStructure({ ...MODEL, common_axes: false }, 1, { ang1: 75 });
    expect(own.structures.map((s) => s.ang1)).toEqual([30, 75]);
    expect(viewsOf({ ...MODEL, structures: [{ ...MODEL.structures[0], ang1: 99 }, MODEL.structures[1]], common_axes: true })[0].angles.ang1).toBe(99);
  });
});

describe("the ellipsoid of a window in the scene", () => {
  it("is drawn for an open window with its ellipsoid shown, follows the model in place and is gone with the window", () => {
    const win = openModelWindow();
    const s = new VarioScene(scene);
    expect(slot).toBe(s.handles); // the only handles: set in the slot as they are
    expect(layers.size).toBe(0);
    setEllipsoid(win, { visible: true });
    const layer = layers.get(ellipsoidLayerKey(win));
    expect(layer).toBeInstanceOf(EllipsoidLayer);
    expect(layers.get("vario:handles")).toBeInstanceOf(EllipsoidHandles);
    expect(s.stats()).toEqual([{ win, selected: 1, structures: 2, samples: 0, centre: CENTRE }]);
    // a cell of the table: new matrices of the same layer, «Вписать» follows
    setWork(win, (w) => ({ model: withStructure(w.model, 0, { a1: 70 }) }));
    expect(layers.get(ellipsoidLayerKey(win))).toBe(layer);
    expect((layer as EllipsoidLayer).pose(0).ranges.a1).toBe(70);
    expect(scene.layerChanged).toHaveBeenCalledTimes(1);
    // a third structure: built again
    setWork(win, (w) => ({ model: { ...w.model, structures: [...w.model.structures, { ...w.model.structures[0], a1: 200 }] } }));
    expect(layers.get(ellipsoidLayerKey(win))).not.toBe(layer);
    expect(s.stats()[0].selected).toBe(2);
    setEllipsoid(win, { structure: 0 });
    expect(s.stats()[0].selected).toBe(0);
    setEllipsoid(win, { visible: false });
    expect(layers.size).toBe(0);
    setEllipsoid(win, { visible: true });
    expect(layers.size).toBe(2);
    closeWindow(win);
    expect(layers.size).toBe(0);
    s.dispose();
    expect(slot).toBeNull();
  });

  it("a drag of the ring writes ang1 of every structure into the store, step 1° (`Shift` 5°); the release lets go", () => {
    const win = openModelWindow();
    const s = new VarioScene(scene);
    setEllipsoid(win, { visible: true });
    const ring = arc("ang1", win);
    const from = screen(pointAt(ring, 200));
    s.handles.pointer(hover(from));
    expect(s.handles.stats().hover).toBe("ang1");
    expect(canvas.style.cursor).toBe("grab");
    expect(s.handles.pointer(at("down", from))).toBe(true);
    expect(variographyStore.get().dragging).toEqual({ win, kind: "ang1" });
    s.handles.pointer(at("move", screen(pointAt(ring, 240.4))));
    expect(workOf(win)!.model.structures.map((x) => x.ang1)).toEqual([70, 70]);
    expect(workOf(win)!.dirty).toBe(true);
    expect((layers.get(ellipsoidLayerKey(win)) as EllipsoidLayer).pose(1).frame.ang1).toBeCloseTo(70, 12);
    expect(scene.layerChanged).not.toHaveBeenCalled(); // not every move
    s.handles.pointer(at("move", screen(pointAt(ring, 251.9)), { shift: true }));
    expect(workOf(win)!.model.structures[1].ang1).toBe(80); // 51.9 → 50
    expect(s.handles.stats().shown.find((h) => h.kind === "ang1")!.lit).toBe(true);
    s.handles.pointer(at("up", screen(pointAt(ring, 251.9))));
    expect(variographyStore.get().dragging).toBeNull();
    expect(scene.layerChanged).toHaveBeenCalledTimes(1);
    expect(workOf(win)!.model.structures[1]).toMatchObject({ ang2: 0, ang3: 0, a1: 120, a2: 80, a3: 20 });
    s.dispose();
  });

  it("a knob: the range by the move along its axis, not less than L / 2; with `Ctrl` all three in one ratio; a cancel brings the press back", () => {
    const win = openModelWindow();
    const s = new VarioScene(scene);
    setEllipsoid(win, { visible: true });
    const t = s.target()!;
    const knob = handlesOf(t.model.angles, t.model.ranges, CENTRE).find((h) => h.kind === "a1") as KnobHandle;
    const on = (r: number) => screen([CENTRE[0] + knob.axis[0] * r, CENTRE[1] + knob.axis[1] * r, CENTRE[2] + knob.axis[2] * r]);
    expect(s.handles.pointer(at("down", on(120)))).toBe(true);
    s.handles.pointer(at("move", on(150)));
    expect(workOf(win)!.model.structures[1]).toMatchObject({ a1: 150, a2: 80, a3: 20 });
    s.handles.pointer(at("move", on(-50)));
    expect(workOf(win)!.model.structures[1].a1).toBe(5); // L / 2
    s.handles.pointer(at("move", on(60), { ctrl: true }));
    expect(workOf(win)!.model.structures[1]).toMatchObject({ a1: 60, a2: 40, a3: 10 });
    expect(workOf(win)!.model.structures[0]).toMatchObject({ a1: 60, a2: 40, a3: 10 }); // its own ranges: untouched
    s.handles.cancel();
    expect(workOf(win)!.model.structures[1]).toMatchObject({ a1: 120, a2: 80, a3: 20 });
    expect(variographyStore.get().dragging).toBeNull();
    s.dispose();
  });

  it("the window on top has the handles; a press away from them is the scene's", () => {
    const a = openModelWindow();
    const b = openModelWindow({ ...MODEL, structures: [{ ...MODEL.structures[0], ang1: 120 }] });
    const s = new VarioScene(scene);
    setEllipsoid(a, { visible: true });
    setEllipsoid(b, { visible: true });
    expect(s.target()!.id).toBe(b);
    expect(s.focused()).toBe(true);
    expect(s.handles.pointer(at("down", { x: 3, y: 3 }))).toBe(false);
    s.dispose();
  });

  it("the frame of an edit is measured: `vario:edit` → the frame → `vario:drawn`, ≤ 50 ms", async () => {
    const win = openModelWindow();
    const s = new VarioScene(scene);
    setEllipsoid(win, { visible: true });
    const ring = arc("ang1", win);
    s.handles.pointer(at("down", screen(pointAt(ring, 200))));
    const before = frameTimes().length;
    // 60 moves of 1°: the store, the table model, the matrices of the layer, the handles — as in the app, then the frame
    const t0 = performance.now();
    for (let k = 1; k <= 60; k++) {
      s.handles.pointer(at("move", screen(pointAt(ring, 200 + k + 0.2))));
      for (const fn of frameFns) fn();
    }
    const perMove = (performance.now() - t0) / 60;
    s.handles.pointer(at("up", screen(pointAt(ring, 260.2))));
    await new Promise((r) => setTimeout(r, 5));
    const got = frameTimes().slice(before);
    expect(got.length).toBe(60);
    expect(Math.max(...got)).toBeLessThan(50);
    expect(perMove).toBeLessThan(50);
    expect(performance.getEntriesByName("vario:frame").length).toBeGreaterThan(0);
    console.info(`vario drag: ${perMove.toFixed(2)} ms per move, frame ≤ ${Math.max(...got).toFixed(2)} ms`);
    s.dispose();
  });
});

describe("variography.ellipsoid, variography.show", () => {
  const STORED = {
    id: 7,
    name: "AU",
    color: null,
    meta: { v: 1, source: { set_id: 3, table: "12", field: "AU", domain: null, weights: null }, transform: "none", ...MODEL },
    points: null,
    stale: false,
    stale_reason: null,
    source_lost: false,
  };
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        const body = path === "/geostat/variography/models/7" ? STORED : path === "/jobs" ? { jobs: [] } : { code: "not_found", message: "Нет", hint: "" };
        return new Response(JSON.stringify(body), { status: path.endsWith("/models/7") || path === "/jobs" ? 200 : 404, headers: { "Content-Type": "application/json" } });
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("have no stage any more; the angles and ranges are checked before anything", () => {
    for (const id of ["variography.ellipsoid", "variography.show"]) expect(commands.find((c) => c.id === id)!.stage).toBeUndefined();
    expect(ellipsoidPatch({ ang1: 360, ang2: -12.5, a2: 40 })).toEqual({ ang1: 0, ang2: -12.5, a2: 40 });
    expect(() => ellipsoidPatch({ ang2: 95 })).toThrow(/вне диапазона/);
    expect(() => ellipsoidPatch({ a1: 0 })).toThrow(/больше 0/);
  });

  it("open the window of the model, give the structure its angles and show the ellipsoid; `visible: false` hides it", async () => {
    const app = createApp({ persist: false });
    bindVariography(app);
    app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
    const r = await app.registry.execute("variography.ellipsoid", { model_id: 7, structure: 1, ang1: 45, a1: 90 }, "claude");
    expect(r.ok).toBe(true);
    const win = windowOfModel(7)!;
    expect(workOf(win)!.ellipsoid).toEqual({ visible: true, structure: 0, centre: null });
    expect(workOf(win)!.model.structures.map((s) => [s.ang1, s.a1])).toEqual([[45, 90], [45, 120]]);
    expect(String(r.result)).toMatch(/структура 1 из 2/);
    expect((await app.registry.execute("variography.ellipsoid", { model_id: 7, structure: 3 }, "claude")).ok).toBe(false);
    expect((await app.registry.execute("variography.show", { model_id: 7, visible: false }, "claude")).ok).toBe(true);
    expect(workOf(win)!.ellipsoid!.visible).toBe(false);
    expect((await app.registry.execute("variography.show", { model_id: 7 }, "claude")).ok).toBe(true);
    expect(workOf(win)!.ellipsoid!.visible).toBe(true);
  });
});
