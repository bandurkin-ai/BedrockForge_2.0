/**
 * What the window «Влияние изменения» does with «Перестроить с зависимыми» (Д-35; ADR 0025 §9), apart from the drawing: the plan
 * (a dry run), then the start and the follow of the job. Each step answers the next state of the window (`Phase`), so the
 * window only shows it. No React.
 */
import type { App } from "../../shell/app";
import { errorText } from "../../shell/props";
import { planRebuild, type LNode, type LostItem, type Plan } from "./api";
import { takeOutcome } from "./rebuild";

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
 * Starts the job of the plan through the command `lineage.rebuild` of the registry (the log, the keys and the assistant see the same run);
 * `confirmed` — the plan was shown here, so the command does not ask about it again. `show` gets «running» on the way. A rebuild that
 * loses results or columns answers `lost` (repeat with `dropLost` when the person agrees); the end of the job is told as it was.
 */
export async function runPhase(app: App, node: LNode, plan: Plan, dropLost: boolean, show: (p: Phase) => void): Promise<Phase> {
  show({ t: "running", text: "Запуск… ход — во вкладке «Задания»." });
  const args: Record<string, unknown> = { kind: node.kind, id: node.id, ...(node.sub ? { sub: node.sub } : {}), drop_lost: dropLost, confirmed: true };
  const r = await app.registry.execute("lineage.rebuild", args, "user");
  const o = takeOutcome();
  if (o.lost) return { t: "lost", plan, lost: o.lost };
  if (o.end) return { t: "end", ok: o.end.status === "done", text: o.end.text };
  return r.ok ? { t: "end", ok: true, text: r.result } : { t: "error", text: r.result };
}
