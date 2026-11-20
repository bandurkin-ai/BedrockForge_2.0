/**
 * Ручная правка рудных интервалов (Д-26 «Место»; ADR 0018 §12, решение 24 — исключение из «только чтения»): in the
 * window of a result «Рудные интервалы» — split, merge, move a boundary by the boundaries of the samples of one
 * hole; the core checks К, Мпп and the прирезка again, names the violations, marks the row MANUAL = 1 and keeps the
 * edit in `report.edits` («Перестроить» loses them — its own warning). Here: the intervals of a hole from the
 * protocol answer (`GET /composites/results/{id}/protocol?hole=`), the boundaries an edit may take, the body of
 * `POST /composites/results/{id}/edit {hole, action, from, to}`, its answer made safe. Pure except `sendEdit`.
 *
 * `from` / `to` by action (sample boundaries, metres along the hole):
 * - `split` — `from` the FROM of the interval, `to` the depth of the cut (FROM < to < TO) → [FROM; to], [to; TO];
 * - `merge` — `from` the FROM of the upper interval, `to` the TO of the lower one → one interval [from; to];
 * - `move` — `from` the boundary as it is (FROM or TO of an interval), `to` the new depth.
 */
import { oreClass, type OreClass } from "../../../charts/column/classes";
import { api } from "../../../shell/api";
import type { IntervalColumns } from "../api";
import { num, type OreAnswer } from "./ore";

export type EditAction = "split" | "merge" | "move";

export const ACTION_TITLES: Readonly<Record<EditAction, string>> = { split: "Разделить", merge: "Объединить", move: "Сдвинуть границу" };

/** A row of the hole a user may edit: `ore` or `subgrade` of the top level (NESTED = 0). */
export interface EditInterval {
  /** the row in `answer.result` */
  index: number;
  from: number;
  to: number;
  cls: OreClass;
  m: number | null;
  c: number | null;
  mc: number | null;
  label: string;
  manual: boolean;
}

const fin = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const truthy = (v: unknown): boolean => v === true || v === 1 || v === "1";

/** The editable intervals of a hole, top down. */
export function editIntervals(result: IntervalColumns): EditInterval[] {
  const v = result.values;
  const out: EditInterval[] = [];
  for (let i = 0; i < result.from.length; i++) {
    const from = fin(result.from[i]);
    const to = fin(result.to[i]);
    const cls = oreClass(v.CLASS?.[i]);
    if (from === null || to === null || !(to > from) || (cls !== "ore" && cls !== "subgrade") || truthy(v.NESTED?.[i])) continue;
    out.push({ index: i, from, to, cls, m: fin(v.M?.[i]), c: fin(v.C?.[i]), mc: fin(v.MC?.[i]), label: typeof v.LABEL?.[i] === "string" ? (v.LABEL[i] as string) : "", manual: truthy(v.MANUAL?.[i]) });
  }
  return out.sort((a, b) => a.from - b.from);
}

const EPS = 1e-6;

/** The boundaries of the samples of the hole (FROM and TO), ascending, those closer than 1e−6 м as one. */
export function sampleBounds(samples: IntervalColumns): number[] {
  const all = [...samples.from, ...samples.to].filter((x): x is number => fin(x) !== null).sort((a, b) => a - b);
  const out: number[] = [];
  for (const x of all) if (!out.length || x - out[out.length - 1] > EPS) out.push(x);
  return out;
}

/** Where an interval may be cut: the sample boundaries strictly inside it. */
export const splitPoints = (iv: EditInterval, bounds: readonly number[]): number[] => bounds.filter((b) => b > iv.from + EPS && b < iv.to - EPS);

export type Side = "top" | "bottom";

/**
 * Where a boundary of interval `i` may go: the sample boundaries between the neighbours (never into another
 * interval, never past its own other boundary), the boundary itself left out.
 */
export function moveTargets(list: readonly EditInterval[], i: number, side: Side, bounds: readonly number[]): number[] {
  const iv = list[i];
  if (!iv) return [];
  if (side === "top") {
    const lo = i > 0 ? list[i - 1].to : -Infinity;
    return bounds.filter((b) => b >= lo - EPS && b < iv.to - EPS && Math.abs(b - iv.from) > EPS);
  }
  const hi = i + 1 < list.length ? list[i + 1].from : Infinity;
  return bounds.filter((b) => b > iv.from + EPS && b <= hi + EPS && Math.abs(b - iv.to) > EPS);
}

export interface EditBody {
  hole: string;
  action: EditAction;
  from: number;
  to: number;
}

/** The body of an edit, or why it cannot be sent (Russian). */
export function editBody(hole: string, action: EditAction, list: readonly EditInterval[], i: number, opts: { at?: number | null; side?: Side; target?: number | null }, bounds: readonly number[]): EditBody | string {
  const iv = list[i];
  if (!hole.trim()) return "Выберите скважину";
  if (!iv) return "Выберите интервал";
  if (action === "split") {
    const at = opts.at ?? null;
    if (at === null) return "Выберите границу пробы, по которой разделить интервал";
    if (!splitPoints(iv, bounds).some((b) => Math.abs(b - at) <= EPS)) return "Разделить можно только по границе пробы внутри интервала";
    return { hole: hole.trim(), action, from: iv.from, to: at };
  }
  if (action === "merge") {
    const next = list[i + 1];
    if (!next) return "Ниже нет интервала, с которым объединить";
    return { hole: hole.trim(), action, from: iv.from, to: next.to };
  }
  const side = opts.side ?? "top";
  const target = opts.target ?? null;
  if (target === null) return "Выберите новую глубину границы";
  if (!moveTargets(list, i, side, bounds).some((b) => Math.abs(b - target) <= EPS)) return "Граница сдвигается только на границу пробы между соседними интервалами";
  return { hole: hole.trim(), action, from: side === "top" ? iv.from : iv.to, to: target };
}

/** «Разделить 3–11 м по 7 м», «Объединить 0–4 и 6–8 м», «Сдвинуть кровлю 3 → 2 м». */
export function editText(b: EditBody): string {
  if (b.action === "split") return `Разделить интервал от ${num(b.from, 2)} м по ${num(b.to, 2)} м`;
  if (b.action === "merge") return `Объединить интервалы ${num(b.from, 2)}–${num(b.to, 2)} м`;
  return `Сдвинуть границу ${num(b.from, 2)} → ${num(b.to, 2)} м`;
}

// ---- the answer ----

export interface Violation {
  text: string;
  from: number | null;
  to: number | null;
}

export interface EditAnswer {
  /** the rows of «Рудные интервалы» of the hole after the edit; null — not sent (read the protocol again) */
  rows: IntervalColumns | null;
  violations: Violation[];
  /** manual edits of the result after this one */
  edits: number;
}

function violation(raw: unknown): Violation | null {
  if (typeof raw === "string") return raw.trim() ? { text: raw.trim(), from: null, to: null } : null;
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const text = [r.message, r.hint].filter((x): x is string => typeof x === "string" && x.trim() !== "").join(" ") || (typeof r.code === "string" ? r.code : "");
  return text ? { text, from: fin(r.from), to: fin(r.to) } : null;
}

const isColumns = (v: unknown): v is IntervalColumns => !!v && typeof v === "object" && Array.isArray((v as IntervalColumns).from) && Array.isArray((v as IntervalColumns).to);

export function toEditAnswer(raw: Record<string, unknown>): EditAnswer {
  const rep = raw.report && typeof raw.report === "object" ? (raw.report as Record<string, unknown>) : {};
  const rows = isColumns(raw.rows) ? { from: raw.rows.from, to: raw.rows.to, values: raw.rows.values && typeof raw.rows.values === "object" ? raw.rows.values : {} } : null;
  return {
    rows,
    violations: (Array.isArray(raw.violations) ? raw.violations : []).map(violation).filter((v): v is Violation => v !== null),
    edits: Array.isArray(rep.edits) ? rep.edits.length : typeof rep.edits === "number" ? rep.edits : 0,
  };
}

/** «3–11 м: …» for a violation tied to depths. */
export const violationText = (v: Violation): string => (v.from !== null && v.to !== null ? `${num(v.from, 2)}–${num(v.to, 2)} м: ${v.text}` : v.text);

export async function sendEdit(resultId: number, body: EditBody): Promise<EditAnswer> {
  return toEditAnswer(await api.post<Record<string, unknown>>(`/composites/results/${resultId}/edit`, body));
}

/** The answer of the protocol with the rows of the edit put in (until the protocol is read again). */
export const withRows = (a: OreAnswer, rows: IntervalColumns | null): OreAnswer => (rows ? { ...a, result: rows } : a);
