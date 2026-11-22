/**
 * The variogram model on the side of the window (ТЗ 8.6.3; ADR 0022 §6, decision 19) — the mirror of
 * `core/bf_core/geostat/variogram/model.py`, read against the shared answers `core/tests/data/variography/
 * model_curves.json` (ТД14-3, 1e−12). The axes of a structure are the rows of R of `viz/ellipsoid/frame.ts`
 * (not repeated here):
 *
 *     h_r = √((u₁·Δ / a1)² + (u₂·Δ / a2)² + (u₃·Δ / a3)²)
 *     spherical     g = h_r·(1.5 − 0.5·h_r²) at h_r < 1, else 1
 *     exponential   g = −expm1(−3·h_r)            practical range (95 % of c), as in BF
 *     gaussian      g = −expm1(−3·h_r²)
 *     γ(Δ) = C0 + Σ c_s·g_s (left to right) at Δ ≠ 0, γ(0) = 0;  C(Δ) = sill − γ(Δ)
 *
 * A NaN in Δ gives NaN: a missing lag is never a zero. «Общие оси» — the angles of every structure are those of the
 * first. Pure: no React, no three.js; the curves of the charts and the ellipsoid of the scene are computed here
 * (≤ 50 ms of a drag).
 */
import type { Vec3 } from "../../viz/core/vec3";
import { frameOf, reducedDistance, type Angles, type EllipsoidFrame } from "../../viz/ellipsoid/frame";
import { sinCosDegCore } from "../../viz/section/plane";

export const STRUCTURE_TYPES = ["spherical", "exponential", "gaussian"] as const;
export type StructureType = (typeof STRUCTURE_TYPES)[number];
export const MODEL_TYPES = ["semivariogram", "correlogram", "pairwise", "nscore"] as const;
export type ModelType = (typeof MODEL_TYPES)[number];
export const MAX_STRUCTURES = 3;

export const STRUCTURE_TITLES: Record<StructureType, string> = { spherical: "сферическая", exponential: "экспоненциальная", gaussian: "гауссова" };
export const MODEL_TITLES: Record<ModelType, string> = {
  semivariogram: "полувариограмма",
  correlogram: "коррелограмма (1 − ρ)",
  pairwise: "парная относительная",
  nscore: "нормальные оценки",
};

export interface Structure extends Angles {
  type: StructureType;
  /** the contribution c ≥ 0 */
  c: number;
  a1: number;
  a2: number;
  a3: number;
}

/** The form of `model` of the API (`fit`, `back`, `curve`, `models`; e16-api.md). */
export interface VModel {
  type: ModelType;
  c0: number;
  structures: Structure[];
  common_axes: boolean;
}

export const EMPTY_MODEL: VModel = { type: "semivariogram", c0: 0, structures: [], common_axes: true };

const GSLIB: Record<string, StructureType> = { "1": "spherical", "2": "exponential", "3": "gaussian" };

/** A structure type: its name or the code 1, 2, 3 of GSLIB; null — unknown. */
export function structureType(v: unknown): StructureType | null {
  const t = typeof v === "number" ? String(v) : typeof v === "string" ? v.trim().toLowerCase() : "";
  return (STRUCTURE_TYPES as readonly string[]).includes(t) ? (t as StructureType) : (GSLIB[t] ?? null);
}

/** g(h_r) of a structure with the contribution 1 (`shape` of the core). */
export function shape(type: StructureType, hr: number): number {
  if (type === "spherical") return Number.isNaN(hr) ? NaN : hr < 1 ? hr * (1.5 - 0.5 * hr * hr) : 1;
  if (type === "exponential") return -Math.expm1(-3 * hr);
  return -Math.expm1(-3 * hr * hr);
}

/** The structures as they are computed: with «общие оси» every one takes the angles of the first. */
export function effectiveStructures(m: Pick<VModel, "structures" | "common_axes">): Structure[] {
  const [first] = m.structures;
  if (!m.common_axes || !first) return m.structures;
  return m.structures.map((s) => ({ ...s, ang1: first.ang1, ang2: first.ang2, ang3: first.ang3 }));
}

/** The frame (u1, u2, u3) of a structure — `frameOf` of `viz/ellipsoid/frame.ts`. */
export const structureFrame = (s: Angles): EllipsoidFrame => frameOf(s);

/** C0 + Σ c, left to right. */
export function sill(m: Pick<VModel, "c0" | "structures">): number {
  let total = m.c0;
  for (const s of m.structures) total += s.c;
  return total;
}

/** A model prepared for many lag vectors: the frames once. */
export interface Prepared {
  c0: number;
  sill: number;
  parts: { s: Structure; f: EllipsoidFrame }[];
}

export function prepare(m: VModel): Prepared {
  return { c0: m.c0, sill: sill(m), parts: effectiveStructures(m).map((s) => ({ s, f: structureFrame(s) })) };
}

const prep = (m: VModel | Prepared): Prepared => ("parts" in m ? m : prepare(m));

/** h_r of each structure for one lag vector. */
export function reduced(m: VModel | Prepared, d: Vec3): number[] {
  return prep(m).parts.map(({ s, f }) => reducedDistance(f, s, d));
}

/** γ(Δ): 0 at Δ = 0, NaN where Δ has a NaN. */
export function gamma(m: VModel | Prepared, d: Vec3): number {
  if (d[0] === 0 && d[1] === 0 && d[2] === 0) return 0;
  if (Number.isNaN(d[0]) || Number.isNaN(d[1]) || Number.isNaN(d[2])) return NaN;
  const p = prep(m);
  let total = p.c0;
  for (const { s, f } of p.parts) total = total + s.c * shape(s.type, reducedDistance(f, s, d));
  return total;
}

/** C(Δ) = sill − γ(Δ). */
export function cov(m: VModel | Prepared, d: Vec3): number {
  const p = prep(m);
  return p.sill - gamma(p, d);
}

const fmod360 = (x: number) => ((x % 360) + 360) % 360;

/** u = (cos p·sin Az, cos p·cos Az, −sin p): azimuth clockwise from north, plunge positive down (`direction` of the core). */
export function direction(azimuth: number, plunge = 0): Vec3 {
  if (!Number.isFinite(azimuth) || !Number.isFinite(plunge) || Math.abs(plunge) > 90) throw new RangeError("Направление: азимут — число, погружение от −90° до +90°");
  const [s1, c1] = sinCosDegCore(fmod360(azimuth) + 0);
  const [s2, c2] = sinCosDegCore(-plunge + 0);
  return [c2 * s1 + 0, c2 * c1 + 0, s2 + 0];
}

/** The axis 1, 2, 3 of the model — of its first structure (of zero angles when there is none). */
export function axisOf(m: Pick<VModel, "structures">, axis: 1 | 2 | 3): Vec3 {
  const s = m.structures[0];
  const f = structureFrame(s ?? { ang1: 0, ang2: 0, ang3: 0 });
  return axis === 1 ? f.u1 : axis === 2 ? f.u2 : f.u3;
}

/** γ at the distances h along u (Δ = h·u). */
export function gammaAlong(m: VModel | Prepared, h: readonly number[], u: Vec3): number[] {
  const p = prep(m);
  return h.map((x) => gamma(p, [x * u[0], x * u[1], x * u[2]]));
}

/** The longest range of the model (for the extent of a curve, 1,5 × as `/curve`); 0 — no structure. */
export function longestRange(m: Pick<VModel, "structures">): number {
  return m.structures.reduce((a, s) => Math.max(a, s.a1, s.a2, s.a3), 0);
}

/** n + 1 distances 0 … h_max. */
export function distances(hMax: number, n = 100): number[] {
  return Array.from({ length: n + 1 }, (_, i) => (hMax * i) / n);
}

/** The model of the tab «Статистика» (BF `{nug, structures: [{it, cc, hmaj, hmin, azi}]}`): ang1 = azi, a2 = a3 = hmin. */
export function model2d(nug: number, items: readonly { it: unknown; cc: number; hmaj: number; hmin?: number | null; azi?: number | null }[]): VModel {
  const structures = items.map((x): Structure => {
    const hmin = x.hmin ?? x.hmaj;
    return { type: structureType(x.it) ?? "spherical", c: x.cc, a1: x.hmaj, a2: hmin, a3: hmin, ang1: fmod360(x.azi ?? 0) + 0, ang2: 0, ang3: 0 };
  });
  return { type: "semivariogram", c0: nug, structures, common_axes: structures.every((s) => s.ang1 === structures[0].ang1) };
}

// ---- the API form ----

const num = (v: unknown, dflt: number): number => (typeof v === "number" && Number.isFinite(v) ? v : dflt);

/** A model of an answer (`fit`, `import`, `GET models/{id}` → `meta`) in the form of the window; missing a2, a3 — a1. */
export function readModel(raw: unknown): VModel {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const type = (MODEL_TYPES as readonly string[]).includes(String(o.type)) ? (o.type as ModelType) : "semivariogram";
  const list = Array.isArray(o.structures) ? o.structures.slice(0, MAX_STRUCTURES) : [];
  const structures = list.map((r): Structure => {
    const s = (r && typeof r === "object" ? r : {}) as Record<string, unknown>;
    const a1 = num(s.a1, 1);
    return { type: structureType(s.type) ?? "spherical", c: num(s.c, 0), a1, a2: num(s.a2, a1), a3: num(s.a3, a1), ang1: num(s.ang1, 0), ang2: num(s.ang2, 0), ang3: num(s.ang3, 0) };
  });
  return { type, c0: num(o.c0, 0), structures, common_axes: o.common_axes !== false };
}

/** The body `model` of a request: the angles of «общие оси» written as computed. */
export function modelBody(m: VModel): Record<string, unknown> {
  return {
    type: m.type,
    c0: m.c0,
    common_axes: m.common_axes,
    structures: effectiveStructures(m).map((s) => ({ type: s.type, c: s.c, ang1: s.ang1, ang2: s.ang2, ang3: s.ang3, a1: s.a1, a2: s.a2, a3: s.a3 })),
  };
}

/** A new structure for the table of the model: the angles of the first, the ranges of the last × 2 (nested). */
export function newStructure(m: VModel, type: StructureType = "spherical", hint?: { sigma2?: number | null; range?: number }): Structure {
  const first = m.structures[0];
  const last = m.structures[m.structures.length - 1];
  const r = last ? 2 : 1;
  const a1 = last ? last.a1 * r : (hint?.range ?? 100);
  return {
    type,
    c: hint?.sigma2 && hint.sigma2 > 0 ? hint.sigma2 / 2 : 0.5,
    a1,
    a2: last ? last.a2 * r : a1,
    a3: last ? last.a3 * r : a1,
    ang1: first?.ang1 ?? 0,
    ang2: first?.ang2 ?? 0,
    ang3: first?.ang3 ?? 0,
  };
}

/** The warning of the window (ADR 0022 §16): the sill above 1,2·σ²; null — none (or σ² unknown). */
export function sillWarning(m: VModel, sigma2: number | null): string | null {
  if (sigma2 === null || !(sigma2 > 0)) return null;
  const s = sill(m);
  if (s <= 1.2 * sigma2) return null;
  const f = (x: number) => x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 });
  return `Порог модели ${f(s)} больше 1,2·σ² (${f(1.2 * sigma2)})`;
}
