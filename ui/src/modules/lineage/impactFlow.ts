/**
 * What the window «Влияние изменения» does with «Перестроить с зависимыми» (Д-35; ADR 0025 §9), apart from the drawing: the plan
 * (a dry run), then the start and the follow of the job. Each step answers the next state of the window (`Phase`), so the
 * window only shows it. No React.
 */
import type { App } from "../../shell/app";
import { showJobs } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { lostOf, planRebuild, startRebuild, type LNode, type LostItem, type Plan } from "./api";
import { followRebuild } from "./rebuild";

export type Phase =
  | { t: "idle" }
  | { t: "planning" }
  | { t: "plan"; plan: Plan }
  | { t: "lost"; plan: Plan; lost: LostItem[] }
  | { t: "running"; text: string }
  | { t: "end"; ok: boolean; text: string }
  | { t: "error"; text: string };

/** The plan of the rebuild of the node with what is built from it: `plan`, or the refusal of the core in words. */
export async function planPhase(node: LNode): Promise<Phase> {
  try {
    return { t: "plan", plan: await planRebuild(node) };
  } catch (e) {
    return { t: "error", text: errorText(e) };
  }
}

/**
 * Starts the job of the plan and follows it; `show` gets «running» on the way. A rebuild that loses results or columns answers
 * `lost` (repeat with `dropLost` when the person agrees).
 */
export async function runPhase(app: App, node: LNode, plan: Plan, dropLost: boolean, show: (p: Phase) => void): Promise<Phase> {
  show({ t: "running", text: "Запуск…" });
  try {
    const started = await startRebuild(node, { dropLost });
    showJobs(app);
    show({ t: "running", text: `Задание №${started.job.id} запущено: ход — во вкладке «Задания».` });
    const end = await followRebuild(started.job);
    return { t: "end", ok: end.status === "done", text: end.text };
  } catch (e) {
    const lost = lostOf(e);
    return lost ? { t: "lost", plan, lost } : { t: "error", text: errorText(e) };
  }
}
