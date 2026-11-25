/**
 * The contact analysis along the holes (ТЗ 8.3; ADR 0019 §4): the request (domains A and B of a text field, or a
 * wireframe — then the sides are «снаружи» / «внутри»), the steps b from −D to D (side A — d < 0, B — d > 0) with
 * the mean (weight — the length; a step without samples stays empty, never 0) and n, the number of contacts, the CSV
 * and the assistant's text. A step plot of the means shows a hard boundary, a slope — a soft one (8.8.2). Pure.
 */
import type { Calculator } from "../../modules/charts/api";
import type { App } from "../../shell/app";
import { dataCalculator } from "../../modules/charts/state";
import { computeContact, type ContactResult } from "../../modules/charts/apiMore";
import { needsWeightField, type ContactBlocks, type ContactSettings } from "../../modules/charts/settings";
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
  return blocksProblem(s);
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
  const blocks = blockSteps(r);
  const volume = r.blocks?.volume ?? [];
  const head = ["От, м", "До, м", "Сторона", "Среднее", "Проб"];
  return csvText(
    blocks.length ? [...head, "Блоки, среднее", "Блоков", "Объём блоков, м³"] : head,
    contactSteps(r).map((s, i) => {
      const row = [s.from, s.to, s.side === "a" ? r.a.label : r.b.label, s.mean, s.n];
      return blocks.length ? [...row, blocks[i].mean, blocks[i].n, volume[i] ?? null] : row;
    }),
  );
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function contactNotes(r: ContactResult): string[] {
  const out = [`контактов ${n(r.contacts)} в ${n(r.holes)} скважинах`, `шаг ${formatNumber(r.step)} м, до ${formatNumber(r.distance)} м`];
  if (r.wireframe_id !== null) out.push("контакт — пересечение ствола с каркасом");
  if (r.counts.no_domain) out.push(`без кода домена ${n(r.counts.no_domain)}`);
  if (r.counts.missing) out.push(`без значения ${n(r.counts.missing)}`);
  const blocks = blocksNote(r);
  if (blocks) out.push(blocks);
  return out;
}

export function contactSummary(name: string, r: ContactResult): string {
  const base = `${name}: контактов ${n(r.contacts)} (скважин ${n(r.holes)}); ${r.a.label}: проб ${n(r.a.n)}, среднее ${formatNumber(r.a.mean, 4)}; ${r.b.label}: проб ${n(r.b.n)}, среднее ${formatNumber(r.b.mean, 4)}`;
  const b = r.blocks;
  return b ? `${base}; блоки «${b.field}»: ${r.a.label}: блоков ${n(b.a.n)}, среднее ${formatNumber(b.a.mean, 4)}; ${r.b.label}: блоков ${n(b.b.n)}, среднее ${formatNumber(b.b.mean, 4)}` : base;
}

// ---- the blocks beside the samples (Д-28) ----

/** The largest d_м the wizard takes (`RANGES.soft_dist` of the estimation). */
export const SOFT_MAX = 100_000;

/** The profile of the blocks as steps of the same edges as the samples' (a different number of steps — the profile is not drawn). */
export function blockSteps(r: ContactResult): ContactStep[] {
  const b = r.blocks;
  return b && b.edges.length === r.edges.length && b.edges.every((e, i) => e === r.edges[i]) ? contactSteps(b) : [];
}

/** The blocks of the request: the profile needs the model and the field of its estimate. */
export function blocksProblem(s: ContactSettings): string | null {
  if (!s.blocks) return null;
  if (!s.blocks.field) return "Выберите поле оценки блочной модели.";
  return null;
}

/** The blocks the profile leaves out and why (`counts` of `signed_chart`): a block without a value, without a volume, farther than the distance, at no contact. */
const LEFT_OUT: readonly (readonly [string, string])[] = [
  ["no_value", "без значения"],
  ["no_weight", "без объёма"],
  ["beyond", "дальше расстояния контакта"],
  ["no_contact", "вне контакта"],
];

/** The number of blocks the profile took, with the ones left out of the estimate (STATUS ≠ 0) and the others the profile did not take (with the reasons). */
export function blocksNote(r: ContactResult): string | null {
  const b = r.blocks;
  if (!b) return null;
  const used = b.counts.in_steps ?? 0;
  const skipped = b.counts.not_estimated ?? 0;
  // the blocks «не оценено» have their value cleared by the core, so they are in `no_value` too: said once, in their own words
  const count = (k: string): number => (k === "no_value" ? Math.max(0, (b.counts[k] ?? 0) - skipped) : (b.counts[k] ?? 0));
  const left = LEFT_OUT.filter(([k]) => count(k) > 0).map(([k, label]) => `${label} ${n(count(k))}`);
  return `блоки «${b.field}»: ${n(used)} в шагах${skipped ? `, не оценено ${n(skipped)}` : ""}${left.length ? `; отсеяно: ${left.join(", ")}` : ""}`;
}

/** Why the soft boundary cannot be set from this chart yet; null — it can. */
export function softProblem(s: ContactSettings, distance: number | null): string | null {
  if (s.wireframe_id !== null) return "Мягкая граница задаётся для пары доменов A | B, не для каркаса.";
  if (!s.a || !s.b || s.a === s.b) return "Выберите два разных домена A и B.";
  if (distance === null || !(distance > 0)) return "Задайте расстояние d_м больше 0 — решение за вами.";
  if (distance > SOFT_MAX) return `Расстояние d_м — не больше ${SOFT_MAX.toLocaleString("ru-RU")} м.`;
  return null;
}

/** The arguments of `estimate.open` (18.19): the pair A, B and the d_м the person chose; the model of the profile, when there is one. */
export function softArgs(s: ContactSettings, distance: number): Record<string, unknown> {
  return { soft_a: s.a, soft_b: s.b, soft_distance: distance, ...(s.blocks ? { model_id: s.blocks.model_id } : {}) };
}

/** «Задать мягкую границу…»: the wizard of the estimation with the pair and d_м (through the registry: a command as any other); null — it opened. */
export async function openSoft(app: Pick<App, "registry">, s: ContactSettings, distance: number | null): Promise<string | null> {
  const why = softProblem(s, distance);
  if (why) return why;
  const r = await app.registry.execute("estimate.open", softArgs(s, distance!));
  return r.ok ? null : r.result || "Мастер оценки не открылся.";
}

/** What the window preselects for a model of blocks: the field of the estimate (`…EST`), its status (`…STATUS`) and the field of the domain of the model (the name of the samples' domain, else the one that has both A and B). */
export function blockDefaults(fields: readonly { name: string; kind: string; labels?: string[] }[], s: Pick<ContactSettings, "domain" | "a" | "b">): Omit<ContactBlocks, "model_id"> | null {
  const numbers = fields.filter((f) => f.kind === "number");
  const status = numbers.find((f) => /STATUS$/i.test(f.name));
  const field = numbers.find((f) => /EST$/i.test(f.name)) ?? numbers.find((f) => f !== status);
  if (!field) return null;
  const cats = fields.filter((f) => f.kind === "category");
  const domain = (s.domain ? cats.find((f) => f.name.toLowerCase() === s.domain!.toLowerCase()) : undefined) ?? (s.a && s.b ? cats.find((f) => f.labels?.includes(s.a!) && f.labels.includes(s.b!)) : undefined);
  return { field: field.name, ...(status ? { status_field: status.name } : {}), ...(domain ? { domain_field: domain.name } : {}) };
}
