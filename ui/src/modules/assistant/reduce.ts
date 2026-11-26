/**
 * Events of a turn → the feed (Э23; ADR 0029 решения 21–24, 33; docs/progress/e23-api.md §7, §10). Pure: `applyEvents(state, batch)` returns a new
 * state; no store, no React, no fetch.
 *
 * - the feed (`items`) is copied once per batch and only the item an event concerns is replaced, the other items keep their identity
 *   (2000 deltas of the text do not rebuild the feed: memoised views of the other items are not touched);
 * - every creation is idempotent by `id`: the replay of a segment (`GET …/stream?after=0` after the window was reloaded) does not double items;
 * - `text.delta` creates the `assistant` item at its first piece; a gap stays a gap (`cost_usd: null` is not turned into 0);
 * - `pending` makes the cards `waiting` of the `ui` commands (the title is the window's: `ReduceOptions.titleOf`, by default the command id),
 *   `turn.end {state: "pending"}` keeps `pending` for `pending.ts`, every other end clears it.
 */
import type {
  AssistantItem,
  Dialog,
  DialogHead,
  ErrorItem,
  Item,
  Pending,
  PendingTool,
  ToolItem,
  ToolStatus,
  TurnEnvelope,
  UsageCall,
  UsageDialog,
} from "./wireTypes";

export type ChatStatus = "idle" | "running" | "pending" | "error" | "cancelled";

export interface ChatState {
  dialog: DialogHead | null;
  /** «Диалог не сохранится: проект не открыт» */
  note: string | null;
  items: Item[];
  pending: Pending | null;
  status: ChatStatus;
  /** «Claude думает…» */
  thinking: boolean;
  usage: { call: UsageCall; dialog: UsageDialog } | null;
  segment: { id: string; model: string; effort: string } | null;
  /** the number of the last event of the segment taken */
  lastEventId: number;
  stopReason: string | null;
}

export const EMPTY_CHAT: ChatState = {
  dialog: null,
  note: null,
  items: [],
  pending: null,
  status: "idle",
  thinking: false,
  usage: null,
  segment: null,
  lastEventId: 0,
  stopReason: null,
};

export interface ReduceOptions {
  /** the time of an item the core did not date (default — now) */
  now?: () => string;
  /** the title of the card of a command of the window (the registry knows it); null — the command id is shown */
  titleOf?: (tool: PendingTool) => string | null;
}

const LOCAL = "local-";

/** The id of an item the window made before the core confirmed it (a question shown at once). */
export const isLocalId = (id: string): boolean => id.startsWith(LOCAL);

const nowIso = () => new Date().toISOString();

function indexOfId(items: Item[], id: string): number {
  for (let i = items.length - 1; i >= 0; i--) if (items[i].id === id) return i;
  return -1;
}

/** A state of a dialog read from the core (`GET /dialogs/{id}`); `running` means a segment may still be going on (`resumeTurn`). */
export function fromDialog(d: Dialog): ChatState {
  const status: ChatStatus = d.head.state === "idle" ? "idle" : d.head.state;
  return { ...EMPTY_CHAT, dialog: d.head, note: d.note, items: d.items.slice(), pending: d.pending, status };
}

/** The question shown before the core answers; `turn.start {user_item}` replaces it. */
export function addLocalUser(state: ChatState, text: string, localId: string, at: string = nowIso()): ChatState {
  const item: Item = { kind: "user", id: `${LOCAL}${localId}`, at, text };
  return { ...state, items: [...state.items, item], status: "running", thinking: false, stopReason: null, lastEventId: 0 };
}

/** A local change of a card (the command of the window ran, was declined or refused). */
export function setToolResult(state: ChatState, itemId: string, status: ToolStatus, summary: string): ChatState {
  const i = indexOfId(state.items, itemId);
  const it = i < 0 ? null : state.items[i];
  if (!it || it.kind !== "tool") return state;
  const items = state.items.slice();
  items[i] = { ...it, status, summary };
  return { ...state, items };
}

/** The commands of the window will not run (a new question, the dialog was left): the cards are declined, `pending` is dropped. */
export function dropPending(state: ChatState, summary = "Не выполнено"): ChatState {
  if (!state.pending) return state;
  let s = state;
  for (const t of state.pending.tools) {
    const i = indexOfId(s.items, t.item_id);
    const it = i < 0 ? null : s.items[i];
    if (it && it.kind === "tool" && it.status === "waiting") s = setToolResult(s, t.item_id, "declined", summary);
  }
  return { ...s, pending: null };
}

export function isRunning(state: ChatState): boolean {
  return state.status === "running";
}

/** Applies a batch of events in order; the feed is copied at most once. */
export function applyEvents(state: ChatState, events: readonly TurnEnvelope[], opts: ReduceOptions = {}): ChatState {
  if (events.length === 0) return state;
  const now = opts.now ?? nowIso;
  const s: ChatState = { ...state };
  let copied = false;
  const own = (): Item[] => {
    if (!copied) {
      s.items = s.items.slice();
      copied = true;
    }
    return s.items;
  };
  const put = (item: Item): void => {
    const list = own();
    const i = indexOfId(list, item.id);
    if (i >= 0) list[i] = item;
    else list.push(item);
  };
  const patch = (id: string, f: (it: Item) => Item | null): void => {
    const i = indexOfId(s.items, id);
    if (i < 0) return;
    const next = f(s.items[i]);
    if (next) own()[i] = next;
  };
  const lastAssistantIndex = (): number => {
    for (let i = s.items.length - 1; i >= 0; i--) {
      const k = s.items[i].kind;
      if (k === "assistant") return i;
      if (k === "user") return -1;
    }
    return -1;
  };

  for (const ev of events) {
    if (ev.event !== "turn.start" && ev.id !== null && ev.id <= s.lastEventId) continue;
    if (ev.id !== null) s.lastEventId = ev.id;
    switch (ev.event) {
      case "turn.start": {
        const d = ev.data;
        s.dialog = d.dialog;
        s.segment = { id: d.segment_id, model: d.model, effort: d.effort };
        s.status = "running";
        s.thinking = false;
        s.pending = null;
        s.stopReason = null;
        s.lastEventId = ev.id ?? 1;
        if (d.user_item) {
          const list = own();
          let at = -1;
          for (let i = list.length - 1; i >= 0 && at < 0; i--) if (list[i].kind === "user" && isLocalId(list[i].id)) at = i;
          if (at >= 0 && indexOfId(list, d.user_item.id) < 0) list[at] = d.user_item;
          else put(d.user_item);
        }
        break;
      }
      case "text.delta": {
        s.thinking = false;
        const { item_id: id, text } = ev.data;
        const i = indexOfId(s.items, id);
        const it = i < 0 ? null : s.items[i];
        if (it && it.kind === "assistant") own()[i] = { ...it, text: it.text + text };
        else if (!it) own().push({ kind: "assistant", id, at: now(), model: s.segment?.model ?? "", text, stopped: false, interrupted: false });
        break;
      }
      case "thinking":
        s.thinking = ev.data.state === "start";
        break;
      case "tool.start":
        put(ev.data.item);
        break;
      case "tool.end": {
        const { item_id, status, summary } = ev.data;
        patch(item_id, (it) => (it.kind === "tool" ? { ...it, status, summary } : null));
        break;
      }
      case "pending": {
        s.pending = ev.data;
        for (const t of ev.data.tools) {
          if (indexOfId(s.items, t.item_id) >= 0) continue;
          const card: ToolItem = {
            kind: "tool",
            id: t.item_id,
            at: now(),
            tool_use_id: t.tool_use_id,
            name: t.name,
            side: "ui",
            title: opts.titleOf?.(t) ?? t.command,
            args: t.args,
            status: "waiting",
            summary: "",
          };
          own().push(card);
        }
        break;
      }
      case "usage": {
        s.usage = ev.data;
        if (s.dialog) s.dialog = { ...s.dialog, cost_usd: ev.data.dialog.cost_usd, tokens: ev.data.dialog.tokens, questions: ev.data.dialog.questions };
        break;
      }
      case "notice":
        put(ev.data.item);
        break;
      case "error": {
        put(ev.data.item as ErrorItem);
        if (ev.data.item.code === "assistant_interrupted") {
          const i = lastAssistantIndex();
          if (i >= 0) own()[i] = { ...(s.items[i] as AssistantItem), interrupted: true };
        }
        break;
      }
      case "turn.end": {
        const { state: end, stop_reason, dialog } = ev.data;
        s.dialog = dialog;
        s.status = end === "idle" ? "idle" : end;
        s.thinking = false;
        s.stopReason = stop_reason;
        if (end !== "pending") s.pending = null;
        if (end === "cancelled") {
          const i = lastAssistantIndex();
          if (i >= 0) own()[i] = { ...(s.items[i] as AssistantItem), stopped: true };
        }
        if (end !== "pending") {
          // a card that has no end any more: nothing spins in the feed
          for (let i = 0; i < s.items.length; i++) {
            const it = s.items[i];
            if (it.kind !== "tool" || it.status !== "waiting") continue;
            own()[i] = it.side === "data" ? { ...it, status: "error", summary: "Прервано" } : { ...it, status: "declined", summary: "Не выполнено" };
          }
        }
        break;
      }
    }
  }
  return s;
}

/** The text of the last answer of the model after the last question (the one «Копировать» takes). */
export function lastAnswerText(state: ChatState): string {
  for (let i = state.items.length - 1; i >= 0; i--) {
    const it = state.items[i];
    if (it.kind === "assistant") return it.text;
    if (it.kind === "user") return "";
  }
  return "";
}
