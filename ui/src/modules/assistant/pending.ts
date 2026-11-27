/**
 * The commands of the window Claude asked for (Э23, 23.15; ADR 0029 решения 22–23, 25, 26; docs/progress/e23-api.md §9–§11): a turn that ends with `pending` is run here.
 *
 * - `PendingRunner.run(pending)` — the commands in the order the model gave them, each by `registry.execute(id, args, "claude")` (the arguments are checked, `uiOnly` and
 *   an unavailable command are refused with a reason, `confirm` waits for the card of the chat, the log gets the source «Claude»). The command is found by the `name` of the
 *   tool (`findByToolName`): `Pending.tools[].command` may be null. The result of a command is cut to 4000 characters. When all are done the results go to the core
 *   (`submitResults`) and the turn goes on. «Стоп» (`stop`): the command that runs is left to finish, a question of confirmation is declined, the rest get «Пользователь остановил ход»;
 *   the results are sent all the same (§11.4). `project.delete` ran: the dialog went with the project (решение 32), so nothing is sent — the feed gets a notice (`closeTurn`);
 *   the same notice (another text) when the core answers 404 `assistant_dialog_not_found` to the results (a project was opened meanwhile).
 * - A dialog opened with `pending` (after a restart, решение 23) is not run by itself: the cards show «Выполнить» / «Отклонить» (`runPendingCards`, `declinePendingCards`).
 * - The run is dropped (nothing is sent) when the pending of the dialog is not the one it was started for any more: a new question, a new or another dialog.
 *
 * The state of the chat is reached only through `PendingIo` (the real one is `state.ts`), so the runner is tested without the stream. No React; the text of the
 * arguments and of the results is not written anywhere but the log of the commands.
 */
import { findByToolName, type Command } from "../../shell/commands";
import { DECLINED, type CommandRegistry, type RunResult } from "../../shell/registry";
import { Store } from "../../shell/store";
import { declineAllConfirms } from "./confirm";
import type { ChatState } from "./reduce";
import { chatStore, closeTurn, markTool, submitResults, type CloseReason } from "./state";
import type { Pending, PendingTool, ToolResultIn, ToolStatus } from "./wireTypes";

export const RESULT_MAX = 4000;
export const NO_SUCH_COMMAND = "Нет такой команды";
export const STOPPED = "Пользователь остановил ход";
export { DECLINED };

/** A result of a command as the core takes it: ≤ 4000 characters, the cut shown with «…». */
export function clipResult(text: string): string {
  return text.length <= RESULT_MAX ? text : `${text.slice(0, RESULT_MAX - 1)}…`;
}

/** What the card of a command shows: a refusal of the user is «отклонено», any other failure — «ошибка». */
export function statusOf(r: Pick<ToolResultIn, "ok" | "text">): ToolStatus {
  if (r.ok) return "ok";
  return r.text === DECLINED || r.text === STOPPED ? "declined" : "error";
}

/** The doors of the runner to the state of the chat. */
export interface PendingIo {
  chat: () => Pick<ChatState, "pending" | "items" | "status">;
  mark: (itemId: string, status: ToolStatus, summary: string) => void;
  /** `true` while the commands run (the panel shows «Стоп», a new question is refused), `false` — they wait again */
  working: (on: boolean) => void;
  /** the results go to the core; null — nothing to continue (the pending is gone) */
  submit: (results: ToolResultIn[]) => Promise<unknown> | null;
  /** the turn ends here, nothing is sent: the dialog is gone with the project (a notice in the feed, the chat is idle) */
  close: (reason: CloseReason) => void;
}

export const liveIo: PendingIo = {
  chat: () => chatStore.get(),
  mark: markTool,
  working: (on) => chatStore.set({ status: on ? "running" : "pending", thinking: false }),
  submit: submitResults,
  close: closeTurn,
};

export interface RunReport {
  results: ToolResultIn[];
  /** the results were handed to the core */
  submitted: boolean;
  /** `project.delete` ran in this turn: the dialog went with the project, the results were not sent */
  closed?: CloseReason;
  /** `busy` — a run is going; `stale` — the pending of the dialog changed under the run */
  skipped?: "busy" | "stale";
}

/** The command that deletes the open project: the dialog of the project is deleted with it (ADR 0029 решение 32). */
export const DELETE_PROJECT = "project.delete";

export const pendingStore = new Store<{ running: string | null }>({ running: null });

export class PendingRunner {
  private stopped = false;
  private current: string | null = null;

  constructor(
    private readonly registry: Pick<CommandRegistry, "execute" | "list">,
    private readonly io: PendingIo = liveIo,
  ) {}

  get active(): boolean {
    return this.current !== null;
  }

  /** The run of this pending goes on (the dialog still waits for exactly these commands). */
  private fresh(id: string): boolean {
    return this.io.chat().pending?.id === id;
  }

  /** «Стоп»: the question of confirmation is declined, the commands that did not start are not run. */
  stop(): void {
    if (this.current === null) return;
    this.stopped = true;
    declineAllConfirms();
  }

  private async one(tool: PendingTool): Promise<ToolResultIn> {
    const command: Command | undefined = findByToolName(this.registry.list(), tool.name);
    if (!command) return { tool_use_id: tool.tool_use_id, ok: false, text: NO_SUCH_COMMAND };
    let r: RunResult;
    try {
      r = await this.registry.execute(command.id, tool.args ?? {}, "claude");
    } catch (e) {
      r = { ok: false, result: e instanceof Error ? e.message : String(e) };
    }
    return { tool_use_id: tool.tool_use_id, ok: r.ok, text: clipResult(r.result), title: command.title };
  }

  /** Runs the commands in order and sends the results. */
  async run(pending: Pending): Promise<RunReport> {
    if (this.current !== null) return { results: [], submitted: false, skipped: "busy" };
    this.current = pending.id;
    this.stopped = false;
    pendingStore.set({ running: pending.id });
    this.io.working(true);
    const results: ToolResultIn[] = [];
    let deleted = false;
    try {
      for (const tool of pending.tools) {
        if (!this.fresh(pending.id)) this.stopped = true;
        const r: ToolResultIn = this.stopped ? { tool_use_id: tool.tool_use_id, ok: false, text: STOPPED } : await this.one(tool);
        if (r.ok && findByToolName(this.registry.list(), tool.name)?.id === DELETE_PROJECT) deleted = true;
        results.push(r);
        this.io.mark(tool.item_id, statusOf(r), r.text);
      }
    } finally {
      this.current = null;
      pendingStore.set({ running: null });
    }
    if (!this.fresh(pending.id)) return { results, submitted: false, skipped: "stale" };
    if (deleted) {
      this.io.close("deleted");
      return { results, submitted: false, closed: "deleted" };
    }
    const sent = this.io.submit(results);
    if (!sent) {
      this.io.working(false);
      return { results, submitted: false };
    }
    return { results, submitted: true };
  }

  /** «Отклонить» on the cards of a dialog opened with `pending`: nothing runs, the model is told so. */
  decline(pending: Pending): RunReport {
    const results: ToolResultIn[] = pending.tools.map((t) => ({ tool_use_id: t.tool_use_id, ok: false, text: DECLINED }));
    pending.tools.forEach((t) => this.io.mark(t.item_id, "declined", DECLINED));
    if (!this.fresh(pending.id)) return { results, submitted: false, skipped: "stale" };
    return { results, submitted: this.io.submit(results) !== null };
  }
}

// ---- the runner of the application (set by `bind.ts`) ----

let runner: PendingRunner | null = null;
export function setPendingRunner(r: PendingRunner | null): void {
  runner = r;
}

/** A commands run goes on now (the buttons are hidden, the tail shows «Стоп»). */
export const pendingActive = (): boolean => runner?.active ?? false;

/** `assistant.stop`, «Стоп» of the tail, the closed panel. */
export function stopPending(): void {
  runner?.stop();
}

/** The first card that still waits for the window: the buttons «Выполнить» / «Отклонить» (they act on the whole `pending`) are drawn on it only. */
export function buttonsItemId(chat: Pick<ChatState, "pending" | "items" | "status">, active: boolean): string | null {
  if (!chat.pending || active || chat.status !== "pending") return null;
  for (const t of chat.pending.tools) {
    const item = chat.items.find((i) => i.id === t.item_id);
    if (item && item.kind === "tool" && item.status === "waiting") return item.id;
  }
  return null;
}

/** «Выполнить» of a restored card: the commands run in order (confirmations on the cards), then the turn goes on. */
export async function runPendingCards(): Promise<RunReport | null> {
  const pending = chatStore.get().pending;
  return runner && pending ? runner.run(pending) : null;
}

/** «Отклонить» of a restored card. */
export function declinePendingCards(): RunReport | null {
  const pending = chatStore.get().pending;
  return runner && pending ? runner.decline(pending) : null;
}
