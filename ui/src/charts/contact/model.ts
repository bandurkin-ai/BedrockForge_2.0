/**
 * The contact analysis along the holes (ТЗ 8.3; ADR 0019 §4): the request (domains A and B of a text field, or a
 * wireframe — then the sides are «снаружи» / «внутри»), the steps b from −D to D (side A — d < 0, B — d > 0) with
 * the mean (weight — the length; a step without samples stays empty, never 0) and n, the number of contacts, the CSV
 * and the assistant's text. A step plot of the means shows a hard boundary, a slope — a soft one (8.8.2). Pure.
 */
import type { Calculator } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import { computeContact, type ContactResult } from "../../modules/charts/apiMore";
import { needsWeightField, type ContactSettings } from "../../modules/charts/settings";
import { formatNumber } from "../core/axisScale";
import { csvText } from "../core/csv";

export type ContactRequest = Omit<ContactSettings, "view">;

/** No more steps than this on each side (the chart and the table stay readable; the core may refuse more). */
export const MAX_STEPS = 500;

/** Why the analysis cannot be computed yet; null — it can. */
export function contactProblem(s: ContactSettings): string | null {
  if (!s.field) return "Выберите поле.";
  if (needsWeightField(s)) return "Выберите поле веса.";
  if (s.wireframe_id === null) {
    if (!s.domain) return "Выберите поле домена.";
    if (!s.a || !s.b) return "Выберите домены A и B.";
    if (s.a === s.b) return "Домены A и B должны различаться.";
  }
  if (!(s.distance > 0) || !(s.step > 0)) return "Расстояние и шаг — больше 0.";
  if (s.distance / s.step > MAX_STEPS) return `Не больше ${MAX_STEPS} шагов на сторону: увеличьте шаг.`;
  return null;
}

export function contactRequest(s: ContactSettings): ContactRequest | null {
  if (contactProblem(s)) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function contactCalculator(apply: (r: ContactResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: ContactRequest) => Promise<ContactResult> = computeContact): Calculator<ContactRequest> {
  return dataCalculator(load, apply, fail, busy);
}

export interface ContactStep {
  from: number;
  to: number;
  mid: number;
  side: "a" | "b";
  mean: number | null;
  n: number;
}

/** The steps between the edges; a step below 0 is of side A. */
export function contactSteps(r: Pick<ContactResult, "edges" | "mean" | "n">): ContactStep[] {
  const out: ContactStep[] = [];
  for (let i = 0; i + 1 < r.edges.length; i++) {
    const from = r.edges[i];
    const to = r.edges[i + 1];
    const m = r.mean[i];
    out.push({ from, to, mid: (from + to) / 2, side: to <= 0 ? "a" : "b", mean: m === null || m === undefined || !Number.isFinite(m) ? null : m, n: r.n[i] ?? 0 });
  }
  return out;
}

/** The step line of the means: runs of steps with a mean (an empty step breaks the line). */
export function stepRuns(steps: readonly ContactStep[]): ContactStep[][] {
  const runs: ContactStep[][] = [];
  let cur: ContactStep[] = [];
  for (const s of steps) {
    if (s.mean === null) {
      if (cur.length) runs.push(cur);
      cur = [];
    } else cur.push(s);
  }
  if (cur.length) runs.push(cur);
  return runs;
}

export function contactCsv(r: ContactResult): string {
  return csvText(["От, м", "До, м", "Сторона", "Среднее", "Проб"], contactSteps(r).map((s) => [s.from, s.to, s.side === "a" ? r.a.label : r.b.label, s.mean, s.n]));
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function contactNotes(r: ContactResult): string[] {
  const out = [`контактов ${n(r.contacts)} в ${n(r.holes)} скважинах`, `шаг ${formatNumber(r.step)} м, до ${formatNumber(r.distance)} м`];
  if (r.wireframe_id !== null) out.push("контакт — пересечение ствола с каркасом");
  if (r.counts.no_domain) out.push(`без кода домена ${n(r.counts.no_domain)}`);
  if (r.counts.missing) out.push(`без значения ${n(r.counts.missing)}`);
  return out;
}

export function contactSummary(name: string, r: ContactResult): string {
  return `${name}: контактов ${n(r.contacts)} (скважин ${n(r.holes)}); ${r.a.label}: проб ${n(r.a.n)}, среднее ${formatNumber(r.a.mean, 4)}; ${r.b.label}: проб ${n(r.b.n)}, среднее ${formatNumber(r.b.mean, 4)}`;
}
