/**
 * The form of «Новая серия» and of the parameters of a series (ТЗ 8.1.1–8.1.2; ADR 0016 §1): the fields as text
 * (a decimal comma or a dot, spaces between thousands), what they become for the core, the field an error of the
 * core names (`field` of a 422), the own window of a 409 (never window.confirm), the windows of one section.
 * No React: the windows (`SeriesWindow.tsx`, `SeriesTable.tsx`) only draw this.
 *
 * Types: vertical — P1 → P2 in plan or P1 and the azimuth A; plan — the top and the bottom levels and the step
 * in Z (north up, A = 90°); inclined — P1 (with Z), A and the dip φ; polyline — the vertices P0…Pm in plan and
 * the corridor (one development, K = 1).
 */
import { CANCEL, askChoice } from "../lines/choice";
import { parseNumber } from "../lines/modify";
import type { Vec3 } from "../../viz/core/vec3";
import type { SectionLine } from "../../viz/section/math";
import { seriesAxes } from "../../viz/section/plane";
import { MAX_K, seriesCount, type SeriesKind } from "../../viz/section/series";
import { seriesApi, seriesFault, type SeriesMeta, type SeriesParamsIn, type SeriesRow, type ShadowMode } from "./seriesApi";
import { seriesRange, type CurrentSection } from "./seriesState";

export type DirMode = "p2" | "az";

export interface SeriesForm {
  type: SeriesKind;
  dir: DirMode;
  name: string;
  p1x: string;
  p1y: string;
  p1z: string;
  p2x: string;
  p2y: string;
  az: string;
  phi: string;
  s0: string;
  s1: string;
  step: string;
  towards: string;
  away: string;
  top: string;
  bottom: string;
  corridor: string;
  /** the vertices of a polyline, one «X; Y» a line */
  polyline: string;
  template: string;
  shadow: ShadowMode;
}

export type FieldId = Exclude<keyof SeriesForm, "type" | "dir" | "shadow"> | "type" | "shadow";

export const TYPE_TITLES: Record<SeriesKind, string> = { vertical: "Вертикальная", plan: "План", inclined: "Наклонная", polyline: "Ломаная" };
export const SHADOW_TITLES: Record<ShadowMode, string> = { none: "Нет", prev: "Предыдущий", next: "Следующий", both: "Оба" };

export const FIELD_LABELS: Record<FieldId, string> = {
  type: "Тип", name: "Название", p1x: "X P1", p1y: "Y P1", p1z: "Z P1", p2x: "X P2", p2y: "Y P2", az: "Азимут A, °", phi: "Наклон φ, °",
  s0: "Начало s0, м", s1: "Конец s1, м", step: "Шаг Δ, м", towards: "Окно «к себе», м", away: "Окно «от себя», м", top: "Верхняя отметка, м",
  bottom: "Нижняя отметка, м", corridor: "Ширина коридора, м", polyline: "Вершины ломаной", template: "Шаблон имён", shadow: "Тень",
};

/** A number as the window shows it: a decimal comma, at most 6 decimals, no «−0». */
export function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return String(Number(v.toFixed(6)) + 0).replace(".", ",");
}

const BLANK: SeriesForm = {
  type: "vertical", dir: "az", name: "", p1x: "0", p1y: "0", p1z: "0", p2x: "", p2y: "", az: "90", phi: "90", s0: "0", s1: "0", step: "50",
  towards: "25", away: "25", top: "0", bottom: "0", corridor: "25", polyline: "", template: "", shadow: "prev",
};

/** The form of a stored series (the window of its parameters). */
export function formFromRow(row: SeriesRow): SeriesForm {
  const p = row.params;
  const p1 = p.p1 ?? [0, 0, 0];
  const s0 = p.s0 ?? 0;
  const s1 = p.s1 ?? s0;
  return {
    ...BLANK,
    type: p.type,
    name: row.name,
    dir: p.p2 ? "p2" : "az",
    p1x: fmt(p1[0]), p1y: fmt(p1[1]), p1z: fmt(p1[2] ?? 0),
    p2x: fmt(p.p2?.[0]), p2y: fmt(p.p2?.[1]),
    az: fmt(p.az ?? 90), phi: fmt(p.phi ?? 90),
    s0: fmt(s0), s1: fmt(s1), step: fmt(p.step ?? 50), towards: fmt(p.towards ?? 25), away: fmt(p.away ?? 25),
    top: fmt((p1[2] ?? 0) - s0), bottom: fmt((p1[2] ?? 0) - s1),
    corridor: fmt(p.corridor ?? 25),
    polyline: (p.polyline ?? []).map((q) => `${fmt(q[0])}; ${fmt(q[1])}`).join("\n"),
    template: p.template ?? "", shadow: p.shadow ?? "prev",
  };
}

/** «Из линии разреза», «Через скважину»: a vertical series of one section on the line (s0 = s1 = 0). */
export function formFromLine(line: SectionLine, name = ""): SeriesForm {
  return { ...BLANK, name, dir: "az", p1x: fmt(line.x0), p1y: fmt(line.y0), az: fmt(line.az), towards: fmt(line.towards), away: fmt(line.away), step: fmt(line.towards + line.away) };
}

/** «Серия из одного разреза»: the section k of the active series as a series of its own (s0 = s1 = 0 at O_k). */
export function formFromSection(cur: CurrentSection): SeriesForm {
  const f = formFromRow(cur.row);
  const name = `${cur.row.name} — ${cur.name ?? `разрез ${cur.index + 1}`}`;
  if (cur.geom.kind === "polyline") return { ...f, name };
  const o = cur.plane.origin;
  return { ...f, name, p1x: fmt(o[0]), p1y: fmt(o[1]), p1z: fmt(o[2]), top: fmt(o[2]), bottom: fmt(o[2]), s0: "0", s1: "0", towards: fmt(cur.plane.towards), away: fmt(cur.plane.away) };
}

/**
 * «Новая серия»: the free line gives P1 and A, otherwise the centre of the data and A = 90°; s0, s1 (a plan — the
 * levels) over the visible data, multiples of Δ, K ≤ 5 000.
 */
export function newForm(line: SectionLine | null, corners: readonly Vec3[]): SeriesForm {
  const f: SeriesForm = line ? formFromLine(line) : { ...BLANK };
  if (!line && corners.length) {
    const c = centre(corners);
    f.p1x = fmt(Math.round(c[0]));
    f.p1y = fmt(Math.round(c[1]));
  }
  return { ...f, ...rangeOver(f, corners) };
}

function centre(corners: readonly Vec3[]): Vec3 {
  const lo: Vec3 = [Infinity, Infinity, Infinity];
  const hi: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const p of corners) for (let i = 0; i < 3; i++) (lo[i] = Math.min(lo[i], p[i])), (hi[i] = Math.max(hi[i], p[i]));
  return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
}

/** «По видимым данным»: s0, s1 (a plan — the top and the bottom) over the data along v̂ of the form; nothing for a polyline. */
export function rangeOver(f: SeriesForm, corners: readonly Vec3[]): Partial<SeriesForm> {
  if (f.type === "polyline" || !corners.length) return {};
  const step = parseNumber(f.step);
  const x = parseNumber(f.p1x) ?? 0;
  const y = parseNumber(f.p1y) ?? 0;
  if (step === null || !(step > 0)) return {};
  if (f.type === "plan") {
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of corners) (lo = Math.min(lo, p[2])), (hi = Math.max(hi, p[2]));
    const top = Math.ceil(hi / step - 1e-9) * step;
    const r = seriesRange(corners, [x, y, top], [0, 0, -1], step);
    return { top: fmt(top), bottom: fmt(top - r.s1), step: fmt(r.step) };
  }
  const az = f.dir === "p2" ? azimuthOf(f) : parseNumber(f.az);
  const phi = f.type === "inclined" ? (parseNumber(f.phi) ?? 90) : 90;
  if (az === null) return {};
  const z = f.type === "inclined" ? (parseNumber(f.p1z) ?? 0) : 0;
  const r = seriesRange(corners, [x, y, z], seriesAxes(((az % 360) + 360) % 360, phi).v, step);
  return { s0: fmt(r.s0), s1: fmt(r.s1), step: fmt(r.step) };
}

function azimuthOf(f: SeriesForm): number | null {
  const [x1, y1, x2, y2] = [f.p1x, f.p1y, f.p2x, f.p2y].map(parseNumber);
  if (x1 === null || y1 === null || x2 === null || y2 === null || Math.hypot(x2 - x1, y2 - y1) <= 1e-6) return null;
  const a = (Math.atan2(x2 - x1, y2 - y1) * 180) / Math.PI;
  return a < 0 ? a + 360 : a;
}

// ---------------------------------------------------------------- reading the form

export type ReadResult = { params: SeriesParamsIn; name: string | null } | { error: string; field: FieldId };

class FormError extends Error {
  constructor(readonly field: FieldId, message: string) {
    super(message);
  }
}

function num(f: SeriesForm, id: FieldId, opts: { positive?: boolean; min?: number; max?: number } = {}): number {
  const v = parseNumber(String(f[id as keyof SeriesForm] ?? ""));
  const label = FIELD_LABELS[id];
  if (v === null) throw new FormError(id, `«${label}»: введите число (десятичный знак — запятая или точка)`);
  if (opts.positive && !(v > 0)) throw new FormError(id, `«${label}»: число должно быть больше 0`);
  if (opts.min !== undefined && v < opts.min) throw new FormError(id, `«${label}»: не меньше ${fmt(opts.min)}`);
  if (opts.max !== undefined && v > opts.max) throw new FormError(id, `«${label}»: не больше ${fmt(opts.max)}`);
  return v;
}

/** «X; Y» or «X Y» a line; the decimals — a comma or a dot (with «;» between the numbers a comma is a decimal). */
export function parseVertices(text: string): [number, number][] | string {
  const out: [number, number][] = [];
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    const parts = lines[i].includes(";") ? lines[i].split(";") : lines[i].split(/[\s\t]+/);
    const xy = parts.map((p) => p.trim()).filter(Boolean).map(parseNumber);
    if (xy.length < 2 || xy.length > 3 || xy.some((v) => v === null)) return `Строка ${i + 1}: ожидаются X и Y вершины, например «1000; 5000»`;
    out.push([xy[0]!, xy[1]!]);
  }
  if (out.length < 2) return "Ломаная: укажите не меньше двух вершин, по одной в строке";
  if (out.length > 1000) return "Ломаная: не больше 1 000 вершин";
  return out;
}

/** The form checked and turned into the parameters for `POST` / `PATCH`, or the first error and its field. */
export function readForm(f: SeriesForm): ReadResult {
  try {
    const base = { type: f.type, template: f.template.trim() || null, shadow: f.shadow };
    const name = f.name.trim() || null;
    if (f.type === "polyline") {
      const pts = parseVertices(f.polyline);
      if (typeof pts === "string") throw new FormError("polyline", pts);
      const params: SeriesParamsIn = { ...base, polyline: pts, corridor: num(f, "corridor", { positive: true }), p1: null, p2: null, az: null, phi: null, s0: 0, s1: 0 };
      return { params, name };
    }
    const towards = num(f, "towards", { positive: true });
    const away = num(f, "away", { positive: true });
    const step = num(f, "step", { positive: true });
    const x = num(f, "p1x");
    const y = num(f, "p1y");
    let p1: number[];
    let s0: number;
    let s1: number;
    if (f.type === "plan") {
      const top = num(f, "top");
      const bottom = num(f, "bottom");
      if (bottom > top) throw new FormError("bottom", "«Нижняя отметка, м»: не выше верхней");
      p1 = [x, y, top];
      s0 = 0;
      s1 = top - bottom;
    } else {
      p1 = [x, y, f.type === "inclined" ? num(f, "p1z") : 0];
      s0 = num(f, "s0");
      s1 = num(f, "s1");
      if (s1 < s0) throw new FormError("s1", "«Конец s1, м»: не меньше начала s0");
    }
    const k = seriesCount(s0, s1, step);
    if (k > MAX_K) throw new FormError("step", `В серии получается ${k} разрезов — больше 5 000: увеличьте шаг или сократите пределы`);
    const dir: Pick<SeriesParamsIn, "p2" | "az"> = { p2: null, az: null };
    if (f.type !== "plan" && f.dir === "p2") {
      const p2 = [num(f, "p2x"), num(f, "p2y")];
      if (Math.hypot(p2[0] - x, p2[1] - y) <= 1e-6) throw new FormError("p2x", "Точки P1 и P2 совпадают в плане: укажите вторую точку в стороне от P1");
      dir.p2 = p2;
    } else dir.az = f.type === "plan" ? 90 : num(f, "az");
    const phi = f.type === "inclined" ? num(f, "phi", { min: 0, max: 90 }) : f.type === "plan" ? null : 90;
    const params: SeriesParamsIn = { ...base, p1, ...dir, phi, s0, s1, step, towards, away, polyline: null, corridor: null };
    return { params, name };
  } catch (e) {
    if (e instanceof FormError) return { error: e.message, field: e.field };
    throw e;
  }
}

/** The field of the form a parameter of the core names (`field` of a 422 `bad_series`). */
export function formField(coreField: string | null, type: SeriesKind): FieldId | null {
  if (!coreField) return null;
  if (type === "plan" && coreField === "p1") return "top";
  if (type === "plan" && coreField === "s1") return "bottom";
  const map: Record<string, FieldId> = { p1: "p1x", p2: "p2x", az: "az", phi: "phi", s0: "s0", s1: "s1", step: "step", towards: "towards", away: "away", template: "template", polyline: "polyline", corridor: "corridor", shadow: "shadow", type: "type" };
  return map[coreField] ?? null;
}

// ---------------------------------------------------------------- saving

export type SaveResult =
  | { ok: true; row: SeriesRow }
  | { ok: false; error: string; hint: string; field: FieldId | null }
  /** 409, «Взять текущие»: the form is filled with the series as it is now */
  | { ok: false; reload: SeriesRow }
  | { ok: false; cancelled: true };

export type SaveTarget = { mode: "create"; folder?: string | null } | { mode: "edit"; id: number; version: number };

/**
 * Creates or changes a series. A 422 names its field; a 409 asks in an own window (`lines-choice`): write my
 * changes over the new version, take the current parameters, or cancel.
 */
export async function saveSeries(target: SaveTarget, f: SeriesForm): Promise<SaveResult> {
  const read = readForm(f);
  if ("error" in read) return { ok: false, error: read.error, hint: "", field: read.field };
  let version = target.mode === "edit" ? target.version : 0;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const row =
        target.mode === "create"
          ? await seriesApi.create(read.params, read.name, target.folder)
          : await seriesApi.patch(target.id, version, { params: read.params, ...(read.name ? { name: read.name } : {}) });
      return { ok: true, row };
    } catch (e) {
      const fault = seriesFault(e);
      if (fault.status !== 409 || !fault.current || target.mode !== "edit") return { ok: false, error: fault.message, hint: fault.hint, field: formField(fault.field, f.type) };
      const answer = await askChoice("Серия изменена", `Серию «${fault.current.name}» только что изменили в другом окне (версия ${fault.current.version}). Записать ваши параметры поверх или взять текущие?`, [
        { id: "mine", label: "Записать мои", hint: "Ваши параметры заменят изменения из другого окна", primary: true },
        { id: "theirs", label: "Взять текущие", hint: "Окно покажет параметры серии, как они сейчас" },
        { id: CANCEL, label: "Отмена" },
      ]);
      if (answer === "theirs") return { ok: false, reload: fault.current };
      if (answer !== "mine") return { ok: false, cancelled: true };
      version = fault.current.version;
    }
  }
  return { ok: false, error: "Серию продолжают менять в другом окне.", hint: "Закройте другие окна серии и повторите.", field: null };
}

// ---------------------------------------------------------------- own windows of sections

/**
 * `overrides` after a change of the window of section k (k from 0): own «к себе» / «от себя», or null — the
 * window of the series again. A value equal to the series window is not kept.
 */
export function overridesWith(meta: Pick<SeriesMeta, "overrides" | "towards" | "away">, k: number, win: { towards: number; away: number } | null): Record<string, { towards?: number; away?: number }> {
  const out: Record<string, { towards?: number; away?: number }> = {};
  for (const [key, w] of Object.entries(meta.overrides ?? {})) out[key] = { ...(w.towards != null ? { towards: w.towards } : {}), ...(w.away != null ? { away: w.away } : {}) };
  delete out[String(k)];
  if (win) {
    const own: { towards?: number; away?: number } = {};
    if (win.towards !== (meta.towards ?? 25)) own.towards = win.towards;
    if (win.away !== (meta.away ?? 25)) own.away = win.away;
    if (Object.keys(own).length) out[String(k)] = own;
  }
  return out;
}

/** The own window of a row of the table from its two fields, or the error. */
export function readWindow(towards: string, away: string): { towards: number; away: number } | { error: string } {
  const t = parseNumber(towards);
  const a = parseNumber(away);
  if (t === null || a === null) return { error: "Окно разреза: введите числа (десятичный знак — запятая или точка)" };
  if (!(t > 0) || !(a > 0)) return { error: "Окно разреза: числа больше 0" };
  return { towards: t, away: a };
}
