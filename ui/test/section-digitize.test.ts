// Э10 10.22 (ADR 0016 §7): «Контур на разрезе» — the session draws on t = 0 of section k of the active series, new
// lines carry SECTION_SET / SECTION / SECTION_NAME into `new_attrs` of the save, after `]` the plane is the next
// section; the snap of other line layers keeps only the targets in the window of the section.
import { afterEach, describe, expect, it, vi } from "vitest";
import { asLoaded, fromStored } from "../src/edit/io";
import type { Vec3 } from "../src/edit/model";
import { EditSession } from "../src/edit/session";
import type { SnapSource } from "../src/edit/snap";
import type { EditPointer } from "../src/edit/tools";
import { applyEditSection, editSection, sectionAttrs } from "../src/modules/section/digitize";
import type { SeriesRow } from "../src/modules/section/seriesApi";
import { currentSection, pickSection, type SeriesState } from "../src/modules/section/seriesState";
import { pickRequest } from "../src/viz/mesh/exact";
import { planeQuery, planeSectionQuery, setSnapQuery, SnapFeeder, windowClip, windowSource } from "../src/modules/lines/snapSources";
import type { LineLayerInfo } from "../src/modules/scene/objects";
import { setCoreTarget } from "../src/shell/api";
import { decodeBfa1 } from "../src/viz/format/bfa1";
import { halfSpaces, planeLocal } from "../src/viz/section/plane";
import { layer, linegeom } from "./linesHarness";

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const X0 = 512345.678901234;
const Y0 = 6789012.345678901;

function seriesRow(): SeriesRow {
  return {
    id: 4, name: "Профили", folder: null, version: 3, number: 2, count: 5, created_at: "", updated_at: "",
    params: { type: "vertical", p1: [X0, Y0, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null },
  };
}
const STATE: SeriesState = {
  list: [seriesRow()], limit: 1000, loaded: true, error: null,
  tables: { 4: { version: 3, count: 5, sections: [0, 1, 2, 3, 4].map((k) => ({ k, name: `${6789012 + 25 * k}N`, s: 25 * k, origin: [X0, Y0 + 25 * k, 0], towards: 12.5, away: 12.5 })) } } as unknown as SeriesState["tables"],
};
const keys = (k: number, clip = true) => ({ section: { clip, series: { id: 4, k, seg: null } } });

/** A session looking along v̂ = (0, 1, 0) (az 90°): the screen x — east, up — Z; snap off (points from the ray). */
function session(): EditSession {
  const s = new EditSession(asLoaded(1, 1, fromStored(new Float64Array(0), [0], [], [])));
  // column-major project → clip: x → screen x (2 px/m from X0), Z → screen up (0…200 m), Y → depth
  const matrix = [0.01, 0, 0, 0, 0, 0, 1e-4, 0, 0, 0.01, 0, 0, -X0 / 100 - 1, -1, -Y0 / 1e4, 1];
  s.view = { transform: () => ({ matrix, width: 400, height: 400 }), ray: (cx, cy) => ({ origin: [X0 + cx / 2, Y0 - 5000, 200 - cy / 2], dir: [3e-10, 1, -2e-10] }) };
  s.snap = { ...s.snap, enabled: false };
  s.viewChanged();
  return s;
}
const click = (x: number, y: number): EditPointer => ({ type: "click", x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false });
const tick = () => new Promise((r) => setTimeout(r, 0));

async function polygon(s: EditSession, pts: number[][]): Promise<void> {
  s.setTool("polygon");
  for (const [x, y] of pts) {
    s.pointer(click(x, y));
    await tick();
  }
  s.finishDrawing();
}

describe("editSection", () => {
  it("a section of the active series with clipping: its plane, the test of k and the attributes of new lines", () => {
    const sec = editSection(keys(2), STATE);
    const cur = currentSection(keys(2), STATE)!;
    expect(sec.line).toBeNull();
    expect(sec.plane!.origin).toEqual([X0, Y0 + 50, 0]);
    expect(sec.attrs).toEqual({ SECTION_SET: 2, SECTION: 3, SECTION_NAME: "6789062N" });
    expect(sectionAttrs(cur)).toEqual(sec.attrs);
    expect(sec.test!([[X0, Y0 + 50.2, 1], [X0 + 5, Y0 + 49, 3]])).toBe(true);
    expect(sec.test!([[X0, Y0 + 50.2, 1], [X0 + 5, Y0 + 63, 3]])).toBe(false);
    // the same section gives the same test (the lock keeps its verdicts)
    expect(editSection(keys(2), STATE).test).toBe(sec.test);
    // clipping off: the plane is shown, points go to the plan level, nothing is locked
    const off = editSection(keys(2, false), STATE);
    expect([off.clip, off.test, off.attrs]).toEqual([false, null, undefined]);
    // no series: the free line of Э9
    const line = { x0: X0, y0: Y0, az: 45, length: null, towards: 10, away: 20 };
    expect(editSection({ section: { line, clip: true } }, STATE)).toMatchObject({ line, plane: null, clip: true, test: null });
  });

  it("new lines carry SECTION_SET / SECTION / SECTION_NAME into new_attrs of the save; after `]` they fall on the next section", async () => {
    const s = session();
    applyEditSection(s, editSection(keys(2), STATE));
    await polygon(s, [[20, 300], [180, 310], [170, 120]]);
    applyEditSection(s, editSection(keys(3), STATE)); // `]`
    await polygon(s, [[40, 280], [120, 260], [90, 150], [30.3, 170.7]]);
    const [a, b] = s.layer.lines;
    const g = currentSection(keys(0), STATE)!.geom;
    for (const p of a.points) expect(Math.abs(g.local(p as Vec3, 2)[2])).toBeLessThanOrEqual(1e-9);
    for (const p of b.points) expect(Math.abs(g.local(p as Vec3, 3)[2])).toBeLessThanOrEqual(1e-9);
    expect(g.lineSection(b.points)).toBe(3);
    const header = decodeBfa1<{ new_attrs: Record<string, Record<string, unknown>> }>(s.saveBody()).header;
    expect(header.new_attrs).toEqual({ 0: { SECTION_SET: 2, SECTION: 3, SECTION_NAME: "6789062N" }, 1: { SECTION_SET: 2, SECTION: 4, SECTION_NAME: "6789087N" } });
    // the contour of section 3 is not picked on section 4 («текущий разрез»), «все» picks it
    s.setTool("select");
    s.pointer(click(20, 300));
    expect(s.selection.lines).toEqual([]);
    s.lock.setScope("all");
    s.pointer(click(20, 300));
    expect(s.selection.lines).toEqual([a.id]);
  });
});

describe("10.28: the section changes under a line being drawn", () => {
  it("`]` in the middle of a polygon ends it on its own section (as Enter); the next click starts one on the new", async () => {
    const s = session();
    applyEditSection(s, editSection(keys(2), STATE));
    s.setTool("polygon");
    for (const [x, y] of [[20, 300], [180, 310], [170, 120]]) {
      s.pointer(click(x, y));
      await tick();
    }
    expect(s.drawing).not.toBeNull();
    // the name of the section read later: the same place, the drawing goes on
    const unnamed = { ...STATE, tables: {} };
    applyEditSection(s, editSection(keys(2), unnamed));
    applyEditSection(s, editSection(keys(2), STATE));
    expect(s.drawing).not.toBeNull();
    applyEditSection(s, editSection(keys(3), STATE)); // `]`
    expect(s.drawing).toBeNull();
    expect(s.layer.lines).toHaveLength(1);
    expect(s.layer.lines[0].closed).toBe(true);
    expect(s.message).toMatch(/Линия завершена на прежнем разрезе/);
    s.pointer(click(40, 280));
    await tick();
    expect(s.layer.lines).toHaveLength(2); // a new line, not the old one continued on section 4
    const g = currentSection(keys(0), STATE)!.geom;
    for (const p of s.layer.lines[0].points) expect(Math.abs(g.local(p as Vec3, 2)[2])).toBeLessThanOrEqual(1e-9);
    expect(Math.abs(g.local(s.layer.lines[1].points[0] as Vec3, 3)[2])).toBeLessThanOrEqual(1e-9);
    s.finishDrawing();
    const header = decodeBfa1<{ new_attrs: Record<string, Record<string, unknown>> }>(s.saveBody()).header;
    expect(header.new_attrs[0]).toMatchObject({ SECTION: 3 });
    expect(Object.keys(header.new_attrs)).toHaveLength(1); // the one-point line of section 4 went away on finish
  });
});

describe("10.28: the exact pick under a series is clipped by the window of its section", () => {
  it("pickSection gives the plane of the series; pickRequest — its half-spaces (was `clip: null`)", () => {
    const cur = currentSection(keys(2), STATE)!;
    const sec = pickSection(keys(2), STATE);
    expect(sec).toEqual({ line: null, clip: true, plane: cur.plane });
    const ray = { origin: [X0, Y0, 500] as Vec3, dir: [0, 0, -1] as Vec3 };
    expect(pickRequest(ray, [3], sec.line, sec.clip, sec.plane).clip).toEqual(halfSpaces(cur.plane));
    expect(pickRequest(ray, [3], null, false, cur.plane).clip).toBeNull();
    // the free line: its window, as before
    const line = { x0: X0, y0: Y0, az: 45, length: null, towards: 10, away: 20 };
    const free = pickSection({ section: { line, clip: true } }, STATE);
    expect(free).toEqual({ line, clip: true, plane: null });
    expect(pickRequest(ray, [3], free.line, free.clip, free.plane).clip).toHaveLength(2);
  });
});

describe("snap by the window of a section of a series", () => {
  const plane = currentSection(keys(1), STATE)!.plane;
  const sec = { line: null, clip: true, plane };

  it("the queries of the core: `plane=` for sets, `point=O_k&normal=v̂` for wireframes, the half-spaces for pick", () => {
    expect(setSnapQuery(sec, [0, 0, 1, 1])).toBe(planeQuery(plane));
    expect(planeQuery(plane)).toBe(`plane=${X0},${Y0 + 25},0,0,1,0,12.5,12.5`);
    expect(planeSectionQuery(plane)).toBe(`point=${X0},${Y0 + 25},0&normal=0,1,0`);
    expect(windowClip(sec)).toEqual(halfSpaces(plane));
    // clipping off: the extent of the view as before
    expect(setSnapQuery({ ...sec, clip: false }, [1, 2, 3, 4])).toBe("bbox=1,2,3,4");
  });

  it("the targets of another layer outside the window are dropped: points out, a segment crossing the window kept", () => {
    const y = (t: number) => Y0 + 25 + t;
    const src: SnapSource = {
      key: "lines:9",
      points: { kind: "vertex", xyz: Float64Array.from([X0, y(3), 5, X0, y(20), 5, X0 + 1, y(-12.5), 7]) },
      segments: { kind: "nearest", xyz: Float64Array.from([X0, y(-40), 0, X0, y(40), 0, X0, y(20), 0, X0, y(30), 0, X0, y(1), 0, X0, y(2), 0]), midpoints: true },
    };
    const out = windowSource(src, halfSpaces(plane));
    expect(Array.from(out.points!.xyz)).toEqual([X0, y(3), 5, X0 + 1, y(-12.5), 7]);
    expect(out.segments!.xyz.length).toBe(12); // the crossing one and the inner one
    expect(windowSource(src, halfSpaces(plane))).toBe(out);
  });

  it("SnapFeeder: the line layer goes into the index with its targets in the window only", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(linegeom(9, 1, [{ pts: [[X0, Y0 + 26, 1], [X0 + 1, Y0 + 27, 1]] }, { pts: [[X0, Y0 + 90, 1], [X0 + 1, Y0 + 91, 1]] }]))));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const s = new EditSession(asLoaded(7, 3, fromStored(new Float64Array(0), [0], [], [])), "Контуры");
    const set: SnapSource[] = [];
    const real = s.snapIndex.setSource.bind(s.snapIndex);
    s.snapIndex.setSource = (src) => (set.push(src), real(src));
    const shown = [layer(9, "Дороги", { version: 1, point_count: 4 })] as unknown as LineLayerInfo[];
    const feeder = new SnapFeeder(s, { lines: () => shown, sets: () => [], wireframes: () => [], section: () => sec, viewBox: () => null });
    await feeder.run();
    const got = set.find((x) => x.key === "lines:9")!;
    const ts: number[] = [];
    for (let i = 0; i < got.points!.xyz.length; i += 3) ts.push(planeLocal(plane, [got.points!.xyz[i], got.points!.xyz[i + 1], got.points!.xyz[i + 2]])[2]);
    expect(ts).toEqual([1, 2]);
    expect(got.segments!.xyz.length).toBe(6);
    feeder.dispose();
  });
});

describe("contourLayer: the layer of contours of the series", () => {
  it("none yet: a new «Контуры <серия>» in a new file «Серии разрезов», kept as the layer of the series; then it is the one", async () => {
    const { projectStore } = await import("../src/modules/projects/state");
    const { sceneObjects } = await import("../src/modules/scene/objects");
    const { seriesStore } = await import("../src/modules/section/seriesState");
    const { contourLayer } = await import("../src/modules/section/digitize");
    const files: { id: number; name: string; folder: null; layers: unknown[] }[] = [];
    const calls: string[] = [];
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      calls.push(`${method} ${path} ${JSON.stringify(body)}`);
      if (path === "/lines/files") return json({ files });
      if (path === "/rasters") return json({ rasters: [] });
      if (path === "/wireframes") return json({ wireframes: [] });
      if (path === "/lines/layers" && method === "POST") {
        const made = layer(31, body.name, { version: 1, point_count: 0 });
        files.push({ id: 30, name: body.name, folder: null, layers: [{ ...made, file_id: 30 }] });
        return json({ ...made, file_id: 30 });
      }
      if (path === "/lines/layers/31" && method === "PATCH") {
        (files[0].layers[0] as { name: string }).name = body.name;
        return json({});
      }
      if (path === "/section-series/4" && method === "PATCH") return json({ ...seriesRow(), version: 4, params: { ...seriesRow().params, contour_layer: body.params.contour_layer } });
      return json({});
    }));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as never });
    seriesStore.set(STATE);
    const cur = currentSection(keys(1), STATE)!;
    expect(await contourLayer(cur)).toBe(31);
    expect(calls.filter((c) => !c.startsWith("GET"))).toEqual([
      'POST /lines/layers {"name":"Серии разрезов"}',
      'PATCH /lines/layers/31 {"expected_version":1,"name":"Контуры Профили"}',
      'PATCH /section-series/4 {"expected_version":3,"params":{"contour_layer":31}}',
    ]);
    expect(sceneObjects.get().files[0].layers[0].name).toBe("Контуры Профили");
    const kept = currentSection(keys(1), seriesStore.get())!;
    expect(kept.row.params.contour_layer).toBe(31);
    calls.length = 0;
    expect(await contourLayer(kept)).toBe(31);
    expect(calls.filter((c) => !c.startsWith("GET"))).toEqual([]);
    projectStore.set({ current: null } as never);
  });
});
