/**
 * What the panel «Claude» and the windows of the assistant know (Э23; ADR 0029; docs/progress/e23-api.md): the feed of the open dialog (`chatStore`,
 * built by `reduce.ts` from the events of `stream.ts`), the status of the connection, the list of dialogs, the usage. The contract for 23.14–23.17:
 *
 * - `configureAssistant({context, tools, titleOf?})` — what a request carries: the context of the screen (§6, `context.ts`), the tools of the registry (§5.1),
 *   the titles of the cards; set once by `bind.ts`;
 * - `setPendingHandler(fn)` — `pending.ts`: called when a live segment ends with `turn.end {state: "pending"}` (a dialog opened with `pending` is not run:
 *   the cards wait for «Выполнить» / «Отклонить», ADR 0029 решение 23); it answers with `submitResults`;
 * - `ask(text)`, `submitResults(results)`, `stopTurn()`, `openDialog(id)`, `newDialog()`, `markTool(itemId, status, summary)`;
 * - `refreshStatus()`, `refreshDialogs()`, `refreshUsage()`; `startAssistantEvents()` — `assistant.connection` / `assistant.dialogs` of the bus.
 *
 * No React. The text of the questions is not written into any log.
 */
import { CoreError } from "../../shell/api";
import { onCoreEvent } from "../jobs/state";
import { Store } from "../../shell/store";
import { cancelTurn, getDialog, getStatus, getUsage, listDialogs, testConnection } from "./api";
import { addLocalUser, applyEvents, dropPending, EMPTY_CHAT, fromDialog, setToolResult, type ChatState, type ReduceOptions } from "./reduce";
import { continueTurn, resumeTurn, startTurn, type SegmentResult, type TurnStream } from "./stream";
import type { AssistantStatus, DialogHead, Item, Pending, ToolDefinition, ToolResultIn, ToolStatus, TurnContext, TurnEnvelope, UsageReport } from "./wireTypes";

export const chatStore = new Store<ChatState>(EMPTY_CHAT);
export const statusStore = new Store<{ status: AssistantStatus | null; error: string | null }>({ status: null, error: null });
export const dialogsStore = new Store<{ items: DialogHead[]; total: number; error: string | null }>({ items: [], total: 0, error: null });
export const usageStore = new Store<{ usage: UsageReport | null; error: string | null }>({ usage: null, error: null });

/** The panel «Claude» shows the chat or the history of the dialogs (23.16): `assistant.history` switches it; `error` — the last refusal of a row action. */
export type PanelView = "chat" | "history";
export const panelViewStore = new Store<{ view: PanelView; error: string | null }>({ view: "chat", error: null });
export const showChat = (): void => panelViewStore.set({ view: "chat", error: null });
export const showHistory = (): void => panelViewStore.set({ view: "history", error: null });

// ---- what the requests carry ----

export interface AssistantEnv {
  context: () => TurnContext;
  tools: () => ToolDefinition[];
  titleOf?: ReduceOptions["titleOf"];
}

let env: AssistantEnv | null = null;
export function configureAssistant(e: AssistantEnv | null): void {
  env = e;
}

export type PendingHandler = (pending: Pending, dialogId: string) => void;
let onPending: PendingHandler | null = null;
export function setPendingHandler(fn: PendingHandler | null): void {
  onPending = fn;
}

// ---- the running segment ----

let current: TurnStream | null = null;
/** a segment that was overtaken (a new dialog, a new question) writes nothing */
let epoch = 0;
let localSeq = 0;

/** Changes whenever the feed is replaced or started anew: `history.ts` tells «the user did something meanwhile» by it. */
export const chatEpoch = (): number => epoch;

const messageOf = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

function localError(code: string, message: string, hint: string): ChatState["items"][number] {
  return { kind: "error", id: `local-err-${++localSeq}`, at: new Date().toISOString(), code, message, hint };
}

function showError(code: string, message: string, hint: string): void {
  const s = chatStore.get();
  chatStore.set({ items: [...s.items, localError(code, message, hint)], status: s.status === "running" ? "error" : s.status, thinking: false });
}

const reduceOpts = (): ReduceOptions => ({ titleOf: env?.titleOf });

function onEvents(mine: number) {
  return (batch: TurnEnvelope[]) => {
    if (mine === epoch) chatStore.set(applyEvents(chatStore.get(), batch, reduceOpts()));
  };
}

/** `done` ends with the segment (the input gives the text back when the core refuses the question after the stream began). */
export type AskResult = { ok: true; done?: Promise<SegmentResult> } | { ok: false; message: string; hint: string };

/** Why a turn is closed without the answer of the model: the project of the dialog was deleted by this turn, or another project is open now. */
export type CloseReason = "deleted" | "changed";
const CLOSE_NOTICES: Record<CloseReason, { code: string; message: string }> = {
  deleted: { code: "dialog_closed", message: "Диалог закрыт вместе с проектом: ответ Claude получить нельзя." },
  changed: { code: "project_changed", message: "Проект сменился: этот диалог остался в прежнем проекте, продолжите его там." },
};

/**
 * The results of the commands cannot go back to the core (the dialog lives in the project that is gone or not open): the cards stay as they are, the feed gets
 * a notice, the chat is idle and empty of a dialog (the next question starts a new one), the list of dialogs is read again.
 */
export function closeTurn(reason: CloseReason): void {
  const s = dropPending(chatStore.get());
  const n = CLOSE_NOTICES[reason];
  const notice: Item = { kind: "notice", id: `local-notice-${++localSeq}`, at: new Date().toISOString(), code: n.code, message: n.message };
  chatStore.set({ ...s, items: [...s.items, notice], dialog: null, status: "idle", thinking: false, lastEventId: 0 });
  void refreshDialogs();
}

/** `continued` — the segment goes on a dialog after the results of the window: a dialog the core does not have in the open project closes the turn (`closeTurn`). */
function follow(stream: TurnStream, mine: number, live: boolean, depth = 0, continued = false): Promise<SegmentResult> {
  current = stream;
  return stream.done.then(async (res) => {
    if (mine !== epoch) return res;
    if (current === stream) current = null;
    if (res.kind === "refused" && continued && res.error.code === "assistant_dialog_not_found") {
      closeTurn("changed");
    } else if (res.kind === "refused") {
      const e = res.error;
      showError(e.code, e.message, e.hint);
      const s = chatStore.get();
      chatStore.set({ status: s.dialog?.state === "pending" ? "pending" : "idle", thinking: false });
    } else if (res.kind === "end") {
      void refreshDialogs();
      const s = chatStore.get();
      if (live && res.state === "pending" && s.pending && s.dialog && onPending) onPending(s.pending, s.dialog.id);
    } else if (depth < 2) {
      await recover(stream.dialogId ?? chatStore.get().dialog?.id ?? null, mine, res.kind === "broken", depth + 1);
    } else {
      chatStore.set({ status: "idle", thinking: false });
    }
    return res;
  });
}

/** The stream is lost: the dialog is read again (the core keeps the segment), and a segment that still runs is followed from its first event. */
async function recover(dialogId: string | null, mine: number, notify: boolean, depth: number): Promise<void> {
  if (!dialogId) {
    if (notify) showError("assistant_interrupted", "Связь прервалась", "Нажмите «Повторить»");
    chatStore.set({ status: "idle", thinking: false });
    return;
  }
  try {
    const d = await getDialog(dialogId);
    if (mine !== epoch) return;
    chatStore.set(fromDialog(d, reduceOpts()));
    if (d.head.state === "running") void follow(resumeTurn(dialogId, 0, { onEvents: onEvents(mine) }), mine, true, depth);
  } catch (e) {
    if (mine !== epoch) return;
    showError(e instanceof CoreError ? e.code : "core_offline", e instanceof CoreError ? e.message : "Ядро недоступно.", e instanceof CoreError ? e.hint : "Подождите перезапуска ядра.");
    chatStore.set({ status: "idle", thinking: false });
  }
}

// ---- the actions ----

/** A question: shown at once, the stream of the answer fills the feed. Resolves when the segment ends. */
export async function ask(text: string): Promise<AskResult> {
  const q = text.trim();
  if (!q) return { ok: false, message: "Введите вопрос", hint: "" };
  if (!env) return { ok: false, message: "Ассистент не настроен", hint: "Перезапустите программу." };
  const before = chatStore.get();
  if (before.status === "running") return { ok: false, message: "Ответ ещё идёт", hint: "Дождитесь конца ответа или нажмите «Стоп»." };
  const mine = ++epoch;
  // a command of the window that was not run: the core closes it itself («Не выполнено: пользователь продолжил диалог»)
  chatStore.set(addLocalUser(dropPending(before, "Не выполнено: пользователь продолжил диалог"), q, String(++localSeq)));
  const stream = startTurn({ dialog_id: before.dialog?.id ?? null, text: q, context: env.context(), tools: env.tools() }, { onEvents: onEvents(mine) });
  return { ok: true, done: follow(stream, mine, true) };
}

/** The results of the commands of the window (`pending.ts`): the core goes on with a new segment of the same dialog. */
export function submitResults(results: ToolResultIn[]): Promise<SegmentResult> | null {
  const s = chatStore.get();
  if (!env || !s.pending || !s.dialog) return null;
  const mine = ++epoch;
  chatStore.set({ status: "running", thinking: false, lastEventId: 0 });
  const stream = continueTurn(s.dialog.id, { pending_id: s.pending.id, results, context: env.context(), tools: env.tools() }, { onEvents: onEvents(mine) });
  return follow(stream, mine, true, 0, true);
}

/** A card of a command of the window changes (it ran, was declined or refused). */
export function markTool(itemId: string, status: ToolStatus, summary: string): void {
  chatStore.set(setToolResult(chatStore.get(), itemId, status, summary));
}

/** «Стоп»: the core cancels the segment, the stream ends with `turn.end {state: "cancelled"}`. */
export async function stopTurn(): Promise<void> {
  const stream = current;
  const id = chatStore.get().dialog?.id;
  if (stream) await stream.stop();
  else if (id) await cancelTurn(id).catch(() => undefined);
}

/** Opens a dialog of the list; one that is still running is followed (`GET …/stream?after=0`). */
export async function openDialog(id: string): Promise<void> {
  const mine = ++epoch;
  current?.abort();
  current = null;
  try {
    const d = await getDialog(id);
    if (mine !== epoch) return;
    chatStore.set(fromDialog(d, reduceOpts()));
    if (d.head.state === "running") void follow(resumeTurn(id, 0, { onEvents: onEvents(mine) }), mine, true);
  } catch (e) {
    if (mine !== epoch) return;
    showError(e instanceof CoreError ? e.code : "error", messageOf(e), "");
  }
}

/** A new dialog: the stream of the old one is left (the core finishes the segment), the feed is empty. */
export function newDialog(): void {
  epoch++;
  current?.abort();
  current = null;
  chatStore.set({ ...EMPTY_CHAT });
}

// ---- status, dialogs, usage ----

export async function refreshStatus(): Promise<void> {
  try {
    statusStore.set({ status: await getStatus(), error: null });
  } catch (e) {
    statusStore.set({ error: messageOf(e) });
  }
}

/**
 * «Проверить связь» of the banner «нет интернета»: the core learns whether Anthropic is reachable only from a call, so the connection is tested and the status
 * read again (a failed test is the answer too: `online` of the status tells).
 */
export async function checkConnection(): Promise<void> {
  try {
    await testConnection();
  } catch {
    // the refusal of the test is not shown here: the status read below carries what the core knows
  }
  await refreshStatus();
}

export const DIALOGS_PAGE = 50;
const DIALOGS_MAX = 200;

let dialogsFlight: Promise<void> | null = null;
let dialogsAgain = false;

/**
 * The list is read again as far as it was shown (pages of 50 already loaded stay); an error keeps the rows read before (the history is read without the network).
 * The end of a turn and `assistant.dialogs` of the bus ask for it together: the requests that come while a read goes on are one more read after it (not one each;
 * and never an answer that began before the news — a project deleted by the turn must not stay in the list).
 */
export function refreshDialogs(): Promise<void> {
  if (dialogsFlight) {
    dialogsAgain = true;
    return dialogsFlight;
  }
  const flight = (async () => {
    do {
      dialogsAgain = false;
      await readDialogs();
    } while (dialogsAgain);
  })().finally(() => {
    dialogsFlight = null;
  });
  dialogsFlight = flight;
  return flight;
}

async function readDialogs(): Promise<void> {
  try {
    const shown = Math.ceil(dialogsStore.get().items.length / DIALOGS_PAGE) * DIALOGS_PAGE;
    const r = await listDialogs(0, Math.min(DIALOGS_MAX, Math.max(DIALOGS_PAGE, shown)));
    dialogsStore.set({ items: r.items, total: r.total, error: null });
  } catch (e) {
    dialogsStore.set({ error: messageOf(e) });
  }
}

/** The next page of the list (50 rows). */
export async function loadMoreDialogs(): Promise<void> {
  const have = dialogsStore.get().items;
  try {
    const r = await listDialogs(have.length, DIALOGS_PAGE);
    const known = new Set(have.map((d) => d.id));
    dialogsStore.set({ items: [...have, ...r.items.filter((d) => !known.has(d.id))], total: r.total, error: null });
  } catch (e) {
    dialogsStore.set({ error: messageOf(e) });
  }
}

export async function refreshUsage(): Promise<void> {
  try {
    usageStore.set({ usage: await getUsage(), error: null });
  } catch (e) {
    usageStore.set({ error: messageOf(e) });
  }
}

let eventsStop: (() => void) | null = null;
let eventsUsers = 0;

/**
 * `assistant.connection` and `assistant.dialogs` of the bus (§1) read the status and the list again. There is one subscription however many callers ask
 * (`bind.ts` for the life of the window, the connection window while it is open); it ends with the last of them.
 */
export function startAssistantEvents(): () => void {
  if (eventsUsers++ === 0) {
    eventsStop = onCoreEvent((event) => {
      if (event === "assistant.connection") void refreshStatus();
      else if (event === "assistant.dialogs") void refreshDialogs();
    });
  }
  let live = true;
  return () => {
    if (!live) return;
    live = false;
    if (--eventsUsers === 0) {
      eventsStop?.();
      eventsStop = null;
    }
  };
}
