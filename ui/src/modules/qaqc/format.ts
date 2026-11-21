/**
 * Texts and colours of QAQC (ТЗ 8.4.2; ADR 0020 §4, §11): statuses «норма / предупреждение / отказ» and the rules
 * behind them, classes of the bias, states of a group, numbers and percents (a missing one is «—», never 0),
 * dates of the records (UTC: the core gives them without a zone). Colours of statuses are the brand Cobalt for the
 * norm and the theme's warning and danger tones; they are baked into the charts (an exported PNG keeps them). Pure.
 */
import { formatNumber } from "../../charts/core/axisScale";
import { BRAND } from "../../charts/column/classes";
import { RULE_PAIR, RULE_RUN, RULE_SWAP, type DateValue } from "./api";

export const STATUS_TITLES = ["норма", "предупреждение", "отказ"] as const;
/** Norm — Cobalt (the data), warning — amber, failure — red: the `--warn` and `--danger` tokens of the dark theme. */
export const STATUS_COLORS = [BRAND.cobalt, "#f4b13a", "#e5483a"] as const;
/** The status −1 of the core (no reference, no DL, nothing assessed): index 3, Slate Grey — never the norm. */
export const NOT_ASSESSED = 3;
export const QC_STATUS_TITLES = [...STATUS_TITLES, "не оценён"] as const;
export const QC_STATUS_COLORS = [...STATUS_COLORS, BRAND.slate] as const;

/** The status of the core as an index of the titles and colours: 0, 1, 2; −1, none or NaN — 3 «не оценён». */
export function statusIndex(s: number | null | undefined): 0 | 1 | 2 | 3 {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return NOT_ASSESSED;
  return s >= 2 ? 2 : s >= 1 ? 1 : 0;
}
export const statusTitle = (s: number | null | undefined): string => QC_STATUS_TITLES[statusIndex(s)];
export const statusColor = (s: number | null | undefined): string => QC_STATUS_COLORS[statusIndex(s)];

/** Why a point of a control chart has its status: the bits of `rule` (ADR 0020 §4). */
export function ruleTitles(rule: number): string[] {
  const out: string[] = [];
  if (rule & RULE_PAIR) out.push("две подряд за 2σ");
  if (rule & RULE_RUN) out.push("серия по одну сторону μ");
  if (rule & RULE_SWAP) out.push("возможна подмена");
  return out;
}

/** «отказ (две подряд за 2σ)», «предупреждение (серия по одну сторону μ)», «норма». */
export function statusLabel(status: number, rule = 0): string {
  const why = ruleTitles(rule);
  return why.length ? `${statusTitle(status)} (${why.join(", ")})` : statusTitle(status);
}

export const BIAS_TITLES = ["норма", "внимание", "недопустимо"] as const;
export const biasTitle = (c: number | null | undefined): string => (c === null || c === undefined || !Number.isFinite(c) || c < 0 ? "—" : BIAS_TITLES[c >= 2 ? 2 : c >= 1 ? 1 : 0]);

const STATES: Record<string, string> = {
  ok: "",
  no_reference: "нет в справочнике CRM",
  unit_mismatch: "единицы не совпадают",
  few_pairs: "меньше 50 пар",
  no_dl: "нет DL",
};
/** A group without an assessment and why; «» — assessed. */
export const stateTitle = (state: string | null | undefined): string => (state ? (STATES[state] ?? state) : "");

export const labTitle = (lab: string | null | undefined): string => (lab ? lab : "без лаборатории");

/** A number of the tables: 4 significant digits, a decimal comma; missing — «—». */
export const num = (v: number | null | undefined, digits = 4): string => formatNumber(v, digits);

/** A percent with one decimal: «12,5 %»; missing — «—». */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits).replace(".", ",")} %`;
}

export const int = (n: number | null | undefined): string => (n === null || n === undefined || !Number.isFinite(n) ? "—" : n.toLocaleString("ru-RU"));

/** The share of `part` in `whole`, %, or null for an empty whole. */
export const share = (part: number, whole: number): number | null => (whole > 0 ? (100 * part) / whole : null);

/** ms since 1970 (UTC) of a date of a record: ISO text without a zone is taken as UTC; none or wrong — null. */
export function dateMs(v: DateValue | undefined): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(v.trim());
  if (!m) return null;
  if (m[8]) {
    const t = Date.parse(v.trim());
    return Number.isFinite(t) ? t : null;
  }
  const n = (x: string | undefined) => (x === undefined ? 0 : Number(x));
  return Date.UTC(n(m[1]), n(m[2]) - 1, n(m[3]), n(m[4]), n(m[5]), n(m[6]), m[7] ? Number(m[7].padEnd(3, "0")) : 0);
}

const two = (n: number) => String(n).padStart(2, "0");

/** A row of a table chosen from the keyboard as by a click: Enter or Space (the page does not scroll). */
export const onActivateKey =
  (run: () => void) =>
  (e: { key: string; preventDefault(): void }): void => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    run();
  };

/** «ДД.ММ.ГГГГ», with the time when it is not midnight; null — «—». */
export function dateText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const d = new Date(ms);
  const day = `${two(d.getUTCDate())}.${two(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
  const h = d.getUTCHours();
  const mi = d.getUTCMinutes();
  return h || mi ? `${day} ${two(h)}:${two(mi)}` : day;
}
