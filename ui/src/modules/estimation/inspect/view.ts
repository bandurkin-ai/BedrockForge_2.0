/**
 * What the window «Блок · i, j, k» shows, as pure functions (ТЗ 7.35 Д-30): the title, the table of the probes (sort, filter by
 * the reason), the colours of the weights (Cobalt for a positive weight, Copper Orange for a negative one, Slate for an excluded
 * probe — docs/brand), the values of the row, the text of «Скопировать снимок». A gap is «—», never 0 (8.0.1). No React, no three.js.
 */
import { fieldNumber, num } from "../../../shell/props";
import type { Candidate, FieldCompare, InspectAnswer, InspectResult, Triple } from "./api";
/** The brand colours of the layer and of the legend of the window (docs/brand/README.md). */
export const COLORS = {
  positive: "#1f6bff",
  positiveLight: "#a9c4ff",
  negative: "#f46a1a",
  negativeLight: "#f9b48a",
  excluded: "#8a99a8",
  pick: "#e6e9ee",
} as const;

export const dash = "—";

export const numText = (v: number | null | undefined, digits = 3): string => (v === null || v === undefined || !Number.isFinite(v) ? dash : fieldNumber(v, digits));

/** The title of the window: «Блок · 13, 7, 1» (the cell of the parent, from 0). */
export function blockTitle(a: Pick<InspectAnswer, "block"> | null): string {
  return a ? `Блок · ${a.block.ijk.join(", ")}` : "Блок · i, j, k";
}

const AXES = ["X", "Y", "Z"] as const;
export const pointText = (p: Triple, digits = 2): string => p.map((v, i) => `${AXES[i]} ${fieldNumber(v, digits)}`).join(", ");

// ---------------------------------------------------------------------------------------------------- the reasons

/** The codes of the reasons (the core's `search`): `selected` — taken into the system, the rest — why a candidate is not. */
export const SELECTED = "selected";
export const OUTSIDE = "outside";

/** The numbers of the probes the core drew a segment to (the ones taken into the system: `selected` and `hy_capped`). */
export const takenSet = (r: Pick<InspectResult, "segments">): ReadonlySet<number> => new Set(r.segments.map((s) => s.i));

/**
 * A probe that took part in the estimation, as the core counts it: it has a weight λ or a segment (`selected` and `hy_capped` — the one cut
 * to t is taken too, with its λ; `preview.ts` takes it the same way). Not `reason === "selected"`: a probe cut to t is not «selected» by the code.
 */
export const isSelected = (c: Pick<Candidate, "i" | "lambda">, taken?: ReadonlySet<number>): boolean => c.lambda !== null || (taken?.has(c.i) ?? false);

/** The system of the row was solved (STATUS 0 or the status is not known): the weights and the segments are the core's. */
export const solved = (r: Pick<InspectResult, "status">): boolean => r.status === null || r.status === 0;

/** «система не решалась (статус 2 — мало проб)» for the row whose system was not solved (STATUS 2, 3), else null. */
export function unsolvedNote(r: Pick<InspectResult, "status" | "status_label">): string | null {
  return solved(r) ? null : `система не решалась (статус ${r.status}${r.status_label ? ` — ${r.status_label}` : ""})`;
}

/** The mode of the sub-blocks of the row, only if it is not the default (`parent`); null for the default. */
export const subText = (sub: string | null | undefined): string | null => (sub === "own" ? "каждый субблок оценивается сам, со своей дискретизацией" : sub && sub !== "parent" ? `режим субблоков: ${sub}` : null);

export interface ReasonOption {
  code: string;
  label: string;
  count: number;
}

/** The reasons present among the candidates with their number, «выбрана» first, then by the number. */
export function reasonOptions(cands: readonly Candidate[]): ReasonOption[] {
  const by = new Map<string, ReasonOption>();
  for (const c of cands) {
    const o = by.get(c.reason) ?? { code: c.reason, label: c.reason_label || c.reason, count: 0 };
    o.count++;
    by.set(c.reason, o);
  }
  return [...by.values()].sort((a, b) => (a.code === SELECTED ? -1 : b.code === SELECTED ? 1 : b.count - a.count || a.label.localeCompare(b.label, "ru")));
}

// ---------------------------------------------------------------------------------------------------- sort and filter

export type SortKey = "i" | "hole" | "from" | "h" | "dist" | "octant" | "z_raw" | "z" | "lambda" | "reason";
export type SortDir = "asc" | "desc";
export interface Sort {
  key: SortKey;
  dir: SortDir;
}

/** The default order: the selected first, then by the distance (the order the system was built in). */
export const DEFAULT_SORT: Sort | null = null;

export const COLUMNS: { key: SortKey; title: string; numeric: boolean; hint: string }[] = [
  { key: "hole", title: "Скважина", numeric: false, hint: "Скважина пробы" },
  { key: "from", title: "От–до, м", numeric: true, hint: "Интервал пробы вдоль скважины" },
  { key: "h", title: "h_s", numeric: true, hint: "Расстояние в метрике эллипсоида последнего прохода: 1 — на его границе" },
  { key: "dist", title: "Расстояние, м", numeric: true, hint: "Евклидово расстояние от центра блока до пробы" },
  { key: "octant", title: "Октант", numeric: true, hint: "Октант пробы относительно центра блока (0–7)" },
  { key: "z_raw", title: "Значение", numeric: true, hint: "Значение пробы как в таблице" },
  { key: "z", title: "Использовано", numeric: true, hint: "Значение после урезки или преобразования — то, что вошло в систему" },
  { key: "lambda", title: "Вес λ", numeric: true, hint: "Вес пробы в оценке блока; отрицательные веса — в красно-оранжевом цвете" },
  { key: "reason", title: "Причина", numeric: false, hint: "Почему проба выбрана или отброшена" },
];

const keyed = (c: Candidate, k: SortKey): number | string | null => (k === "hole" ? c.hole : k === "reason" ? c.reason_label : k === "from" ? c.from : (c[k] as number | null));

/** A copy of the candidates: only the reason asked (null — all), in the order asked; a gap goes last in either direction. */
export function shownCandidates(cands: readonly Candidate[], reason: string | null, sort: Sort | null): Candidate[] {
  const out = reason === null ? [...cands] : cands.filter((c) => c.reason === reason);
  if (!sort) return out;
  const sign = sort.dir === "asc" ? 1 : -1;
  return out.sort((a, b) => {
    const x = keyed(a, sort.key);
    const y = keyed(b, sort.key);
    if (x === null && y === null) return a.i - b.i;
    if (x === null) return 1;
    if (y === null) return -1;
    const d = typeof x === "string" || typeof y === "string" ? String(x).localeCompare(String(y), "ru", { numeric: true }) : x - y;
    return d !== 0 ? sign * d : a.i - b.i;
  });
}

/** A click on a header: the same column — the other direction, then the order off; another column — ascending. */
export function nextSort(cur: Sort | null, key: SortKey): Sort | null {
  if (!cur || cur.key !== key) return { key, dir: "asc" };
  return cur.dir === "asc" ? { key, dir: "desc" } : null;
}

export const sortMark = (cur: Sort | null, key: SortKey): "ascending" | "descending" | "none" => (cur?.key === key ? (cur.dir === "asc" ? "ascending" : "descending") : "none");

// ---------------------------------------------------------------------------------------------------- the weights in colour

/** The largest |λ| of the probes taken: the scale of the colours of the weights. */
export function maxWeight(cands: readonly Pick<Candidate, "lambda">[]): number {
  let m = 0;
  for (const c of cands) if (c.lambda !== null && Math.abs(c.lambda) > m) m = Math.abs(c.lambda);
  return m;
}

const hex = (s: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as [number, number, number];
const mix = (a: [number, number, number], b: [number, number, number], t: number): [number, number, number] => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

/** The colour of a weight, sRGB 0–1: the positive ones light blue → Cobalt, the negative ones light → Copper Orange by |λ| / max. No weight — Slate. */
export function weightColor(lambda: number | null, max: number): [number, number, number] {
  if (lambda === null || !Number.isFinite(lambda)) return hex(COLORS.excluded);
  const t = max > 0 ? Math.min(1, Math.abs(lambda) / max) : 1;
  return lambda < 0 ? mix(hex(COLORS.negativeLight), hex(COLORS.negative), t) : mix(hex(COLORS.positiveLight), hex(COLORS.positive), t);
}

export const cssColor = (c: readonly number[]): string => `rgb(${c.map((x) => Math.round(x * 255)).join(", ")})`;

/** The colour of the weight in the table: «positive», «negative» or none (the class of the cell). */
export const weightClass = (lambda: number | null): "negative" | "positive" | null => (lambda === null ? null : lambda < 0 ? "negative" : "positive");

// ---------------------------------------------------------------------------------------------------- the row

/** The values of the estimation of the row in the order of the ТЗ 8.8.2 (those the core gave). */
export const VALUE_ORDER = ["STATUS", "PASS", "EST", "KV", "SLOPE", "KE", "NS", "NH", "NHY", "NOCT", "AD", "DMIN", "LAGR", "SNW"] as const;

export const VALUE_TITLES: Record<string, string> = {
  STATUS: "STATUS — статус блока",
  PASS: "PASS — проход",
  EST: "EST — оценка",
  KV: "KV — дисперсия кригинга",
  SLOPE: "SLOPE — наклон регрессии",
  KE: "KE — эффективность кригинга",
  NS: "NS — проб в системе",
  NH: "NH — скважин",
  NHY: "NHY — затронуто ураганных",
  NOCT: "NOCT — занято октантов",
  AD: "AD — среднее расстояние, м",
  DMIN: "DMIN — ближайшая проба, м",
  LAGR: "LAGR — множитель Лагранжа",
  SNW: "SNW — сумма отрицательных весов",
};

export interface ValueRow {
  key: string;
  title: string;
  text: string;
}

export function valueRows(r: Pick<InspectResult, "values" | "status_label">): ValueRow[] {
  const keys = [...VALUE_ORDER.filter((k) => k in r.values), ...Object.keys(r.values).filter((k) => !(VALUE_ORDER as readonly string[]).includes(k))];
  return keys.map((key) => {
    const v = r.values[key];
    const text = key === "STATUS" && v !== null ? `${numText(v, 0)} — ${r.status_label}` : numText(v, 6);
    return { key, title: VALUE_TITLES[key] ?? key, text };
  });
}

/** «записано 1,16882594 · пересчёт 1,16882594»; «—» for a gap. */
export function compareText(f: FieldCompare): { stored: string; calc: string; mark: string } {
  const mark = f.same === null ? dash : f.same ? "совпало" : "не совпало";
  return { stored: numText(f.stored, 9), calc: numText(f.calc, 9), mark };
}

/** The label of a tab: the name of the row and, if the block is not estimated by it, why. */
export function tabLabel(r: Pick<InspectResult, "name" | "active" | "status" | "status_label">): string {
  return r.active ? r.name : `${r.name} (${r.status_label || "блок вне домена"})`;
}

/** The weights of the probes taken: the sum (it is 1 for OK and the like; SK and IDW differ) and the sum of the negative ones. */
export function weightSums(cands: readonly Pick<Candidate, "lambda">[]): { sum: number | null; negative: number } {
  let sum = 0;
  let n = 0;
  let neg = 0;
  for (const c of cands) {
    if (c.lambda === null) continue;
    sum += c.lambda;
    n++;
    if (c.lambda < 0) neg += c.lambda;
  }
  return { sum: n ? sum : null, negative: neg };
}

/** A short line under the table: «Кандидатов 121, выбрано 9, показано 121». */
export function countsText(r: Pick<InspectResult, "counts">, shown: number): string {
  const c = r.counts;
  return `Кандидатов ${num(c.candidates, 0)}, выбрано ${num(c.selected, 0)}${shown !== c.candidates ? `, в таблице ${num(shown, 0)}` : ""}${c.shown < c.candidates ? ` (ядро показало первые ${num(c.shown, 0)})` : ""}`;
}

/** The interval of a probe: «20 – 22,5». */
export function intervalText(c: Pick<Candidate, "from" | "to">): string {
  return c.from === null || c.to === null ? dash : `${fieldNumber(c.from, 3)} – ${fieldNumber(c.to, 3)}`;
}

// ---------------------------------------------------------------------------------------------------- «Скопировать снимок»

/** The snapshot of a block for the assistant and for the remarks: the block, the parameters, the probes and the weights of the row. */
export function snapshotText(a: InspectAnswer, r: InspectResult): string {
  const body = { model_id: a.model_id, model_version: a.model_version, block: a.block, estimation: { id: r.estimation_id, name: r.name, method: r.method, changed: r.changed, warning: r.warning }, snapshot: r.snapshot };
  return JSON.stringify(body, null, 2);
}
