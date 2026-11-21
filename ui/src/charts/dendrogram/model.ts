/**
 * The dendrogram (ТЗ 7.27; ADR 0019 §4; перенос `DendrogramWindow` / `DendrogramChart` BF): the request from the
 * settings, the links offered (no Ward: it needs Euclidean distances, Т7.27-2), the groups at a cut (as scipy
 * `fcluster`, criterion «distance»: merges at or below the cut join), the colours of the matrix, the CSV of the
 * matrix in the order of the leaves, the notes and the assistant's text. A missing r stays empty, never 0. Pure.
 */
import { computeDendrogram, type Calculator, type DendrogramResult, type NumericField } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import type { DendrogramSettings } from "../../modules/charts/settings";
import { formatNumber } from "../core/axisScale";
import { csvText } from "../core/csv";

export type DendrogramRequest = Omit<DendrogramSettings, "view">;

/** The links of the window (ТЗ 7.27): UPGMA, complete, single, weighted — Ward is not offered. */
export const LINKAGES: [DendrogramSettings["linkage"], string][] = [
  ["average", "Средняя (UPGMA)"],
  ["complete", "Полная"],
  ["single", "Одиночная"],
  ["weighted", "Взвешенная (WPGMA)"],
];

/** At least two fields are clustered. */
export const MIN_FIELDS = 2;

/** The fields clustered: those chosen, or every component of the table. */
export function chosenFields(s: Pick<DendrogramSettings, "fields">, numeric: readonly NumericField[]): string[] {
  return s.fields.length ? s.fields : numeric.filter((f) => f.kind === "component").map((f) => f.name);
}

/** The body of `POST /stats/dendrogram` — the settings without the view; null with fewer than 2 fields. */
export function dendrogramRequest(s: DendrogramSettings, numeric?: readonly NumericField[]): DendrogramRequest | null {
  if (numeric && chosenFields(s, numeric).length < MIN_FIELDS) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function dendrogramCalculator(apply: (r: DendrogramResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: DendrogramRequest) => Promise<DendrogramResult> = computeDendrogram): Calculator<DendrogramRequest> {
  return dataCalculator(load, apply, fail, busy);
}

/**
 * Groups at a cut: fields joined by merges at or below `cut` share a group (scipy `fcluster`, «distance»).
 * `leaf` — the group of each field (0… numbered down the leaves), `merge` — of each merge (−1 above the cut).
 */
export function clustersAt(linkage: readonly (readonly number[])[], n: number, order: readonly number[], cut: number): { leaf: number[]; merge: number[] } {
  const parent = Array.from({ length: 2 * n - 1 }, (_, i) => i);
  const find = (x: number): number => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  linkage.forEach(([a, b, d], i) => {
    if (d <= cut) {
      parent[find(a)] = n + i;
      parent[find(b)] = n + i;
    }
  });
  const ids = new Map<number, number>();
  const leaf = new Array<number>(n).fill(-1);
  for (const f of order) {
    const root = find(f);
    if (!ids.has(root)) ids.set(root, ids.size);
    leaf[f] = ids.get(root)!;
  }
  const first = (node: number): number => {
    let x = node;
    while (x >= n) x = linkage[x - n][0];
    return x;
  };
  const merge = linkage.map(([, , d], i) => (d <= cut ? leaf[first(n + i)] : -1));
  return { leaf, merge };
}

/** The colours of the groups: the brand's Cobalt and Copper Orange first. */
export const GROUP_COLORS = ["#1f6bff", "#f46a1a", "#2fa36b", "#9b5de5", "#d4a017", "#d64550", "#1fa2b5", "#8a99a8"];
export const groupColor = (k: number): string | null => (k < 0 ? null : GROUP_COLORS[k % GROUP_COLORS.length]);

/** The colour of r in [−1; 1]: Cobalt negative, Light Stone zero, Copper Orange positive; missing — Slate. */
export function corrColor(r: number | null | undefined): string {
  if (r === null || r === undefined || !Number.isFinite(r)) return "#8a99a8";
  const t = Math.max(-1, Math.min(1, r));
  const mix = (a: number, b: number, k: number) => Math.round(a + (b - a) * k);
  const [to, k] = t >= 0 ? [[0xf4, 0x6a, 0x1a], t] : [[0x1f, 0x6b, 0xff], -t];
  return `rgb(${mix(0xe6, to[0], k)},${mix(0xe9, to[1], k)},${mix(0xee, to[2], k)})`;
}

/** CSV of the matrix (ТЗ 7.27 «Экспорт»): r in the order of the leaves; a missing r stays empty. */
export function matrixCsv(r: DendrogramResult): string {
  const names = r.order.map((i) => r.fields[i]);
  return csvText(["", ...names], r.order.map((i) => [r.fields[i], ...r.order.map((j) => r.correlation[i]?.[j] ?? null)]));
}

/** The pair with the strongest |r| (off the diagonal); null — none computed. */
export function strongest(r: DendrogramResult): { a: string; b: string; r: number } | null {
  let best: { a: string; b: string; r: number } | null = null;
  for (let i = 0; i < r.fields.length; i++)
    for (let j = i + 1; j < r.fields.length; j++) {
      const v = r.correlation[i]?.[j];
      if (typeof v === "number" && Number.isFinite(v) && (!best || Math.abs(v) > Math.abs(best.r))) best = { a: r.fields[i], b: r.fields[j], r: v };
    }
  return best;
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function dendrogramNotes(r: DendrogramResult, cut: number | null): string[] {
  const out = [`полей ${r.fields.length}`, `строк ${n(r.rows)}, в расчёте ${n(r.used)}`];
  if (r.nonpositive_log) out.push(`значений ≤ 0 не учтено для ln: ${n(r.nonpositive_log)}`);
  out.push(`наибольшее расстояние ${formatNumber(r.max_distance, 4)}`);
  if (cut !== null && r.fields.length) out.push(`групп при пороге ${formatNumber(cut, 4)}: ${new Set(clustersAt(r.linkage, r.fields.length, r.order, cut).leaf).size}`);
  return out;
}

/** The assistant's text (ADR 0019 §9): the name, fields, rows, groups at the cut, the strongest pair. */
export function dendrogramSummary(name: string, r: DendrogramResult, cut: number | null): string {
  const pair = strongest(r);
  const groups = cut !== null && r.fields.length ? `, групп при пороге ${formatNumber(cut, 4)}: ${new Set(clustersAt(r.linkage, r.fields.length, r.order, cut).leaf).size}` : "";
  return `${name}: полей ${r.fields.length}, строк в расчёте ${n(r.used)}${groups}; порядок листьев ${r.order.map((i) => r.fields[i]).join(", ")}${pair ? `; сильнее всего связаны ${pair.a} и ${pair.b} (r = ${formatNumber(pair.r, 3)})` : ""}`;
}
