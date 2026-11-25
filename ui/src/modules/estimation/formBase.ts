/**
 * What the forms of the wizard «Оценка содержаний» share (ТЗ 8.8.2, 8.8.4; docs/progress/e18-api.md §3): the words under a field
 * (`Issue`: the first wrong thing with the path of the body), the ranges of the numbers (they mirror `spec_parts.RANGES` of the
 * core; a test compares them with `ranges` of the answer of `defaults`) and the check of a typed number. No React.
 */
import { numberText, parseNumber } from "../composites/params";

/** `lo`/`hi` — null: no bound; `open` — the lower bound itself is not allowed. */
export interface Bounds {
  lo: number | null;
  hi: number | null;
  integer?: boolean;
  open?: boolean;
  unit?: string;
}

const RAW = {
  power: { lo: 0.5, hi: 5 },
  disc: { lo: 1, hi: 10, integer: true },
  soft_dist: { lo: 0, hi: 100_000, unit: "м" },
  topcut: { lo: null, hi: null },
  mean: { lo: null, hi: null },
  threshold: { lo: null, hi: null },
  // the passes of the search (8.8.2, Д-29)
  factor: { lo: 0, hi: 100, open: true },
  n_min: { lo: 1, hi: 128, integer: true },
  n_max: { lo: 1, hi: 128, integer: true },
  n_hole: { lo: 0, hi: 64, integer: true },
  n_oct: { lo: 0, hi: 16, integer: true },
  k_oct: { lo: 0, hi: 8, integer: true },
  n_holes_min: { lo: 0, hi: 128, integer: true },
  hy_t: { lo: null, hi: null },
  hy_q: { lo: 0, hi: 1, open: true },
  // the ellipsoid of the search (the angles are those of the variography)
  r1: { lo: 0, hi: 100_000, open: true, unit: "м" },
  r2: { lo: 0, hi: 100_000, open: true, unit: "м" },
  r3: { lo: 0, hi: 100_000, open: true, unit: "м" },
  ang1: { lo: 0, hi: 360, unit: "°" },
  ang2: { lo: -90, hi: 90, unit: "°" },
  ang3: { lo: -180, hi: 180, unit: "°" },
} satisfies Record<string, Bounds>;

export const RANGES: Record<keyof typeof RAW, Bounds> = RAW;

/** The limits of the core (`params.limits` of `defaults`): the passes, the thresholds of IK, the prefix, the values of the domain. */
export const PASSES_MAX = 5;
export const PREFIX_MAX = 40;

/** The first wrong thing of the form (or the refusal of the core): the words, the hint, the path of the body. */
export interface Issue {
  param: string | null;
  code: string;
  message: string;
  hint: string;
  /** a 409 `version_conflict`: «Перечитать» / «Сохранить как новые» */
  reread?: boolean;
}

export const bad = (param: string, message: string, hint: string, code = "bad_param"): Issue => ({ param, code, message, hint });

/** Does the issue belong to the field `path` (itself or inside it)? */
export const issueAt = (issue: Issue | null, ...paths: string[]): Issue | null => (issue && issue.param !== null && paths.some((p) => issue.param === p || issue.param!.startsWith(`${p}.`)) ? issue : null);

/** A typed number checked against its range: the number, or the words under the field. */
export function numberIn(text: string, r: Bounds, label: string, param: string): number | Issue {
  const v = parseNumber(text);
  const range = `${r.lo !== null ? `от ${numberText(r.lo)}` : ""}${r.hi !== null ? ` до ${numberText(r.hi)}` : ""}${r.unit ? ` ${r.unit}` : ""}`.trim();
  if (v === null) return bad(param, `${label}: не число.`, range ? `Введите число ${range}.` : "Введите число, например 0,5.");
  if (r.integer && !Number.isInteger(v)) return bad(param, `${label}: нужно целое число.`, range ? `Введите целое ${range}.` : "Введите целое число.");
  if ((r.lo !== null && (r.open ? v <= r.lo : v < r.lo)) || (r.hi !== null && v > r.hi)) return bad(param, `${label}: значение вне диапазона${range ? ` ${range}` : ""}.`, range ? `Задайте число ${range}.` : "Задайте другое число.");
  return v;
}

export const isIssue = (v: unknown): v is Issue => typeof v === "object" && v !== null && "code" in v && "message" in v;
