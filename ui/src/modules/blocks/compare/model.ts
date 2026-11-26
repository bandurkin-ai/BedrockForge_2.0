/**
 * The form of the window «Сравнить оценки» and the words of its result (ТЗ 7.35 Д-37; ADR 0027 решение 25): the fields A and B and the domain a model offers, the default
 * pair (the fields of an estimate `*_EST`), the body of the request and the arguments of the command from the form, the text of a finished run for the log and for the
 * assistant, the bars of the histogram of Δ, the range of the colour scale in 3D (symmetric around 0). A number the core could not make is `null` — «—» in a table,
 * never 0. Pure.
 */
import type { BlockModelInfo } from "../api";
import { fmt, fmtCount } from "../report/format";
import type { CompareBody, DeltaStats, DomainRow, EstimateCompare } from "./api";
import type { CompareForm } from "./state";

export const GAP = "—";

/** A number for a table cell: a gap is «—». */
export const cell = (v: number | null | undefined): string => (v === null || v === undefined || !Number.isFinite(v) ? GAP : fmt(v));

/** A difference with its sign: «+0,12», «−0,25», «0»; a gap — «—». */
export function signed(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return GAP;
  if (v === 0) return "0";
  return v > 0 ? `+${fmt(v)}` : fmt(v);
}

type Fields = Pick<BlockModelInfo, "fields"> | undefined | null;

/** The numeric fields of the model: what A and B are chosen from. */
export const numberFields = (m: Fields): string[] => (m?.fields ?? []).filter((f) => f.kind === "number").map((f) => f.name);

/** The category fields of the model: what the domain is chosen from. */
export const categoryFields = (m: Fields): string[] => (m?.fields ?? []).filter((f) => f.kind === "category").map((f) => f.name);

/** The pair by default: the first two fields of an estimate (`*_EST`), else the first two numeric ones; fewer than two — what there is. */
export function defaultPair(m: Fields): { a: string; b: string } {
  const nums = numberFields(m);
  const est = nums.filter((n) => /_est$/i.test(n));
  const pool = est.length >= 2 ? est : nums;
  return { a: pool[0] ?? "", b: pool[1] ?? "" };
}

/** The name of the field Δ the core gives by default. */
export const defaultOut = (a: string, b: string): string => (a && b ? `${a}_${b}_D` : "");

/** The form of a model: what depends on its fields is kept only if the model has it; a model without a pair gets the default one. */
export function formForModel(id: number | null, m: Fields, keep: CompareForm): CompareForm {
  const same = keep.modelId === id;
  const nums = numberFields(m);
  const cats = categoryFields(m);
  const fresh = defaultPair(m);
  const a = same && nums.includes(keep.a) ? keep.a : fresh.a;
  const b = same && nums.includes(keep.b) ? keep.b : fresh.b;
  return { modelId: id, a, b, domain: same && cats.includes(keep.domain) ? keep.domain : "", out: same ? keep.out : "" };
}

export interface Issue {
  field: "model" | "a" | "b" | "out" | "general";
  message: string;
  hint: string;
}

/** The body of `POST /blocks/{id}/compare` from the form; the first wrong thing is told in Russian with a hint. */
export function bodyOfForm(f: CompareForm): { ok: true; modelId: number; body: CompareBody } | { ok: false; issue: Issue } {
  if (f.modelId === null) return { ok: false, issue: { field: "model", message: "Не выбрана блочная модель.", hint: "Выберите модель, в которой лежат обе оценки." } };
  if (!f.a) return { ok: false, issue: { field: "a", message: "Не выбрано поле A.", hint: "Выберите числовое поле оценки, например AU_OK_EST." } };
  if (!f.b) return { ok: false, issue: { field: "b", message: "Не выбрано поле B.", hint: "Выберите второе числовое поле оценки, например AU_ID_EST." } };
  const body: CompareBody = { field_a: f.a, field_b: f.b };
  if (f.domain.trim()) body.domain = f.domain.trim();
  if (f.out.trim()) body.out = f.out.trim();
  return { ok: true, modelId: f.modelId, body };
}

/** The arguments of the command `estimate.compare` the form makes (the same for the button, Ctrl+K and the assistant). */
export function argsOfForm(f: CompareForm): Record<string, unknown> {
  const a: Record<string, unknown> = { model_id: f.modelId, field_a: f.a, field_b: f.b };
  if (f.domain.trim()) a.domain = f.domain.trim();
  if (f.out.trim()) a.out = f.out.trim();
  return a;
}

/** The form of the arguments of a command call (the window shows what is asked). */
export function formOfArgs(a: Record<string, unknown>, modelId: number, keep: CompareForm): CompareForm {
  const s = (k: string): string => (typeof a[k] === "string" ? (a[k] as string).trim() : "");
  return { ...keep, modelId, a: s("field_a"), b: s("field_b"), domain: s("domain"), out: s("out") };
}

// ---- the result ----

const PERCENT_TITLES: [string, string][] = [
  ["1", "P1"],
  ["5", "P5"],
  ["25", "P25"],
  ["50", "Медиана"],
  ["75", "P75"],
  ["95", "P95"],
  ["99", "P99"],
];

/** The statistics of Δ as rows «название — значение» for the table; a gap is «—». */
export function statRows(s: DeltaStats): [string, string][] {
  return [
    ["Блоков с Δ", fmtCount(s.n)],
    ["Среднее", signed(s.mean)],
    ["СКО", cell(s.std)],
    ["Минимум", signed(s.min)],
    ...PERCENT_TITLES.map(([k, t]): [string, string] => [t, signed(s.percentiles[k] ?? null)]),
    ["Максимум", signed(s.max)],
  ];
}

export interface Bar {
  x0: number;
  x1: number;
  count: number;
}

/** The bars of the histogram of Δ (by the number of blocks): the edges and the counts of the core; a broken pair gives no bars. */
export function bars(h: EstimateCompare["histogram"]): Bar[] {
  if (h.edges.length < 2 || h.counts.length !== h.edges.length - 1) return [];
  return h.counts.map((count, i) => ({ x0: h.edges[i], x1: h.edges[i + 1], count }));
}

/** The range of the colour scale for Δ in 3D: symmetric around 0 (the colour of zero is the middle), from the largest |Δ|; null — Δ is 0 everywhere or has no numbers. */
export function symmetricRange(s: DeltaStats): { from: number; to: number } | null {
  const m = Math.max(Math.abs(s.min ?? 0), Math.abs(s.max ?? 0));
  return m > 0 && Number.isFinite(m) ? { from: -m, to: m } : null;
}

/** One row of the table by the domains as the texts of the cells (a gap — «—»; the blocks without STATUS — «—»). */
export function domainCells(d: DomainRow): string[] {
  return [d.domain || "—", fmtCount(d.blocks), fmtCount(d.missing), cell(d.volume), cell(d.mean_a), cell(d.mean_b), signed(d.mean_delta), d.status_changed === null ? GAP : fmtCount(d.status_changed)];
}

export const DOMAIN_HEAD = ["Домен", "Блоков с Δ", "Пропуск в A или B", "Объём, м³", "Среднее A", "Среднее B", "Среднее Δ = A − B", "Сменился STATUS, блоков"];

/** The text of a finished run for the log and for the assistant. */
export function resultText(r: EstimateCompare, modelName: string): string {
  const lines = [`Сравнение оценок «${r.a}» и «${r.b}» модели «${modelName}»: Δ = A − B${r.field_written ? ` записана в поле ${r.field}` : ` (поле ${r.field} не записано)`}, блоков с Δ ${fmtCount(r.stats.n)}.`];
  lines.push(`Δ: среднее ${signed(r.stats.mean)}, СКО ${cell(r.stats.std)}, от ${signed(r.stats.min)} до ${signed(r.stats.max)}, медиана ${signed(r.stats.percentiles["50"] ?? null)}.`);
  const top = [...r.domains].filter((d) => d.mean_delta !== null).sort((x, y) => Math.abs(y.mean_delta ?? 0) - Math.abs(x.mean_delta ?? 0)).slice(0, 3);
  for (const d of top) lines.push(`Домен «${d.domain}»: среднее A ${cell(d.mean_a)}, B ${cell(d.mean_b)}, Δ ${signed(d.mean_delta)}${d.status_changed !== null ? `, сменился STATUS: ${fmtCount(d.status_changed)} бл.` : ""}.`);
  if (r.status) lines.push(`STATUS (${r.status.a} и ${r.status.b}): сменился у ${fmtCount(r.status.changed)} бл.`);
  for (const w of r.warnings) lines.push(`Предупреждение: ${w}`);
  return lines.join("\n");
}
