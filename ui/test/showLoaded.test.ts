// «Показать загруженное» (ТЗ 7.13, 7.8; Э8 8.26): after an import the view moves to the objects it stored only
// when they are not seen — off the screen or smaller than 1 % of it — keeping its direction.
import { afterEach, describe, expect, it, vi } from "vitest";
import { boxFromBbox6, scaleZ, type Box3 } from "../src/viz/core/bounds";
import { revealCamera, screenShare } from "../src/viz/core/reveal";
import { distance, normalize, sub } from "../src/viz/core/vec3";
import { project, view, viewDirection, type ViewOptions } from "../src/viz/core/views";
import { extentsOf, importedObjects, ShowLoaded, type ShowLoadedDeps } from "../src/modules/scene/showLoaded";
import type { LineFileInfo, RasterInfo, WireframeInfo } from "../src/modules/scene/objects";

const ortho: ViewOptions = { aspect: 16 / 9, perspective: false, fov: 45, vexag: 1 };
const persp: ViewOptions = { ...ortho, perspective: true };
// the scene origin at the demo; the demo holes and an imported TRIDB far away (scene coordinates)
const ORIGIN = { x: 5175, y: 8125, z: 420 };
const demo: Box3 = boxFromBbox6([4990, 7990, 280, 5360, 8260, 426], ORIGIN);
const near: Box3 = boxFromBbox6([5100, 8100, 200, 5200, 8200, 280], ORIGIN);
const far: Box3 = boxFromBbox6([466_000, 4_584_000, 1200, 467_200, 4_585_100, 1500], ORIGIN);

describe("what is seen (viz/core/reveal.ts)", () => {
  for (const opts of [ortho, persp]) {
    const cam = view("iso", demo, opts);
    it(`${opts.perspective ? "perspective" : "orthographic"}: the fitted content is seen, an object 461 km away is not`, () => {
      expect(screenShare(cam, opts, demo)).toBeGreaterThan(0.4);
      expect(screenShare(cam, opts, near)).toBeGreaterThan(0.05);
      expect(screenShare(cam, opts, far)).toBe(0);
      expect(revealCamera(cam, opts, [near])).toBeNull(); // seen: the view stays
    });
    it(`${opts.perspective ? "perspective" : "orthographic"}: the view moves to the objects not seen, looking the same way`, () => {
      const moved = revealCamera(cam, opts, [near, far])!;
      expect(moved).not.toBeNull();
      expect(distance(viewDirection(moved), viewDirection(cam))).toBeLessThan(1e-9);
      expect(screenShare(moved, opts, far)).toBeGreaterThan(0.5); // fills the view…
      const centre = project(moved, opts, [(far.min[0] + far.max[0]) / 2, (far.min[1] + far.max[1]) / 2, (far.min[2] + far.max[2]) / 2])!;
      expect(Math.hypot(centre.x, centre.y)).toBeLessThan(0.05); // … in the middle of the screen
      expect(screenShare(moved, opts, near)).toBe(0); // only what was not seen: the near object was
    });
  }
  it("a tiny object on the screen is not seen; one taller than the view is (the height of a 16:9 screen)", () => {
    const cam = view("iso", demo, ortho);
    const dot: Box3 = boxFromBbox6([5175, 8125, 400, 5175.5, 8125.5, 400.5], ORIGIN);
    expect(screenShare(cam, ortho, dot)).toBeLessThan(0.01);
    expect(revealCamera(cam, ortho, [dot])).not.toBeNull();
    const tall: Box3 = boxFromBbox6([5170, 8120, -5000, 5180, 8130, 5000], ORIGIN);
    expect(screenShare(cam, ortho, tall)).toBeCloseTo(9 / 16, 9);
  });
  it("with a vertical exaggeration the box is measured and fitted as drawn: stretched", () => {
    const opts = { ...ortho, vexag: 5 };
    const cam = view("front", demo, opts);
    const moved = revealCamera(cam, opts, [far])!;
    expect(screenShare(moved, opts, scaleZ(far, 5))).toBeGreaterThan(0.5);
    expect(revealCamera(moved, opts, [far])).toBeNull(); // shown: a second import of it moves nothing
  });
  it("a perspective camera inside the box sees it; a box behind it does not", () => {
    const cam = view("iso", demo, persp);
    const around: Box3 = { min: [-1e5, -1e5, -1e5], max: [1e5, 1e5, 1e5] };
    expect(screenShare(cam, persp, around)).toBe(1);
    const back = normalize(sub(cam.position, cam.target));
    const p = cam.position.map((v, k) => v + back[k] * 500);
    const behind: Box3 = { min: [p[0] - 5, p[1] - 5, p[2] - 5], max: [p[0] + 5, p[1] + 5, p[2] + 5] };
    expect(screenShare(cam, persp, behind)).toBe(0);
  });
});

const wf = (id: number, extent: (number | null)[] | null): WireframeInfo => ({ id, name: `W${id}`, version: 1, color: null, point_count: 3, triangle_count: 1, extent, dtm: null });
const file = (id: number, extents: ((number | null)[] | null)[]): LineFileInfo =>
  ({ id, name: `F${id}`, folder: null, version: 1, format: null, label: null, filename: null, crs_name: null, warnings: [], layers: extents.map((extent, k) => ({ id: id * 10 + k, file_id: id, extent })) }) as unknown as LineFileInfo;
const raster = (id: number, corners: (number | null)[][], elevation: number | null = null): RasterInfo => ({ id, georef: { corners3d: corners }, elevation }) as unknown as RasterInfo;

describe("the objects of an import (modules/scene/showLoaded.ts)", () => {
  afterEach(() => vi.useRealTimers());

  it("the objects of a finished import of files; none for a failed one or another job", () => {
    expect(importedObjects({ kind: "import.objects", status: "done", result: { objects: [3, 4, 3, "x", -1] } })).toEqual([3, 4]);
    expect(importedObjects({ kind: "import.objects", status: "failed", result: { objects: [3] } })).toEqual([]);
    expect(importedObjects({ kind: "import.drillholes", status: "done", result: { objects: [3] } })).toEqual([]);
    expect(importedObjects({ kind: "import.objects", status: "done", result: null })).toEqual([]);
  });

  it("extents of wireframes, of every layer of a line file and of a raster; unknown heights at the floor", () => {
    const lists = {
      wireframes: [wf(1, [10, 20, 30, 11, 21, 31]), wf(2, null)],
      files: [file(5, [[0, 0, null, 1, 1, null], [2, 2, 5, 3, 3, 6]])],
      rasters: [raster(7, [[0, 10, null], [4, 10, null], [4, 0, null], [0, 0, null]], 250), raster(8, [[0, 0, 1], [1, 0, 2], [1, 1, 3], [0, 1, 4]])],
    };
    const { boxes, missing } = extentsOf([1, 2, 5, 7, 8, 99], lists, -100);
    expect(missing).toBe(1); // 99 is not listed (yet)
    expect(boxes).toEqual([
      [10, 20, 30, 11, 21, 31],
      [0, 0, -100, 1, 1, -100],
      [2, 2, 5, 3, 3, 6],
      [0, 0, 250, 4, 10, 250],
      [0, 0, 1, 1, 1, 4],
    ]);
  });

  it("holds the view at once, reveals when the objects are listed, once", () => {
    let lists = { wireframes: [] as WireframeInfo[], files: [] as LineFileInfo[], rasters: [] as RasterInfo[] };
    const deps: ShowLoadedDeps = { hold: vi.fn(), reveal: vi.fn(() => true), lists: () => lists, floor: () => 0 };
    const s = new ShowLoaded(deps);
    s.imported({ kind: "import.objects", status: "done", result: { objects: [1] } });
    expect(deps.hold).toHaveBeenCalledTimes(1);
    expect(deps.reveal).not.toHaveBeenCalled(); // the lists are read again first
    lists = { ...lists, wireframes: [wf(1, [466_000, 4_584_000, 1200, 467_200, 4_585_100, 1500])] };
    s.sync();
    expect(deps.reveal).toHaveBeenCalledWith([[466_000, 4_584_000, 1200, 467_200, 4_585_100, 1500]]);
    s.sync();
    expect(deps.reveal).toHaveBeenCalledTimes(1);
    s.dispose();
  });

  it("an import seen queued or running holds the view once — before its objects arrive", () => {
    const deps: ShowLoadedDeps = { hold: vi.fn(), reveal: vi.fn(() => true), lists: () => ({ wireframes: [], files: [], rasters: [] }), floor: () => 0 };
    const s = new ShowLoaded(deps);
    s.jobs([{ source: "core", id: 1, kind: "import.drillholes", status: "running" }, { source: "core", id: 2, kind: "import.objects", status: "done" }]);
    expect(deps.hold).not.toHaveBeenCalled();
    s.jobs([{ source: "core", id: 3, kind: "import.objects", status: "queued" }]);
    s.jobs([{ source: "core", id: 3, kind: "import.objects", status: "running" }]);
    expect(deps.hold).toHaveBeenCalledTimes(1);
    s.dispose();
  });

  it("an object never listed: what is listed is shown after the wait; nothing imported — nothing held", () => {
    vi.useFakeTimers();
    const lists = { wireframes: [wf(1, [0, 0, 0, 1, 1, 1])], files: [] as LineFileInfo[], rasters: [] as RasterInfo[] };
    const deps: ShowLoadedDeps = { hold: vi.fn(), reveal: vi.fn(() => true), lists: () => lists, floor: () => 0 };
    const s = new ShowLoaded(deps);
    s.imported({ kind: "import.objects", status: "done", result: { objects: [] } });
    expect(deps.hold).not.toHaveBeenCalled();
    s.imported({ kind: "import.objects", status: "done", result: { objects: [1, 2] } });
    expect(deps.reveal).not.toHaveBeenCalled();
    vi.advanceTimersByTime(5000);
    expect(deps.reveal).toHaveBeenCalledWith([[0, 0, 0, 1, 1, 1]]);
    s.dispose();
  });
});
