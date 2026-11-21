/**
 * Box plots by domains (ТЗ 8.3; ADR 0019 §4): the request from the settings, the range of the value axis, the
 * pixels of a box (on a log scale the core gives the quartiles and whiskers of the logarithms in data units, the
 * axis is log10 — the box is by the logarithms), the table, the notes (outliers over 5000 — how many are not drawn),
 * the CSV and the assistant's text. Shared by the charts by domains: the label of a group and the domain counts.
 * Pure.
 */
import type { Calculator } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import { computeBox, type BoxGroup, type BoxResult, type DomainCounts } from "../../modules/charts/apiMore";
import { needsWeightField, type BoxSettings, type SampleFilter } from "../../modules/charts/settings";
import { domainOf, formatNumber, type Domain, type Scale } from "../core/axisScale";
import { csvText } from "../core/csv";
import { countsNotes } from "../histogram/model";

export type BoxRequest = Omit<BoxSettings, "view">;

/** The body of `POST /stats/box`; null while it cannot be computed. */
export function boxRequest(s: BoxSettings): BoxRequest | null {
  if (!s.field || needsWeightField(s)) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function boxCalculator(apply: (r: BoxResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: BoxRequest) => Promise<BoxResult> = computeBox): Calculator<BoxRequest> {
  return dataCalculator(load, apply, fail, busy);
}

/** A domain as written: samples without a code — «без домена»; one group without a domain field — «все пробы». */
export const groupLabel = (label: string | null, domain: string | null): string => (label !== null ? label : domain ? "без домена" : "все пробы");

/** The value axis over the whiskers, the outliers drawn and the means (only > 0 on a log axis). */
export function boxDomain(r: Pick<BoxResult, "groups">, log: boolean): Domain | null {
  const values: (number | null)[] = [];
  for (const g of r.groups) values.push(g.whisker_low, g.whisker_high, g.q1, g.q3, g.mean, g.geometric_mean ?? null, ...g.outliers);
  return domainOf(values, log ? "log" : "linear", 0.05);
}

/**
 * The pixels of one box on a scale: null — no quartiles (an empty domain); a value the axis cannot show — null.
 * `geo` — the geometric mean (a log scale only).
 */
export function boxPixels(g: BoxGroup, scale: Scale): { q1: number; median: number; q3: number; low: number | null; high: number | null; mean: number | null; geo: number | null; outliers: number[] } | null {
  const q1 = scale.map(g.q1);
  const median = scale.map(g.median);
  const q3 = scale.map(g.q3);
  if (q1 === null || median === null || q3 === null) return null;
  const geo = g.geometric_mean === null || g.geometric_mean === undefined ? null : scale.map(g.geometric_mean);
  return { q1, median, q3, low: scale.map(g.whisker_low), high: scale.map(g.whisker_high), mean: scale.map(g.mean), geo, outliers: g.outliers.map((v) => scale.map(v)).filter((v): v is number => v !== null) };
}

const n = (v: number) => v.toLocaleString("ru-RU");

/** The rows of the table under the chart: one per domain; on a log scale — the geometric mean after the mean. */
export const BOX_HEADER = ["Домен", "n", "Среднее", "Q1", "Медиана", "Q3", "Ус нижний", "Ус верхний", "Мин", "Макс", "Выбросов"];

export const boxHeader = (r: Pick<BoxResult, "log">): string[] => (r.log ? [...BOX_HEADER.slice(0, 3), "Среднее геом.", ...BOX_HEADER.slice(3)] : BOX_HEADER);

function boxRow<T>(r: BoxResult, g: BoxGroup, f: (v: number | null) => T, count: (v: number) => T): (T | string)[] {
  const geo = r.log ? [f(g.geometric_mean ?? null)] : [];
  return [groupLabel(g.label, r.domain), count(g.n), f(g.mean), ...geo, f(g.q1), f(g.median), f(g.q3), f(g.whisker_low), f(g.whisker_high), f(g.min), f(g.max), count(g.outliers_total)];
}

export function boxTable(r: BoxResult): string[][] {
  return r.groups.map((g) => boxRow(r, g, (v) => formatNumber(v, 5), n) as string[]);
}

/** CSV of the table (numbers as they are, empty stays empty). */
export function boxCsv(r: BoxResult): string {
  return csvText(boxHeader(r), r.groups.map((g) => boxRow<number | null>(r, g, (v) => v, (v) => v)));
}

/** What the selection and the domains left out, in words. */
export function domainNotes(c: DomainCounts, s: Pick<SampleFilter, "negatives">): string[] {
  const out = countsNotes(c, s);
  if (c.no_domain) out.push(`без кода домена ${n(c.no_domain)}`);
  return out;
}

export function boxNotes(r: BoxResult, s: Pick<SampleFilter, "negatives">): string[] {
  const out = domainNotes(r.counts, s);
  if (r.outliers_total > r.outliers_shown) out.push(`выбросов показано ${n(r.outliers_shown)} из ${n(r.outliers_total)} (крайние оставлены)`);
  else if (r.outliers_total) out.push(`выбросов ${n(r.outliers_total)}`);
  if (r.log) out.push("квартили и усы — по логарифмам; ◇ — среднее, ○ — среднее геометрическое");
  return out;
}

/** The assistant's text: the name, the domains with n, the mean and the median. */
export function boxSummary(name: string, r: BoxResult): string {
  const geo = (g: BoxGroup) => (r.log && g.geometric_mean !== null && g.geometric_mean !== undefined ? `, среднее геометрическое ${formatNumber(g.geometric_mean, 4)}` : "");
  const parts = r.groups.slice(0, 12).map((g) => `${groupLabel(g.label, r.domain)}: n ${n(g.n)}, среднее ${formatNumber(g.mean, 4)}${geo(g)}, медиана ${formatNumber(g.median, 4)}`);
  return `${name}: ${parts.join("; ")}${r.groups.length > 12 ? `; ещё доменов ${r.groups.length - 12}` : ""}`;
}
