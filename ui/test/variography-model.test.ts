// ТД14-3 (ADR 0022 §6, decision 19): the mirror of the model in the window equals the core — every curve and point
// of `core/tests/data/variography/model_curves.json` (written by `bf_core/geostat/variogram/model.py`) within its
// tolerances: γ and C 1e−12·max(1, sill), h_r 1e−12 relative. The axes come from `viz/ellipsoid/frame.ts`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Vec3 } from "../src/viz/core/vec3";
import { axisOf, cov, direction, effectiveStructures, gamma, gammaAlong, model2d, modelBody, newStructure, prepare, readModel, reduced, shape, sill, sillWarning, structureType } from "../src/modules/variography/model";

interface Curve {
  axis: 1 | 2 | 3 | null;
  azimuth: number | null;
  plunge: number | null;
  u: Vec3;
  h: number[];
  gamma: number[];
}
interface Point {
  delta: Vec3;
  hr: number[];
  gamma: number;
  cov: number;
}
interface Case {
  id: string;
  model: Record<string, unknown>;
  sill: number;
  form2d?: { nug: number; structures: { it: number; cc: number; hmaj: number; hmin: number; azi: number }[] };
  curves: Curve[];
  points: Point[];
}

const FIXTURE = resolve(__dirname, "../../core/tests/data/variography/model_curves.json");
const data = JSON.parse(readFileSync(FIXTURE, "utf8")) as { tolerance: { gamma: number; hr: number }; cases: Case[] };
const TOL = data.tolerance;

const close = (got: number, want: number, tol: number, what: string) => {
  if (Number.isNaN(want)) return expect(got, what).toBeNaN();
  expect(Math.abs(got - want), `${what}: ${got} ≠ ${want}`).toBeLessThanOrEqual(tol);
};

describe("ТД14-3: model_curves.json", () => {
  it("has the cases of 8.6.3 and the tolerances of ADR 0022 §6", () => {
    expect(data.cases.length).toBeGreaterThanOrEqual(9);
    expect(TOL.gamma).toBe(1e-12);
    expect(TOL.hr).toBe(1e-12);
  });

  for (const c of data.cases) {
    describe(c.id, () => {
      const m = readModel(c.model);
      const tg = TOL.gamma * Math.max(1, c.sill);

      it("the sill", () => close(sill(m), c.sill, tg, "sill"));

      it("the curves along the axes and the directions", () => {
        for (const curve of c.curves) {
          const u = curve.axis ? axisOf(m, curve.axis) : direction(curve.azimuth!, curve.plunge!);
          for (let i = 0; i < 3; i++) close(u[i], curve.u[i], 1e-15, `${c.id} u[${i}]`);
          const g = gammaAlong(m, curve.h, u);
          curve.h.forEach((h, i) => close(g[i], curve.gamma[i], tg, `${c.id} axis ${curve.axis ?? `${curve.azimuth}/${curve.plunge}`} h=${h}`));
        }
      });

      it("the points: h_r of every structure, γ and C", () => {
        const p = prepare(m);
        for (const pt of c.points) {
          const hr = reduced(p, pt.delta);
          expect(hr.length).toBe(pt.hr.length);
          hr.forEach((x, i) => close(x, pt.hr[i], TOL.hr * Math.max(1, Math.abs(pt.hr[i])), `${c.id} hr[${i}] at ${pt.delta}`));
          close(gamma(p, pt.delta), pt.gamma, tg, `${c.id} γ at ${pt.delta}`);
          close(cov(p, pt.delta), pt.cov, tg, `${c.id} C at ${pt.delta}`);
        }
      });

      if (c.form2d) {
        it("the model of the tab «Статистика» (form2d) is the same model", () => {
          const f = c.form2d!;
          expect(model2d(f.nug, f.structures)).toStrictEqual(readModel(c.model));
        });
      }
    });
  }
});

describe("the edges of the mirror", () => {
  const m = readModel({ c0: 0.2, structures: [{ type: "spherical", c: 0.8, ang1: 30, ang2: -10, ang3: 0, a1: 100, a2: 50, a3: 10 }] });

  it("γ(0) = 0, a NaN lag is NaN (not zero), C(0) is the sill", () => {
    expect(gamma(m, [0, 0, 0])).toBe(0);
    expect(gamma(m, [NaN, 1, 0])).toBeNaN();
    expect(cov(m, [0, 0, 0])).toBe(1);
    expect(shape("spherical", NaN)).toBeNaN();
    expect(shape("spherical", 1)).toBe(1);
    expect(shape("exponential", 1)).toBeCloseTo(1 - Math.exp(-3), 15);
  });

  it("«общие оси»: every structure takes the angles of the first, in the body too", () => {
    const two = readModel({ c0: 0, common_axes: true, structures: [{ type: 1, c: 1, ang1: 30, a1: 10 }, { type: "2", c: 1, ang1: 80, ang2: 5, a1: 20 }] });
    expect(two.structures[1].type).toBe("exponential");
    expect(two.structures[0].a2).toBe(10);
    expect(effectiveStructures(two)[1].ang1).toBe(30);
    expect((modelBody(two).structures as { ang1: number; ang2: number }[])[1]).toMatchObject({ ang1: 30, ang2: 0 });
    expect(effectiveStructures({ ...two, common_axes: false })[1].ang1).toBe(80);
  });

  it("types, a new nested structure, the warning of the sill", () => {
    expect(structureType("Gaussian")).toBe("gaussian");
    expect(structureType(4)).toBeNull();
    const s = newStructure(m);
    expect(s).toMatchObject({ a1: 200, a2: 100, a3: 20, ang1: 30, ang2: -10 });
    expect(sillWarning(m, 1)).toBeNull();
    expect(sillWarning(m, 0.5)).toMatch(/1,2·σ²/);
    expect(sillWarning(m, null)).toBeNull();
  });
});
