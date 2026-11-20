// Э10 10.21 (ADR 0016 §7): the drawing plane of a section of a series — t = 0 from O_k, the ray on the CPU in float64
// and the point put back on the plane; Т8.1-3: on UTM (X ≈ 5·10⁵, Y ≈ 7·10⁶) ≤ 1e−9 m from it.
import { describe, expect, it } from "vitest";
import { sectionPlane } from "../../src/edit/coords";
import { drawingPlane, ON_PLANE_M, placePoint, snapPoint, type DrawContext } from "../../src/edit/drawPlane";
import { asLoaded, fromStored } from "../../src/edit/io";
import type { Vec3 } from "../../src/edit/model";
import { EditSession } from "../../src/edit/session";
import type { SnapHit } from "../../src/edit/snap";
import { DEFAULT_SNAP, type EditPointer, type SnapSettings } from "../../src/edit/tools";
import type { SectionLine } from "../../src/viz/section/math";
import { planeLocal, type SectionPlane } from "../../src/viz/section/plane";
import { seriesGeometry, type SeriesGeometry, type SeriesParams } from "../../src/viz/section/series";
import { lcg, ortho } from "./testUtil";

const X0 = 512345.678901234;
const Y0 = 6789012.345678901;
const NO_GRID: SnapSettings = { ...DEFAULT_SNAP, kinds: DEFAULT_SNAP.kinds.filter((k) => k !== "grid") };

const SERIES: Record<string, SeriesParams> = {
  vertical: { type: "vertical", p1: [X0, Y0, 0], az: 37.3, s0: 0, s1: 500, step: 25, towards: 12.5, away: 12.5 },
  inclined: { type: "inclined", p1: [X0, Y0, 350.25], az: 121.7, phi: 60, s0: -100, s1: 400, step: 20, towards: 10, away: 10 },
  plan: { type: "plan", p1: [X0, Y0, 420.125], az: 15, s0: 0, s1: 300, step: 7.5, towards: 3.75, away: 3.75 },
};

const ctxOf = (plane: SectionPlane): DrawContext => ({ section: null, plane, clip: true, zLevel: 0 });

/** A ray towards the plane from the viewer's side (−v̂), a little off v̂, through a point of it near O_k. */
function rayTo(p: SectionPlane, rnd: () => number): { origin: Vec3; dir: Vec3 } {
  const u = (rnd() - 0.5) * 2000;
  const w = (rnd() - 0.5) * 600;
  const back = 50 + rnd() * 3000;
  const tilt: Vec3 = [(rnd() - 0.5) * 0.6, (rnd() - 0.5) * 0.6, (rnd() - 0.5) * 0.6];
  const dir: Vec3 = [p.v[0] + tilt[0], p.v[1] + tilt[1], p.v[2] + tilt[2]];
  const at: Vec3 = [p.origin[0] + u * p.s[0] + w * p.w[0], p.origin[1] + u * p.s[1] + w * p.w[1], p.origin[2] + u * p.s[2] + w * p.w[2]];
  return { origin: [at[0] - back * dir[0], at[1] - back * dir[1], at[2] - back * dir[2]], dir };
}

describe("drawPlane: Т8.1-3", () => {
  for (const [name, params] of Object.entries(SERIES)) {
    it(`${name}: 2 000 points from the cursor ray lie on t = 0 of their section within 1e−9 m (UTM)`, () => {
      const g = seriesGeometry(params) as SeriesGeometry;
      const rnd = lcg(7);
      let worst = 0;
      for (const k of [0, 1, Math.floor(g.count / 2), g.count - 1]) {
        const plane = g.plane(k);
        for (let i = 0; i < 500; i++) {
          const p = placePoint(ctxOf(plane), NO_GRID, null, rayTo(plane, rnd)).point as Vec3;
          expect(p).not.toBeNull();
          worst = Math.max(worst, Math.abs(g.local(p, k)[2]), Math.abs(planeLocal(plane, p)[2]));
          expect(g.sectionOf(p)).toBe(k);
        }
      }
      expect(worst).toBeLessThanOrEqual(1e-9);
    });
  }

  it("a grid node of a section of a series is put back on its plane", () => {
    const g = seriesGeometry(SERIES.inclined) as SeriesGeometry;
    const plane = g.plane(3);
    const rnd = lcg(11);
    for (let i = 0; i < 200; i++) {
      const r = placePoint(ctxOf(plane), { ...DEFAULT_SNAP, kinds: ["grid"], grid: 5 }, null, rayTo(plane, rnd));
      expect(r.snap?.kind).toBe("grid");
      expect(Math.abs(planeLocal(plane, r.point as Vec3)[2])).toBeLessThanOrEqual(1e-9);
    }
  });

  it("the plane of a series is t = 0 through O_k with the normal −v̂; a free line keeps the middle of its window (Э9)", () => {
    const g = seriesGeometry(SERIES.vertical) as SeriesGeometry;
    const plane = g.plane(4);
    expect(drawingPlane(ctxOf(plane))).toEqual({ point: plane.origin, normal: [-plane.v[0], -plane.v[1], 0] });
    const line: SectionLine = { x0: X0, y0: Y0, az: 37.3, length: null, towards: 30, away: 10 };
    expect(drawingPlane({ section: line, plane: null, clip: true, zLevel: 5 })).toEqual(sectionPlane(line));
    // clipping off: the plan level, whatever the section
    expect(drawingPlane({ section: line, plane, clip: false, zLevel: 5 })).toEqual({ point: [0, 0, 5], normal: [0, 0, 1] });
  });
});

describe("drawPlane: snap targets on a section of a series", () => {
  const g = seriesGeometry(SERIES.vertical) as SeriesGeometry;
  const plane = g.plane(2);
  const at = (u: number, w: number, t: number): Vec3 => {
    const o = plane.origin;
    return [o[0] + u * plane.s[0] + t * plane.v[0], o[1] + u * plane.s[1] + t * plane.v[1], w];
  };
  const hit = (kind: SnapHit["kind"], point: Vec3): SnapHit => ({ kind, point, label: "", distancePx: 0 });

  it("a target off the plane is projected along v̂: u and w kept, t = 0; «оставить 3D» keeps it as it is", () => {
    for (const kind of ["collar", "vertex", "nearest", "wf-section"] as const) {
      const q = at(123.456, 87.5, 7.25);
      const p = snapPoint(hit(kind, q), ctxOf(plane), true);
      const [u, w, t] = planeLocal(plane, p);
      const [u0, w0] = planeLocal(plane, q);
      expect(Math.abs(t), kind).toBeLessThanOrEqual(1e-9);
      expect(Math.abs(u - u0), kind).toBeLessThanOrEqual(1e-9);
      expect(w, kind).toBe(w0);
      expect(snapPoint(hit(kind, q), ctxOf(plane), false), kind).toBe(q);
    }
  });

  it("an own vertex already on the plane keeps its bits (a click on the first point still closes the line)", () => {
    const q = placePoint(ctxOf(plane), NO_GRID, null, rayTo(plane, lcg(3))).point as Vec3;
    expect(Math.abs(planeLocal(plane, q)[2])).toBeLessThan(ON_PLANE_M);
    expect(snapPoint(hit("vertex", q), ctxOf(plane), true)).toBe(q);
  });
});

describe("EditSession on a section of a series", () => {
  // looking along v̂ of a vertical section (az 90°: v̂ = (0, 1, 0), the screen x — east, up — Z)
  const g = seriesGeometry({ type: "vertical", p1: [X0, Y0, 0], az: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5 }) as SeriesGeometry;
  function session(k: number): EditSession {
    const s = new EditSession(asLoaded(1, 1, fromStored(new Float64Array(0), [0], [], [])));
    const plane = g.plane(k);
    // the points come from the ray (no snap); the view matrix only has to exist
    const matrix = ortho(X0, X0 + 200, Y0 - 100, Y0 + 100, -10000, 10000);
    s.view = { transform: () => ({ matrix, width: 400, height: 400 }), ray: (cx, cy) => ({ origin: [X0 + cx / 2 + 1e-7 * cy, plane.origin[1] - 5000, 200 - cy / 2], dir: [1e-9, 1, -3e-10] }) };
    s.plane = plane;
    s.clip = true;
    s.snap = { ...s.snap, enabled: false };
    s.viewChanged();
    s.setTool("polygon");
    return s;
  }
  const click = (x: number, y: number): EditPointer => ({ type: "click", x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false });

  it("Т8.1-3 through the session: the vertices of a drawn contour are on t = 0 of section k within 1e−9 m", async () => {
    for (const k of [0, 3]) {
      const s = session(k);
      for (const [x, y] of [[20, 300], [180, 310], [170, 120], [33.3, 101.7]]) {
        s.pointer(click(x, y));
        await new Promise((r) => setTimeout(r, 0));
      }
      s.finishDrawing();
      const line = s.layer.lines[0];
      expect(line.closed).toBe(true);
      for (const p of line.points) expect(Math.abs(g.local(p, k)[2])).toBeLessThanOrEqual(1e-9);
      expect(g.lineSection(line.points)).toBe(k);
    }
  });
});
