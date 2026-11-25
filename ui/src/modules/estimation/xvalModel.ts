/**
 * What the window of the cross-validation shows, as pure functions (ТЗ 8.8.5): the columns and the cells of the table (a gap is
 * «—», never 0), the sort and the page of the table over the rows read, the facts of the summary with what each number means,
 * the data of the scatter plot «истинное — оценка» and of the histogram of the errors in the forms of the charts of Э13
 * (`charts/scatter`, `charts/histogram` draw them), the colour of a point of the errors in 3D (Cobalt — the estimate is below the
 * true value, Light Stone — none, Copper Orange — above). No React.
 */
import { BRAND, mix } from "../../charts/column/classes";
import { formatNumber } from "../../charts/core/axisScale";
import type { Mark } from "../../charts/histogram/model";
import type { Describe, HistogramResult } from "../charts/api";
import type { ScatterResult } from "../charts/apiMore";
import { STATUS_LABELS } from "./api";
import type { XvalBy, XvalHistogram, XvalRow, XvalSummary } from "./xvalApi";

// ---- the table ----

export type SortKey = "i" | "hole" | "from" | "to" | "x" | "y" | "z" | "truth" | "est" | "error" | "kv" | "z_error" | "pass" | "ns" | "status";

export interface Column {
  key: SortKey;
  title: string;
  kind: "text" | "num" | "coord";
  hint: string;
}

export const COLUMNS: readonly Column[] = [
  { key: "i", title: "№", kind: "num", hint: "Порядковый номер пробы в проверке" },
  { key: "hole", title: "Скважина", kind: "text", hint: "Скважина пробы" },
  { key: "from", title: "От, м", kind: "num", hint: "Начало интервала пробы по стволу" },
  { key: "to", title: "До, м", kind: "num", hint: "Конец интервала пробы по стволу" },
  { key: "x", title: "X", kind: "coord", hint: "Положение пробы (координаты проекта)" },
  { key: "y", title: "Y", kind: "coord", hint: "Положение пробы (координаты проекта)" },
  { key: "z", title: "Z", kind: "coord", hint: "Положение пробы (координаты проекта)" },
  { key: "truth", title: "Истинное", kind: "num", hint: "Значение пробы после урезки и усреднения" },
  { key: "est", title: "Оценка", kind: "num", hint: "Оценка в точке пробы без неё (без её скважины — при проверке по скважине)" },
  { key: "error", title: "Ошибка", kind: "num", hint: "Оценка минус истинное: больше нуля — завышение" },
  { key: "kv", title: "KV", kind: "num", hint: "Дисперсия кригинга; для IDW, NN и IK её нет" },
  { key: "z_error", title: "Ошибка/√KV", kind: "num", hint: "Стандартизованная ошибка; пусто, если KV не больше нуля" },
  { key: "pass", title: "Проход", kind: "num", hint: "Проход поиска, на котором оценена проба" },
  { key: "ns", title: "Проб", kind: "num", hint: "Сколько проб вошло в оценку" },
  { key: "status", title: "Статус", kind: "text", hint: "Оценена ли проба; не оценённые в сводку не входят" },
];

export const statusText = (code: number): string => STATUS_LABELS[code] ?? `код ${code}`;

const COORD = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

/** The text of a number of a cell: a gap is «—». */
export function numberText(v: number | null, kind: "num" | "coord" = "num"): string {
  if (v === null || !Number.isFinite(v)) return "—";
  return kind === "coord" ? COORD.format(v) : formatNumber(v, 4);
}

export function cellText(row: XvalRow, key: SortKey): string {
  switch (key) {
    case "hole":
      return row.hole ?? "—";
    case "status":
      return statusText(row.status);
    case "i":
      return String(row.i + 1);
    case "pass":
    case "ns":
      return row[key] === null ? "—" : String(row[key]);
    case "x":
    case "y":
    case "z":
      return numberText(row[key], "coord");
    default:
      return numberText(row[key]);
  }
}

export interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

const NAMES = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

const sortValue = (row: XvalRow, key: SortKey): number | string | null => (key === "i" ? row.i : row[key]);

/** The rows in the order of the sort (stable; a gap is last in either direction); no sort — the order of the core. */
export function sortRows(rows: readonly XvalRow[], sort: Sort | null): XvalRow[] {
  if (!sort) return rows.slice();
  const sign = sort.dir === "asc" ? 1 : -1;
  const keyed = rows.map((r, at) => ({ r, at, v: sortValue(r, sort.key) }));
  keyed.sort((a, b) => {
    const [x, y] = [a.v, b.v];
    if (x === null || y === null) return x === y ? a.at - b.at : x === null ? 1 : -1;
    const d = typeof x === "string" || typeof y === "string" ? NAMES.compare(String(x), String(y)) : x - y;
    return d !== 0 ? d * sign : a.at - b.at;
  });
  return keyed.map((k) => k.r);
}

/** The next sort of a click on a header: the other column — ascending, the same — descending, again — none. */
export function nextSort(cur: Sort | null, key: SortKey): Sort | null {
  if (!cur || cur.key !== key) return { key, dir: "asc" };
  return cur.dir === "asc" ? { key, dir: "desc" } : null;
}

export const TABLE_SIZES: readonly number[] = [50, 100, 500];
export const pagesOf = (n: number, size: number): number => Math.max(1, Math.ceil(n / Math.max(1, size)));
export const pageRows = <T>(rows: readonly T[], page: number, size: number): T[] => rows.slice((page - 1) * size, page * size);

// ---- the summary ----

export interface Fact {
  key: string;
  title: string;
  value: string;
  hint: string;
}

const pct = (n: number, of: number): string => (of > 0 ? ` (${formatNumber((n / of) * 100, 3)} %)` : "");

/** The statuses by the code with the counts: «оценён — 147»; the rows of the zero count are not named. */
export function statusLine(byStatus: readonly number[]): string {
  const parts = byStatus.flatMap((n, code) => (n > 0 ? [`${statusText(code)} — ${n.toLocaleString("ru-RU")}`] : []));
  return parts.length ? parts.join(", ") : "—";
}

export function summaryFacts(s: XvalSummary): Fact[] {
  const skipped = s.n_skipped > 0 ? ` Ещё ${s.n_skipped.toLocaleString("ru-RU")} строк не оценено: они в таблице, но не в сводке.` : "";
  return [
    { key: "n", title: "Проб в сводке", value: s.n.toLocaleString("ru-RU"), hint: `Оценены и вошли в сводку.${skipped}` },
    { key: "mean_error", title: "Средняя ошибка", value: numberText(s.mean_error), hint: "Оценка минус истинное. Хорошо — около нуля; больше нуля — оценка завышает, меньше — занижает" },
    { key: "std_error", title: "СКО ошибки", value: numberText(s.std_error), hint: "Разброс ошибок вокруг средней (делитель — число проб)" },
    { key: "mae", title: "MAE", value: numberText(s.mae), hint: "Средняя абсолютная ошибка" },
    { key: "r", title: "r", value: numberText(s.r), hint: "Коэффициент корреляции Пирсона между истинным и оценкой; чем ближе к 1, тем лучше" },
    { key: "slope", title: "Наклон", value: numberText(s.slope), hint: "Регрессия истинного на оценку. Идеал — 1; меньше 1 — оценка завышает высокие значения и занижает низкие" },
    {
      key: "mean_ratio",
      title: "Среднее ошибка²/KV",
      value: numberText(s.mean_ratio),
      hint: s.mean_ratio === null ? "Дисперсии кригинга нет (IDW, NN, IK) или нет строк с KV больше нуля" : `Идеал — 1: дисперсия кригинга честна; больше 1 — она занижена. По строкам с KV больше нуля: ${s.n_ratio.toLocaleString("ru-RU")}${pct(s.n_ratio, s.n)}`,
    },
    { key: "n_kv_zero", title: "Без KV", value: s.n_kv_zero.toLocaleString("ru-RU"), hint: "Строки, где KV не больше нуля: стандартизованной ошибки у них нет" },
  ];
}

// ---- the words about the result ----

export const BY_WORDS: Record<XvalBy, string> = { hole: "по скважине", sample: "по пробе" };

/** The date of the calculation as the person reads it (the local time of the computer); "" — none. */
export function atText(at: string | null): string {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Why the result is out of date: what the core says (`stale_reason` of the row) after the general words. */
export function staleText(at: string | null, reason: string | null): string {
  const when = atText(at);
  const base = `Параметры оценки или исходные данные изменились после расчёта${when ? ` от ${when}` : ""}.`;
  return `${base}${reason ? ` ${reason[0].toUpperCase()}${reason.slice(1)}${/[.!?]$/.test(reason) ? "" : "."}` : ""} Выполните проверку заново.`;
}

/** The line the command answers with. */
export function resultLine(name: string, by: XvalBy, s: XvalSummary): string {
  return `Перекрёстная проверка «${name}» ${BY_WORDS[by]}: проб ${s.n.toLocaleString("ru-RU")}, средняя ошибка ${numberText(s.mean_error)}, СКО ${numberText(s.std_error)}, MAE ${numberText(s.mae)}, r ${numberText(s.r)}, наклон ${numberText(s.slope)}, среднее ошибка²/KV ${numberText(s.mean_ratio)}`;
}

// ---- the scatter plot «истинное — оценка» ----

const mean = (v: readonly number[]): number | null => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);

function stats(v: readonly number[]): ScatterResult["x_stats"] {
  const m = mean(v);
  const sd = m === null ? null : Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length);
  let lo = Infinity;
  let hi = -Infinity;
  for (const x of v) {
    if (x < lo) lo = x;
    if (x > hi) hi = x;
  }
  return { mean: m, std: sd, min: v.length ? lo : null, max: v.length ? hi : null };
}

/** The pairs (estimate, true) of the rows that have both: x — the estimate, y — the true value; the fit y = a + b·x is the summary's slope. */
export function scatterResult(rows: readonly XvalRow[], s: XvalSummary): ScatterResult | null {
  const x: number[] = [];
  const y: number[] = [];
  for (const r of rows) {
    if (r.est === null || r.truth === null) continue;
    x.push(r.est);
    y.push(r.truth);
  }
  if (!x.length) return null;
  const mx = mean(x) as number;
  const my = mean(y) as number;
  const fit = s.slope === null ? null : { b: s.slope, a: my - s.slope * mx };
  return {
    set_id: 0,
    x_field: "Оценка",
    y_field: "Истинное",
    color_field: null,
    log_x: false,
    log_y: false,
    weighted: false,
    counts: { rows: rows.length, missing_x: rows.length - x.length, missing_y: 0, unmatched: 0, bad_weight: 0, negative: 0, zero_excluded: 0, nonpositive_log: 0, used: x.length },
    mode: "points",
    x,
    y,
    c: null,
    color: null,
    pearson: s.r,
    spearman: null,
    n: x.length,
    fits: { ols: fit, rma: null, weighted: null },
    x_stats: stats(x),
    y_stats: stats(y),
  };
}

// ---- the histogram of the errors ----

const NO_STATS = (n: number, m: number | null, sd: number | null): Describe => ({ count: n, weight_sum: n, min: null, max: null, mean: m, median: null, std: sd, variance: sd === null ? null : sd * sd, cv: null, skewness: null, percentiles: {}, log: null });

/** The bins of the core as the result of the histogram of Э13 (counts of samples, the share and the running share in %). */
export function histogramResult(h: XvalHistogram, s: XvalSummary): HistogramResult | null {
  const { edges, counts } = h;
  if (edges.length < 2 || counts.length !== edges.length - 1) return null;
  const total = counts.reduce((a, b) => a + b, 0);
  const percent = counts.map((c) => (total > 0 ? (c / total) * 100 : 0));
  let run = 0;
  const cumulative = percent.map((p) => (run += p));
  const zero = { count: 0, weight: 0 };
  const used = { rows: total, missing: 0, bad_weight: 0, negative: 0, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 0, top_excluded: 0, used: total };
  return {
    field: "Ошибка",
    log: false,
    weighted: false,
    counts: used,
    stats: NO_STATS(total, s.mean_error, s.std_error),
    stats_unweighted: null,
    bins: { edges, counts, weights: counts.slice(), percent, cumulative_percent: cumulative, width: edges[1] - edges[0], below: zero, above: zero },
    conventions: "",
  };
}

/** The marks of the histogram: the zero (no bias) and the mean error. */
export function histogramMarks(s: XvalSummary): Mark[] {
  const marks: Mark[] = [{ value: 0, label: "0" }];
  if (s.mean_error !== null) marks.push({ value: s.mean_error, label: `средняя ${numberText(s.mean_error)}`, dashed: true });
  return marks;
}

// ---- the errors in 3D ----

/** Where the errors are drawn: the point of every sample with an error (project coordinates) and its colour. */
export interface ErrorCloud {
  xyz: Float64Array;
  /** 0…1, three numbers a point */
  rgb: Float32Array;
  /** the error that has the full colour, ±limit */
  limit: number;
  n: number;
}

/** The error that has the full colour: the 95th percentile of the absolute errors (a few wild ones do not wash out the rest). */
export function errorLimit(errors: readonly number[]): number {
  const abs = errors.filter(Number.isFinite).map(Math.abs).sort((a, b) => a - b);
  if (!abs.length) return 1;
  const p = abs[Math.floor(0.95 * (abs.length - 1))];
  return p > 0 ? p : abs[abs.length - 1] > 0 ? abs[abs.length - 1] : 1;
}

const channels = (hex: string): [number, number, number] => [parseInt(hex.slice(1, 3), 16) / 255, parseInt(hex.slice(3, 5), 16) / 255, parseInt(hex.slice(5, 7), 16) / 255];

export const COLOR_LOW = BRAND.cobalt;
export const COLOR_NONE = BRAND.stone;
export const COLOR_HIGH = BRAND.copper;

/** The colour of an error: Cobalt for −limit and below, Light Stone for 0, Copper Orange for +limit and above. */
export function errorColor(error: number, limit: number): [number, number, number] {
  const t = Math.max(-1, Math.min(1, error / limit));
  return channels(t < 0 ? mix(COLOR_NONE, COLOR_LOW, -t) : mix(COLOR_NONE, COLOR_HIGH, t));
}

/** The cloud of the samples that have a place and an error; null — there is none. */
export function errorCloud(rows: readonly XvalRow[]): ErrorCloud | null {
  const have = rows.filter((r) => r.error !== null && r.x !== null && r.y !== null && r.z !== null);
  if (!have.length) return null;
  const limit = errorLimit(have.map((r) => r.error as number));
  const xyz = new Float64Array(have.length * 3);
  const rgb = new Float32Array(have.length * 3);
  have.forEach((r, k) => {
    xyz.set([r.x as number, r.y as number, r.z as number], k * 3);
    rgb.set(errorColor(r.error as number, limit), k * 3);
  });
  return { xyz, rgb, limit, n: have.length };
}
