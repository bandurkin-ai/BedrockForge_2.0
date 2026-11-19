/**
 * The plane of a section and the series in the 3D window against the answers of the core (ADR 0016 §2, Э10 10.13):
 * `core/tests/data/sections/series_cases.json` — axes, K, s_k, O_k, the faces of the windows, (τ, u, w, t) and the
 * section of points and lines, the segments, development and half-spaces of polyline sections. `exact` — bit for
 * bit, otherwise |a − b| ≤ abs + rel·|b|; null — NaN. A line of 7.14 keeps its old clipping planes bit for bit.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Vec3 } from "../src/viz/core/vec3";
import { clipPlanes, sectionDirection, sectionNormal, type SectionLine } from "../src/viz/section/math";
import { allowedVexag, axisDot, halfSpaces, insidePlanes, isInclined, planeFromLine, planeLocal, planePoint, renderedPlane, windowPlanes } from "../src/viz/section/plane";
import { PolylineGeometry, SeriesGeometry, seriesGeometry, type SeriesParams } from "../src/viz/section/series";

type Num = number | null;
type P3 = [Num, Num, Num];
interface SeriesCase {
  id: string;
  exact: boolean;
  params: SeriesParams;
  az: number;
  phi: number;
  axes: { s: Vec3; v: Vec3; w: Vec3 };
  count: number;
  partition: boolean;
  sections: { k: number; s: number; origin: Vec3; towards: number; away: number; lo: number; hi: number }[];
  points: { p: P3; tau: Num; u: Num; w: Num; section: number; t: Num }[];
  lines: { xyz: P3[]; section: number }[];
}
interface PolylineCase {
  id: string;
  exact: boolean;
  params: SeriesParams;
  count: number;
  total: number;
  segments: { j: number; start: number; length: number; s: Vec3; v: Vec3; half_spaces: number[][] }[];
  points: { p: P3; segment: number; u: Num; w: Num; t: Num }[];
  lines: { xyz: P3[]; section: number }[];
}

const cases = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/sections/series_cases.json"), "utf-8")) as {
  tolerance: { rel: number; abs: number };
  series: SeriesCase[];
  polylines: PolylineCase[];
};
const tol = cases.tolerance;
const nan = (x: Num): number => (x === null ? NaN : x);
const pt = (p: P3): Vec3 => [nan(p[0]), nan(p[1]), nan(p[2])];

/** Compares as the rules of the answers say; `what` names the failing number. */
function same(a: number, b: Num, exact: boolean, what: string): void {
  const e = nan(b);
  if (Number.isNaN(e)) return void expect(a, what).toBeNaN();
  if (exact) return void expect(Object.is(a, e) || (a === 0 && e === 0), `${what}: ${a} ≠ ${e}`).toBe(true);
  expect(Math.abs(a - e) <= tol.abs + tol.rel * Math.abs(e), `${what}: ${a} vs ${e}`).toBe(true);
}
const sameVec = (a: ArrayLike<number>, b: ArrayLike<Num>, exact: boolean, what: string) => {
  expect(a.length, what).toBe(b.length);
  for (let i = 0; i < b.length; i++) same(a[i], b[i], exact, `${what}[${i}]`);
};

describe("series of sections against the core (series_cases.json)", () => {
  it("has the cases of the acceptance (Т8.1-1, Т8.1-5, Т8.1-7) and the tolerance 1e−12", () => {
    expect(tol.rel).toBe(1e-12);
    const ids = [...cases.series, ...cases.polylines].map((c) => c.id);
    for (const id of ["t8_1_1", "t8_1_5", "t8_1_7"]) expect(ids).toContain(id);
  });

  for (const c of cases.series) {
    it(`${c.id}: axes, K, s_k, O_k, windows, points and lines`, () => {
      const g = seriesGeometry(c.params);
      expect(g).toBeInstanceOf(SeriesGeometry);
      if (!(g instanceof SeriesGeometry)) return;
      const ex = c.exact;
      same(g.az, c.az, ex, "az");
      same(g.phi, c.phi, ex, "phi");
      sameVec(g.s, c.axes.s, ex, "s");
      sameVec(g.v, c.axes.v, ex, "v");
      sameVec(g.w, c.axes.w, ex, "w");
      expect(g.count).toBe(c.count);
      expect(g.partition).toBe(c.partition);
      for (const sec of c.sections) {
        same(g.offs[sec.k], sec.s, ex, `s_${sec.k}`);
        sameVec(g.origin(sec.k), sec.origin, ex, `O_${sec.k}`);
        sameVec(g.plane(sec.k).origin, sec.origin, ex, `plane(${sec.k}).origin`);
        same(g.towards[sec.k], sec.towards, ex, `towards_${sec.k}`);
        same(g.away[sec.k], sec.away, ex, `away_${sec.k}`);
        same(g.lo[sec.k], sec.lo, ex, `lo_${sec.k}`);
        same(g.hi[sec.k], sec.hi, ex, `hi_${sec.k}`);
      }
      for (const [i, q] of c.points.entries()) {
        const p = pt(q.p);
        same(g.depth(p), q.tau, ex, `tau of point ${i}`);
        const [u, w] = g.local(p);
        same(u, q.u, ex, `u of point ${i}`);
        same(w, q.w, ex, `w of point ${i}`);
        expect(g.sectionOf(p), `section of point ${i}`).toBe(q.section);
        if (q.section >= 0) {
          same(g.local(p, q.section)[2], q.t, ex, `t of point ${i}`);
          // the plane of the section measures the same (u, w, t)
          const local = planeLocal(g.plane(q.section), p);
          sameVec(local, [q.u, q.w, q.t], ex, `plane(${q.section}).local of point ${i}`);
        } else expect(q.t).toBeNull();
      }
      for (const [i, l] of c.lines.entries()) expect(g.lineSection(l.xyz.map(pt)), `line ${i}`).toBe(l.section);
    });
  }

  for (const c of cases.polylines) {
    it(`${c.id}: segments, development, half-spaces, points and lines`, () => {
      const g = seriesGeometry(c.params);
      expect(g).toBeInstanceOf(PolylineGeometry);
      if (!(g instanceof PolylineGeometry)) return;
      const ex = c.exact;
      expect(g.count).toBe(c.count);
      same(g.total, c.total, ex, "total");
      expect(g.segments).toBe(c.segments.length);
      for (const seg of c.segments) {
        same(g.starts[seg.j], seg.start, ex, `U_${seg.j}`);
        same(g.lengths[seg.j], seg.length, ex, `L_${seg.j}`);
        sameVec(g.sAxes[seg.j], seg.s, ex, `s_${seg.j}`);
        sameVec(g.vAxes[seg.j], seg.v, ex, `v_${seg.j}`);
        const hs = g.halfSpaces(seg.j);
        expect(hs.length).toBe(seg.half_spaces.length);
        // the constants are UTM-sized: compared with the relative tolerance
        hs.forEach((h, i) => sameVec(h, seg.half_spaces[i], ex, `half-space ${i} of segment ${seg.j}`));
      }
      for (const [i, q] of c.points.entries()) {
        const p = pt(q.p);
        expect(g.segmentOf(p), `segment of point ${i}`).toBe(q.segment);
        sameVec(g.local(p), [q.u, q.w, q.t], ex, `local of point ${i}`);
      }
      for (const [i, l] of c.lines.entries()) expect(g.lineSection(l.xyz.map(pt)), `line ${i}`).toBe(l.section);
    });
  }

  it("Т8.1-5: φ = 60° — the point of (u; w) = (3; 2) on a section has t = 0 and those (u; w)", () => {
    const c = cases.series.find((x) => x.id === "t8_1_5")!;
    const g = seriesGeometry(c.params) as SeriesGeometry;
    for (const k of [0, 2, 5]) {
      const pl = g.plane(k);
      const p = planePoint(pl, 3, 2);
      const [u, w, t] = planeLocal(pl, p);
      expect(Math.abs(u - 3)).toBeLessThan(1e-12 * 1e4);
      expect(Math.abs(w - 2)).toBeLessThan(1e-12 * 1e4);
      expect(Math.abs(t)).toBeLessThan(1e-12 * 1e4);
      expect(g.sectionOf(p)).toBe(k);
    }
  });
});

describe("SectionPlane: a line of 7.14 and the space of the view", () => {
  const origin = { x: 512_000, y: 7_012_000 };
  const azimuths = [0, 90, 180, 270, 45, 63, 135.5, 225, 300, 359.999, 0.001, 12.345678];

  it("a line of 7.14 gives the planes of `clipPlanes` bit for bit (with and without a length)", () => {
    for (const az of azimuths) {
      for (const length of [null, 0.5, 200, 3000]) {
        for (const [x0, y0] of [[512_100.25, 7_011_950.5], [512_000, 7_012_000], [-3.5, 0], [0, 0]]) {
          const line: SectionLine = { x0, y0, az, length, towards: 15, away: 25 };
          const old = clipPlanes(line, origin);
          const now = windowPlanes(planeFromLine(line), origin);
          expect(now.length).toBe(old.length);
          now.forEach((pl, i) => {
            for (let j = 0; j < 3; j++) expect(Object.is(pl.normal[j], old[i].normal[j]), `az ${az} plane ${i} n${j}: ${pl.normal[j]} vs ${old[i].normal[j]}`).toBe(true);
            expect(Object.is(pl.constant, old[i].constant), `az ${az} plane ${i} c`).toBe(true);
          });
          // the exaggeration does not change a vertical plane
          for (const pl of now) expect(renderedPlane(pl, 5)).toEqual(pl);
        }
      }
    }
  });

  it("the line plane: ŝ and −v̂ are the direction and the normal of 7.14, ŵ = Z, u and t are along and across", () => {
    for (const az of azimuths) {
      const line: SectionLine = { x0: 512_100, y0: 7_011_950, az, length: null, towards: 25, away: 25 };
      const p = planeFromLine(line);
      expect(p.s).toEqual(sectionDirection(az));
      const n = sectionNormal(az);
      for (let i = 0; i < 2; i++) expect(p.v[i]).toBeCloseTo(-n[i], 15);
      expect(p.w).toEqual([0, 0, 1]);
      expect(isInclined(p)).toBe(false);
      const q: Vec3 = [line.x0 + 7 * n[0] + 3 * p.s[0], line.y0 + 7 * n[1] + 3 * p.s[1], -40];
      const [u, w, t] = planeLocal(p, q);
      expect(u).toBeCloseTo(3, 8);
      expect(w).toBe(-40);
      expect(t).toBeCloseTo(-7, 8); // towards the viewer: t < 0
    }
  });

  it("an inclined and a plan series: window planes in rendered space at ×1 and ×5 keep the same points", () => {
    const o3 = { x: 1000, y: 2000, z: 300 };
    const inclined = (seriesGeometry({ type: "inclined", p1: [1000.5, 2000.25, 300], az: 30, phi: 60, s0: 0, s1: 100, step: 20, towards: 10, away: 10 }) as SeriesGeometry).plane(2);
    const plan = (seriesGeometry({ type: "plan", p1: [500, 600, 400], s0: 0, s1: 100, step: 10, towards: 5, away: 5 }) as SeriesGeometry).plane(3);
    expect(isInclined(inclined)).toBe(true);
    expect(isInclined(plan)).toBe(false);
    expect(allowedVexag(inclined, 5)).toBe(1);
    expect(allowedVexag(plan, 5)).toBe(5);
    for (const pl of [inclined, plan]) {
      const scene = windowPlanes(pl, o3);
      expect(scene.length).toBe(2);
      let n = 0;
      for (let i = 0; i < 400; i++) {
        const u = ((i * 37) % 101) - 50;
        const w = ((i * 53) % 89) - 44;
        const t = ((i * 29) % 41) - 20; // −20 … 20: inside and outside the window ±(5 | 10)
        const p = planePoint(pl, u, w);
        const q: Vec3 = [p[0] + t * pl.v[0] - o3.x, p[1] + t * pl.v[1] - o3.y, p[2] + t * pl.v[2] - o3.z];
        const tq = axisDot([q[0] + o3.x - pl.base[0], q[1] + o3.y - pl.base[1], q[2] + o3.z - pl.base[2]], pl.v) - pl.offset;
        if (Math.abs(Math.abs(tq) - pl.towards) < 1e-6) continue;
        const inside = insidePlanes(scene, q[0], q[1], q[2]);
        expect(inside, `t = ${tq}`).toBe(tq >= -pl.towards && tq <= pl.away);
        for (const e of [1, 5, -2]) {
          const rendered = scene.map((s) => renderedPlane(s, e));
          expect(insidePlanes(rendered, q[0], q[1], q[2] * e)).toBe(inside);
          for (const r of rendered) expect(Math.hypot(...r.normal)).toBeCloseTo(1, 14);
        }
        n++;
      }
      expect(n).toBeGreaterThan(300);
    }
    // the plan: horizontal planes, the window across Z; at ×5 the rendered constants are stretched
    const r5 = windowPlanes(plan, o3).map((s) => renderedPlane(s, 5));
    expect(r5[0].normal[2]).toBe(1);
    expect(Math.abs(r5[0].normal[0]) + Math.abs(r5[0].normal[1])).toBe(0);
    expect(halfSpaces(plan)[0][2]).toBe(-1);
  });
});
