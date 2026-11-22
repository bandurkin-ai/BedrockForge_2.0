/**
 * The fan, the rose and the map of the window «Вариография» (ТЗ 8.6.4 «Веер», «Карта вариограмм»; ADR 0022 §5,
 * §13, decision 8; e16-api.md `POST /map`): the body of the job `variography.map` (the plane — the axes of the model
 * the axes are computed by), the reader of its `job.result` `{key, …, plane, sampled, fan, rose, map, warnings,
 * fresh, stages}` and of the result of «Перестроить» `{model_id, key, fresh, stages, fit, back, events}`; the click
 * on a ray or on the map — the angles of the same plane whose a1 lies at θ (the mirror of `fan.turn` of the core:
 * a1′ = cos θ·a1 + sin θ·a2, a2′ = −sin θ·a1 + cos θ·a2, a3′ = a3), and back — θ of the a1 of the model in the
 * plane of a calculation. θ goes from a1 towards a2; at zero angles a2 is west, so the plan is drawn a1 up, a2 to the
 * left, θ counter-clockwise (the decision of the orchestrator on the mirror of the plane). Read against the real
 * answers (`window_answers.json`). No React.
 */
import { readRose, readVarMap, type RoseData, type VarMapData } from "../../charts/variogram/scale";
import { anglesOf, frameOf, type Angles } from "../../viz/ellipsoid/frame";
import { sinCosDegCore } from "../../viz/section/plane";
import { numOrNull, readDirection, type DirectionCurve } from "./api";
import { readJobCalc, type JobCalc } from "./calc";
import { newStructure, type VModel } from "./model";
import type { AxesAngles } from "./state";

/** `job.result` of `variography.map`: the common form of a calculation, the plane, the fan with its rose, the map. */
export interface MapCalc extends Omit<JobCalc, "directions" | "downhole"> {
  plane: AxesAngles;
  /** the rays of the fan (`theta`, the world `az`, `plunge`); null — not ordered */
  fan: DirectionCurve[] | null;
  rose: RoseData | null;
  map: VarMapData | null;
}

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export function readMapCalc(raw: unknown): MapCalc {
  const o = obj(raw);
  const common = readJobCalc({ ...o, directions: [], downhole: null });
  const p = obj(o.plane);
  const fan = o.fan === null || o.fan === undefined ? null : (Array.isArray(obj(o.fan).directions) ? (obj(o.fan).directions as unknown[]) : []).map(readDirection);
  return { ...common, plane: { ang1: numOrNull(p.ang1) ?? 0, ang2: numOrNull(p.ang2) ?? 0, ang3: numOrNull(p.ang3) ?? 0 }, fan, rose: readRose(o.rose), map: readVarMap(o.map) };
}

/** The rays of the rose with the world direction of the ray of the fan of the same θ (the labels are world az / plunge). */
export function roseWithWorld(m: Pick<MapCalc, "rose" | "fan">): RoseData | null {
  if (!m.rose) return null;
  const by = new Map((m.fan ?? []).filter((d) => d.theta !== undefined).map((d) => [d.theta as number, d]));
  return { ...m.rose, rays: m.rose.rays.map((r) => (by.has(r.theta) ? { ...r, az: by.get(r.theta)!.az, plunge: by.get(r.theta)!.plunge } : r)) };
}

/** The result of the job «Перестроить» (`variography.rebuild`). */
export interface RebuildResult {
  modelId: number | null;
  key: string | null;
  fresh: boolean;
  stages: Record<string, number>;
  fit: Record<string, unknown> | null;
  back: Record<string, unknown> | null;
}

export function readRebuild(raw: unknown): RebuildResult {
  const o = obj(raw);
  const j = readJobCalc({ stages: o.stages, fresh: o.fresh });
  return { modelId: numOrNull(o.model_id), key: typeof o.key === "string" ? o.key : null, fresh: j.fresh, stages: j.stages, fit: o.fit ? obj(o.fit) : null, back: o.back ? obj(o.back) : null };
}

// ---- the click on a ray: θ → the angles ----

const z0 = (x: number) => x + 0;

/** The angles of the plane `plane` whose a1 lies at θ (from a1 towards a2); θ ≡ 0 (mod 360) — the angles as they are. */
export function turnAngles(plane: Angles, theta: number): Angles {
  const f = frameOf(plane);
  if (!Number.isFinite(theta) || ((theta % 360) + 360) % 360 === 0) return { ang1: f.ang1, ang2: f.ang2, ang3: f.ang3 };
  const [s, k] = sinCosDegCore(theta);
  const u1 = [0, 1, 2].map((i) => z0(k * f.u1[i] + s * f.u2[i])) as [number, number, number];
  const u2 = [0, 1, 2].map((i) => z0(-s * f.u1[i] + k * f.u2[i])) as [number, number, number];
  return anglesOf(u1, u2, f.ang1);
}

/** θ of the a1 of `angles` in the plane of a calculation, 0…180° (a direction has no sign); null — a1 is off the plane. */
export function thetaInPlane(plane: Angles, angles: Angles): number | null {
  const p = frameOf(plane);
  const u = frameOf(angles).u1;
  const x = u[0] * p.u1[0] + u[1] * p.u1[1] + u[2] * p.u1[2];
  const y = u[0] * p.u2[0] + u[1] * p.u2[1] + u[2] * p.u2[2];
  if (Math.hypot(x, y) < 0.999) return null; // the a1 of the model left the plane (its a3 differs)
  const t = (Math.atan2(y, x) * 180) / Math.PI;
  return z0((((t % 180) + 180) % 180) % 180);
}

/** The same angles (to 1e−9°): the plane of the calculation is the plane of the model. */
export const samePlane = (a: Angles, b: Angles): boolean => {
  const fa = frameOf(a);
  const fb = frameOf(b);
  const dot = fa.u3[0] * fb.u3[0] + fa.u3[1] * fb.u3[1] + fa.u3[2] * fb.u3[2];
  return Math.abs(Math.abs(dot) - 1) < 1e-12;
};

/**
 * The model turned by a click at θ of the plane of a calculation: the angles of the first structure (with «общие
 * оси» — of every one) become those of the plane whose a1 is at θ; a model without structures gets one (spherical,
 * the angles of the click) — the axes of the calculation are the axes of its first structure.
 */
export function turnModel(m: VModel, plane: Angles, theta: number, hint?: { sigma2?: number | null; range?: number }): VModel {
  const a = turnAngles(plane, theta);
  if (!m.structures.length) return { ...m, structures: [{ ...newStructure(m, "spherical", hint), ...a }] };
  return { ...m, structures: m.structures.map((s, i) => (i === 0 || m.common_axes ? { ...s, ...a } : s)) };
}

// ---- what the view shows ----

/** «Плоскость: Аз 30° · ang2 −10° · ang3 0°» of a calculation. */
export function planeText(p: AxesAngles): string {
  const d = (x: number) => `${x.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}°`;
  return `Плоскость a1–a2: ang1 ${d(p.ang1)} · ang2 ${d(p.ang2)} · ang3 ${d(p.ang3)}`;
}

/** «θ 30° · Аз 330° · Пог 0°» — the label of a ray of the fan. */
export function rayLabel(d: Pick<DirectionCurve, "theta" | "az" | "plunge">): string {
  const f = (x: number) => `${x.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}°`;
  return `θ ${f(d.theta ?? 0)} · Аз ${f(d.az)} · Пог ${f(d.plunge)}`;
}
