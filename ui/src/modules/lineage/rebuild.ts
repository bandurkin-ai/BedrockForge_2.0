/**
 * «Перестроить с зависимыми» (Д-35; ADR 0025 §9): the words of the plan for the question to the person, the job followed to its
 * end and told in a line. The job `lineage.rebuild` runs the nodes one after another (an order of building); a failed node
 * stops what is built from it, a cancel stops the rest; there is no automatic rebuild. The result of the job:
 * `{done, nodes: [{kind, id, sub, name, outcome, state_after, job_kind?}]}` — `outcome`: `done`, `covered`, `replaced`, `failed`,
 * `skipped`. No React.
 */
import { CoreError } from "../../shell/api";
import { loadJob } from "../blocks/build/api";
import { projectStore } from "../projects/state";
import { waitJob } from "../qaqc/setupApi";
import { kindTitle, refusalOf, STATE_TITLES, type Plan, type Started } from "./api";

/** The lines of the plan: «1. Узел 1 — устарел (изменено: …)», a node made by the job of another — «вместе с …». */
export function planLines(p: Plan): string[] {
  return p.plan.map((it, i) => {
    const why = it.reason ? ` (${it.reason})` : "";
    const how = it.action === "covered" && it.coveredBy !== null ? `; создаётся вместе с «${p.plan[it.coveredBy]?.name ?? "…"}»` : "";
    return `${i + 1}. ${kindTitle(it.kind)} «${it.name}» — ${STATE_TITLES[it.state]}${why}${how}`;
  });
}

/** What the plan leaves out: «Отчёт — нельзя перестроить автоматически». */
export function skippedLines(p: Plan): string[] {
  return p.skipped.map((s) => `${kindTitle(s.kind)} «${s.name}» — ${s.reason || (s.state === "source_lost" ? "источник удалён" : "нельзя перестроить автоматически")}`);
}

/** The jobs of the plan (an item made by the job of another is not counted). */
export const jobsOf = (p: Plan): number => p.plan.filter((i) => i.action === "rebuild").length;

/** The head of the plan cannot be rebuilt when its source is gone: the core refuses `source_lost` before the job. */
export const headLost = (p: Plan): boolean => p.plan[0]?.state === "source_lost";

export interface RebuildEnd {
  status: "done" | "failed" | "cancelled";
  text: string;
}

interface ResultNode {
  name: string;
  outcome: string;
}

function resultNodes(result: unknown): ResultNode[] {
  const nodes = (result as { nodes?: unknown } | null)?.nodes;
  if (!Array.isArray(nodes)) return [];
  return nodes.map((n) => ({ name: String((n as { name?: unknown }).name ?? ""), outcome: String((n as { outcome?: unknown }).outcome ?? "") }));
}

/** Follows the job to its end; the line tells what was rebuilt (and what was not, if the job stopped). */
export async function followRebuild(job: Started): Promise<RebuildEnd> {
  const uid = projectStore.get().current?.uid ?? null;
  const end = await waitJob(job);
  if ((projectStore.get().current?.uid ?? null) !== uid) return { status: "cancelled", text: "Проект изменился: ход перестроения больше не показывается." };
  if (end.status === "failed") {
    const e = end.error;
    return { status: "failed", text: e ? `${e.message}${e.hint ? ` ${e.hint}` : ""}` : "Перестроение не выполнено." };
  }
  const nodes = resultNodes((await loadJob(job).catch(() => null))?.result ?? end.result);
  const done = nodes.filter((n) => ["done", "covered", "replaced"].includes(n.outcome)).length;
  if (end.status === "cancelled") return { status: "cancelled", text: `Перестроение отменено: готово ${done}${nodes.length ? ` из ${nodes.length}` : ""}, остальные остались «устарел».` };
  const failed = nodes.filter((n) => n.outcome === "failed");
  if (failed.length) return { status: "failed", text: `Не перестроено: ${failed.map((n) => `«${n.name}»`).join(", ")}.` };
  return { status: "done", text: `Перестроено объектов: ${done}.` };
}

/** The refusal of the core as an error with its Russian text and hint (what a command throws). */
export function asRefusal(e: unknown): CoreError {
  const r = refusalOf(e);
  return new CoreError(0, r.code, r.message, r.hint);
}
