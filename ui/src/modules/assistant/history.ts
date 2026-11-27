/**
 * The history of the dialogs (Э23, 23.16; ТЗ 7.32 «История диалогов — в проекте»; ADR 0029 решения 23, 30, 31; docs/progress/e23-api.md §7). No React.
 *
 * - the rows of the list as the view needs them (`rowOf`): the date of the last question (`ru-RU`), the number of questions, the cost (a dialog with a model of an unknown price shows
 *   «—», never 0), the state (идёт / ждёт окна / ошибка), the mark of a temporary dialog;
 * - «the last dialog of the project»: when a project appears (and when the panel is mounted, and after a restart) `GET /dialogs/last` is opened — unless the user has already started
 *   something (a question, «Новый диалог», another dialog): then nothing is overwritten. A dialog opened with `pending` runs nothing (the cards wait for «Выполнить» / «Отклонить»);
 * - a change of the project: a saved dialog of the old project leaves the panel (the new project has its own last dialog), a temporary dialog goes with the user — the core writes it into
 *   the new current project at the end of its segment (ADR 0029 решение 31);
 * - the actions of a row: open, rename (`askText`), delete (`askConfirm` with the title of the dialog) — all through the commands of the registry.
 */
import type { App } from "../../shell/app";
import { askConfirm, askText } from "../../shell/dialogs";
import type { RunResult } from "../../shell/registry";
import { projectStore } from "../projects/state";
import { lastDialog } from "./api";
import { declineAllConfirms } from "./confirm";
import { stopPending } from "./pending";
import { chatEpoch, chatStore, newDialog, openDialog, panelViewStore } from "./state";
import type { DialogHead, DialogState } from "./wireTypes";

export const TITLE_MAX = 200;
export const TEMPORARY_REASON = "Диалог не сохранится: проект не открыт";

// ---- the rows ----

/** `26 нояб. 2026 г., 10:00` (the time zone of the machine; a date nobody can read is «—»). */
export function formatWhen(iso: string, timeZone?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", ...(timeZone ? { timeZone } : {}) }).format(d);
}

/** `≈ $0,2143`; a price that is not known is «—» (not 0). */
export function formatCost(cost: number | null): string {
  if (cost === null || !Number.isFinite(cost)) return "—";
  return `≈ $${cost.toFixed(cost < 1 ? 4 : 2).replace(".", ",")}`;
}

/** `1 вопрос`, `3 вопроса`, `5 вопросов`, `11 вопросов`. */
export function questionsText(n: number): string {
  const k = Math.max(0, Math.trunc(n));
  const last = k % 10;
  const tens = k % 100;
  const word = last === 1 && tens !== 11 ? "вопрос" : last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? "вопроса" : "вопросов";
  return `${k} ${word}`;
}

export const STATE_LABELS: Record<DialogState, string | null> = {
  idle: null,
  running: "идёт ответ",
  pending: "ждёт окна",
  error: "ошибка",
};

export interface HistoryRow {
  id: string;
  title: string;
  when: string;
  questions: string;
  cost: string;
  state: DialogState;
  stateLabel: string | null;
  temporary: boolean;
  current: boolean;
  /** an answer goes on: the core refuses to rename or delete such a dialog (409 `assistant_busy`) */
  busy: boolean;
}

export function rowOf(head: DialogHead, currentId: string | null, timeZone?: string): HistoryRow {
  return {
    id: head.id,
    title: head.title.trim() || "Без названия",
    when: formatWhen(head.last_at, timeZone),
    questions: questionsText(head.questions),
    cost: formatCost(head.cost_usd),
    state: head.state,
    stateLabel: STATE_LABELS[head.state],
    temporary: head.temporary,
    current: head.id === currentId,
    busy: head.state === "running",
  };
}

// ---- the last dialog of the project ----

let restoredFor: string | null | undefined;
let inFlight: string | null = null;

/** For the tests: the project of the next call is a new one. */
export function resetRestore(): void {
  restoredFor = undefined;
  inFlight = null;
}

const projectUid = (): string | null => projectStore.get().current?.uid ?? null;

/** The feed is empty and the user has not started anything: nothing to lose by opening a dialog. */
export function chatUntouched(): boolean {
  const c = chatStore.get();
  return c.dialog === null && c.items.length === 0 && c.status === "idle" && c.pending === null;
}

export type RestoreResult = "opened" | "none" | "skipped" | "failed";

/**
 * Opens the last non-empty dialog of the project (once per project). Without a project there is nothing to open (temporary dialogs live in the memory of the core). The call is idempotent:
 * the panel, the binding and a change of the project may all ask for it. The result of the core is dropped when the user has done anything meanwhile.
 */
export async function restoreLastDialog(uid: string | null = projectUid()): Promise<RestoreResult> {
  if (uid === null || restoredFor === uid || inFlight === uid) return "skipped";
  if (!chatUntouched()) {
    restoredFor = uid; // a dialog in hand (a temporary one goes to the project by itself) is not replaced
    return "skipped";
  }
  const epoch = chatEpoch();
  inFlight = uid;
  try {
    const head = await lastDialog();
    if (projectUid() !== uid) return "skipped";
    restoredFor = uid;
    if (!head || chatEpoch() !== epoch || !chatUntouched()) return "none";
    await openDialog(head.id);
    return "opened";
  } catch {
    return "failed"; // the core is not ready: the next call (the core came up, the panel was shown) tries again
  } finally {
    if (inFlight === uid) inFlight = null;
  }
}

let seenUid: string | null | undefined;

/** A project appeared, changed or was closed; the core came up: the panel follows (ADR 0029 решение 31). */
export function syncProject(app: Pick<App, "store">): void {
  const uid = projectUid();
  if (uid !== seenUid) {
    const first = seenUid === undefined;
    seenUid = uid;
    if (!first) {
      restoredFor = undefined;
      const chat = chatStore.get();
      if (chat.dialog && !chat.dialog.temporary && chat.dialog.project_uid !== uid && chat.status !== "running") {
        stopPending();
        declineAllConfirms();
        newDialog();
      }
    }
  }
  if (app.store.get().core === "online" && uid !== null) void restoreLastDialog(uid);
}

let stopWatch: (() => void) | null = null;

/** The panel follows the project of the window; one watch at a time (the state of the project it has seen is the module's). */
export function startHistoryWatch(app: Pick<App, "store">): () => void {
  stopWatch?.();
  seenUid = undefined;
  syncProject(app);
  const offs = [app.store.subscribe(() => syncProject(app)), projectStore.subscribe(() => syncProject(app))];
  const stop = (): void => {
    for (const off of offs) off();
    seenUid = undefined;
    if (stopWatch === stop) stopWatch = null;
  };
  stopWatch = stop;
  return stop;
}

// ---- the actions of a row ----

const fail = (text: string): void => panelViewStore.set({ error: text });

async function runCommand(app: Pick<App, "registry">, id: string, args: Record<string, unknown>): Promise<RunResult> {
  panelViewStore.set({ error: null });
  const r = await app.registry.execute(id, args, "user");
  if (!r.ok) fail(r.result);
  return r;
}

export const openFlow = (app: Pick<App, "registry">, head: Pick<DialogHead, "id">): Promise<RunResult> => runCommand(app, "assistant.open", { dialog_id: head.id });

/** The name is asked in the own window of the application; an empty or the same name changes nothing. */
export async function renameFlow(app: Pick<App, "registry">, head: Pick<DialogHead, "id" | "title">): Promise<RunResult | null> {
  const asked = await askText({ title: "Переименовать диалог", label: "Название", initial: head.title, okLabel: "Переименовать" });
  const title = asked?.trim() ?? "";
  if (!title || title === head.title) return null;
  if ([...title].length > TITLE_MAX) {
    fail(`Название длиннее ${TITLE_MAX} знаков`);
    return null;
  }
  return runCommand(app, "assistant.rename", { dialog_id: head.id, title });
}

/** The confirmation is the own window of the application with the title of the dialog; a refusal leaves the dialog whole (`assistant.delete` has none of its own). */
export async function deleteFlow(app: Pick<App, "registry">, head: Pick<DialogHead, "id" | "title" | "temporary">): Promise<RunResult | null> {
  const ok = await askConfirm({
    title: "Удалить диалог",
    message: head.temporary
      ? `Удалить временный диалог «${head.title}»? Он не сохранён в проекте и пропадёт безвозвратно.`
      : `Удалить диалог «${head.title}»? Он будет удалён из проекта без возможности восстановления.`,
    okLabel: "Удалить",
    danger: true,
  });
  if (!ok) return null;
  return runCommand(app, "assistant.delete", { dialog_id: head.id });
}
