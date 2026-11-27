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
import { cancelTurn, getDialog, getStatus, getUsage, listDialogs } from "./api";
import { addLocalUser, applyEvents, dropPending, EMPTY_CHAT, fromDialog, setToolResult, type ChatState, type ReduceOptions } from "./reduce";
import { continueTurn, resumeTurn, startTurn, type SegmentResult, type TurnStream } from "./stream";
import type { AssistantStatus, DialogHead, Pending, ToolDefinition, ToolResultIn, ToolStatus, TurnContext, TurnEnvelope, UsageReport } from "./wireTypes";

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

export type AskResult = { ok: true } | { ok: false; message: string; hint: string };

function follow(stream: TurnStream, mine: number, live: boolean, depth = 0): Promise<SegmentResult> {
  current = stream;
  return stream.done.then(async (res) => {
    if (mine !== epoch) return res;
    if (current === stream) current = null;
    if (res.kind === "refused") {
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
    chatStore.set(fromDialog(d));
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
  void follow(stream, mine, true);
  return { ok: true };
}

/** The results of the commands of the window (`pending.ts`): the core goes on with a new segment of the same dialog. */
export function submitResults(results: ToolResultIn[]): Promise<SegmentResult> | null {
  const s = chatStore.get();
  if (!env || !s.pending || !s.dialog) return null;
  const mine = ++epoch;
  chatStore.set({ status: "running", thinking: false, lastEventId: 0 });
  const stream = continueTurn(s.dialog.id, { pending_id: s.pending.id, results, context: env.context(), tools: env.tools() }, { onEvents: onEvents(mine) });
  return follow(stream, mine, true);
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
    chatStore.set(fromDialog(d));
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

export const DIALOGS_PAGE = 50;
const DIALOGS_MAX = 200;

/** The list is read again as far as it was shown (pages of 50 already loaded stay); an error keeps the rows read before (the history is read without the network). */
export async function refreshDialogs(): Promise<void> {
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

/** `assistant.connection` and `assistant.dialogs` of the bus (§1) read the status and the list again. */
export function startAssistantEvents(): () => void {
  return onCoreEvent((event) => {
    if (event === "assistant.connection") void refreshStatus();
    else if (event === "assistant.dialogs") void refreshDialogs();
  });
}
