/**
 * What «Свойства» of an implicit model say (ТЗ 8.9.4; ADR 0026): the parameters as the core stores them (`GET /models/{id}` → `meta`, §2 of the contract:
 * `spec.canonical()` plus the results of the last build) read as facts — the kernel, the smoothing (η), the greatest condition number, the anisotropy, the step h, the
 * numbers (points, patches, the residual, the time) and the journal of the last build. A key the core did not give is «—», never 0 (8.0.1). The ellipsoid of the
 * anisotropy is drawn by `viz/ellipsoid` as it is: the core gives the angles and the ratios `(1, a2 / a1, a3 / a1)` (`anisotropy.ellipsoid_of` of the same numbers);
 * the size of the major axis shown is a quarter of the extent of the model. Pure.
 */
import type { Fact } from "../../shell/props";
import { isNum, numOrNull, obj, str } from "../estimation/api";
import { findTrend } from "../trends/state";
import { KERNEL_TITLES, type KernelKind, type ModelInfo } from "./api";
import { CLIP_TITLES, type ClipKind } from "./api";

const n = (v: number, digits = 2): string => v.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const sci = (v: number): string => (v === 0 ? "0" : Math.abs(v) >= 1e4 || Math.abs(v) < 1e-2 ? v.toExponential(2).replace(".", ",").replace("e+", "e") : n(v, 3));
const dash = "—";

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const nums = (v: unknown): number[] => arr(v).filter(isNum);

export const kernelText = (meta: Record<string, unknown>): string => {
  const k = str(meta.kernel) as KernelKind | null;
  const base = k ? (KERNEL_TITLES[k] ?? k) : dash;
  const r = numOrNull(meta.kernel_radius);
  return r !== null ? `${base}, радиус ${n(r)} м` : base;
};

/** The smoothing: «вручную, η = 0,05» / «по типу группы» (no number given) / «авто: cond ≤ 1e8». */
export function smoothingText(meta: Record<string, unknown>): string {
  const s = obj(meta.smoothing);
  if (s.mode === "auto") {
    const t = numOrNull(s.cond_target);
    return `авто${t !== null ? `: cond ≤ ${sci(t)}` : ""}${numOrNull(s.s) !== null ? `, начиная с η = ${sci(numOrNull(s.s) as number)}` : ""}`;
  }
  const v = numOrNull(s.s);
  return v !== null ? `вручную, η = ${sci(v)}` : "вручную, по типу группы (контакт 0, градиент 0,05)";
}

/** The angles of a global trend of the project (a structural one has local axes: none). */
export function trendAngles(id: number): Pick<Anisotropy, "ang1" | "ang2" | "ang3" | "ratios"> | null {
  const t = findTrend(id);
  return t && t.kind === "global" ? { ang1: t.ang1, ang2: t.ang2, ang3: t.ang3, ratios: t.ratios } : null;
}

export interface Anisotropy {
  /** `none` — isotropic, `manual` — the angles of the model, `trend` — the axes of a trend of the project */
  source: "none" | "manual" | "trend";
  ang1: number;
  ang2: number;
  ang3: number;
  ratios: [number, number, number];
  trendId: number | null;
  /** the angles are known (a trend of the project that is not read — or a structural one — has none) */
  known: boolean;
}

/** The anisotropy of the model; the angles of a trend are the trend's own (`trendAngles` — what the trends of the project say of that id). */
export function anisotropyOf(meta: Record<string, unknown>, trendAngles?: (id: number) => Pick<Anisotropy, "ang1" | "ang2" | "ang3" | "ratios"> | null): Anisotropy {
  const tid = numOrNull(meta.trend_id);
  const a = meta.anisotropy === null || meta.anisotropy === undefined ? null : obj(meta.anisotropy);
  const none: Anisotropy = { source: "none", ang1: 0, ang2: 0, ang3: 0, ratios: [1, 1, 1], trendId: null, known: false };
  if (tid !== null) {
    const t = trendAngles?.(tid) ?? null;
    return t ? { source: "trend", ...t, trendId: tid, known: true } : { ...none, source: "trend", trendId: tid };
  }
  if (!a) return none;
  const r = nums(a.ratios);
  return { source: "manual", ang1: numOrNull(a.ang1) ?? 0, ang2: numOrNull(a.ang2) ?? 0, ang3: numOrNull(a.ang3) ?? 0, ratios: [1, r[1] ?? 1, r[2] ?? 1], trendId: null, known: true };
}

/** The ellipsoid can be drawn: a manual anisotropy (or a global trend) with positive ratios. */
export const drawable = (a: Anisotropy): boolean => a.source !== "none" && a.known && a.ratios.every((r) => Number.isFinite(r) && r > 0);

export function anisotropyFacts(a: Anisotropy, trendName?: string | null): Fact[] {
  if (a.source === "none") return [{ label: "Анизотропия", value: "нет (шар)" }];
  if (a.source === "trend") {
    const head: Fact = { label: "Анизотропия", value: `из тренда «${trendName ?? `№${a.trendId}`}»` };
    // a structural trend has local axes: no single triple of angles
    return a.known ? [head, ...angleFacts(a)] : [head, { label: "Оси", value: "заданы трендом (локальные оси структурного тренда или тренд не прочитан)" }];
  }
  return [{ label: "Анизотропия", value: "задана вручную" }, ...angleFacts(a)];
}

const angleFacts = (a: Pick<Anisotropy, "ang1" | "ang2" | "ang3" | "ratios">): Fact[] => [
  { label: "Углы ang1, ang2, ang3", value: `${n(a.ang1)}°, ${n(a.ang2)}°, ${n(a.ang3)}°` },
  { label: "Отношения осей", value: `1 : ${n(a.ratios[1], 3)} : ${n(a.ratios[2], 3)}` },
];

/** The settings: the kernel, the smoothing, the step h, the levels, the patches, the data, the clipping. */
export function settingsFacts(meta: Record<string, unknown>): Fact[] {
  const pou = obj(meta.pou);
  const data = obj(meta.data);
  const post = obj(meta.post);
  const h = numOrNull(meta.h);
  const levels = numOrNull(meta.levels);
  const clip = str(post.clip) as ClipKind | null;
  const out: Fact[] = [
    { label: "Ядро", value: kernelText(meta) },
    { label: "Сглаживание (η)", value: smoothingText(meta) },
    { label: "Шаг h", value: h !== null ? `${n(h, 3)} м${levels !== null ? `, уровней ${n(levels, 0)}` : ""}` : dash },
    { label: "Участки", value: numOrNull(pou.n_patch) !== null ? `до ${n(pou.n_patch as number, 0)} точек, не меньше ${n(numOrNull(pou.n_min) ?? 0, 0)}, перекрытие ${n(numOrNull(pou.delta) ?? 0, 2)}` : dash },
  ];
  if (numOrNull(data.d_max) !== null) out.push({ label: "Данные", value: `поле «${str(data.field) ?? dash}», D_max ${n(data.d_max as number)} м, шаг по скважине ${n(numOrNull(data.step) ?? 0)} м` });
  if (clip) out.push({ label: "Обрезка", value: `${CLIP_TITLES[clip] ?? clip}${numOrNull(post.v_min) ? `, тела не меньше ${n(post.v_min as number)} м³` : ""}` });
  return out;
}

/** The numbers of the last build; without one — nothing (the panel says «не построена»). */
export function resultFacts(meta: Record<string, unknown>): Fact[] {
  const s = meta.stats === null || meta.stats === undefined ? null : obj(meta.stats);
  if (!s) return [];
  const pick = (k: string) => numOrNull(s[k]);
  const points = pick("points");
  const patches = pick("patches");
  const resid = pick("max_residual");
  const cond = pick("cond_max");
  const eta = pick("eta_max");
  const sec = pick("seconds");
  const reused = obj(s.reused);
  const out: Fact[] = [
    { label: "Точек", value: points !== null ? n(points, 0) : dash },
    { label: "Участков", value: patches !== null ? n(patches, 0) : dash },
    { label: "Невязка (макс.)", value: resid !== null ? `${sci(resid)} м` : dash },
    { label: "cond (макс.)", value: cond !== null ? sci(cond) : dash },
    { label: "η (макс.)", value: eta !== null ? sci(eta) : dash },
    { label: "Время", value: sec !== null ? `${n(sec, 1)} с` : dash },
  ];
  const rp = numOrNull(reused.patches);
  const rb = numOrNull(reused.bricks);
  if ((rp ?? 0) > 0 || (rb ?? 0) > 0) out.push({ label: "Взято из прежнего", value: `участков ${n(rp ?? 0, 0)}, кирпичей ${n(rb ?? 0, 0)}` });
  const lr = meta.last_run === null || meta.last_run === undefined ? null : obj(meta.last_run);
  if (lr) out.push({ label: "Последний запуск", value: `${lr.mode === "update" ? "обновление" : "построение"}, ${str(lr.at) ? (str(lr.at) as string).replace("T", " ").slice(0, 16) : dash}` });
  return out;
}

export interface GroupStats {
  group: number | null;
  name: string;
  points: string;
  patches: string;
  resid: string;
  cond: string;
  eta: string;
}

/** The numbers of the groups of the last build (`stats.by_group`), the names from the groups of the model. */
export function groupStats(info: Pick<ModelInfo, "meta">): GroupStats[] {
  const names = new Map<number, string>();
  for (const g of arr(info.meta.groups)) {
    const o = obj(g);
    if (isNum(o.id)) names.set(o.id, str(o.name) ?? `группа ${o.id}`);
  }
  return arr(obj(info.meta.stats).by_group).map((raw) => {
    const g = obj(raw);
    const id = numOrNull(g.group);
    const v = (k: string, f: (x: number) => string) => (numOrNull(g[k]) !== null ? f(g[k] as number) : dash);
    return { group: id, name: id !== null ? (names.get(id) ?? `группа ${id}`) : "Прочее", points: v("points", (x) => n(x, 0)), patches: v("patches", (x) => n(x, 0)), resid: v("max_residual", sci), cond: v("cond_max", sci), eta: v("eta_max", sci) };
  });
}

/** The journal lines of a job result (`journal`: strings); anything else is not a journal. */
export const journalOfResult = (result: unknown): string[] => arr(obj(result).journal).filter((l): l is string => typeof l === "string");

/** The centre and the size of the ellipsoid shown: the centre of the extent of the model, the major axis a quarter of its longest side. null — the model has no extent. */
export function ellipsoidFrame(meta: Record<string, unknown>): { centre: [number, number, number]; size: number } | null {
  const e = nums(meta.extent);
  const box = e.length === 6 ? e : nums(obj(meta.boundary).box);
  if (box.length !== 6 || !box.every(Number.isFinite)) return null;
  const span = Math.max(box[3] - box[0], box[4] - box[1], box[5] - box[2]);
  if (!(span > 0)) return null;
  return { centre: [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2], size: span / 4 };
}

/** «Устарела: причина» for the panel; null — the data are current. */
export { staleLine } from "./run";
