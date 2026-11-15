import { useCallback, useEffect, useRef, useState } from "react";
import {
  assistantChat, deleteAssistantChat, getAssistantChat, getAssistantStatus, listAssistantChats, renameAssistantChat,
  type AssistantChatInfo, type AssistantRequest, type AssistantStatus, type AssistantToolResult, type AssistantUsageTotals,
} from "../api";

export type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; title: string; detail: string; ok: boolean }
  | { kind: "notice"; text: string }
  | { kind: "error"; text: string };

export interface ToolOutcome {
  title: string;
  content: string;
  ok: boolean;
}

export interface AssistantDeps {
  /** Interface commands offered to Claude as tools (Messages API tool definitions). */
  tools: () => unknown[];
  /** What the user is looking at right now; sent with every message. */
  context: () => Record<string, unknown>;
  /** Run an interface command Claude asked for. */
  execute: (name: string, input: Record<string, unknown>) => Promise<ToolOutcome>;
}

const describe = (input: Record<string, unknown>) =>
  Object.entries(input).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");

const CHAT_KEY = (projectId: number) => `bf.assistant.chat.${projectId}`;
const remember = (projectId: number, id: string | null) => {
  try {
    if (id) localStorage.setItem(CHAT_KEY(projectId), id);
    else localStorage.removeItem(CHAT_KEY(projectId));
  } catch {
    /* not remembered */
  }
};

/**
 * Chat with Claude through the backend. The server owns the conversation, keeps it in the project
 * (history of chats) and runs the data tools; interface commands come back as `pending` and are
 * executed here, in the browser.
 */
export function useAssistant(projectId: number, deps: AssistantDeps) {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [chats, setChats] = useState<AssistantChatInfo[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [usage, setUsage] = useState<AssistantUsageTotals | null>(null);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const depsRef = useRef(deps);
  depsRef.current = deps;

  const refreshStatus = useCallback(() => {
    getAssistantStatus()
      .then(setStatus)
      .catch(() => setStatus({ configured: false, model: "" }));
  }, []);
  useEffect(refreshStatus, [refreshStatus]);

  const refreshChats = useCallback(() => {
    listAssistantChats(projectId).then(setChats).catch(() => setChats([]));
  }, [projectId]);

  const select = useCallback((id: string | null) => {
    conversation.current = id;
    setChatId(id);
    remember(projectId, id);
  }, [projectId]);

  /** Opens a saved chat of the project: its messages, and where it stopped. */
  const openChat = useCallback(async (id: string) => {
    abort.current?.abort();
    const c = await getAssistantChat(id);
    select(c.id);
    setTitle(c.title);
    setUsage({ input_tokens: c.input_tokens, output_tokens: c.output_tokens, cost_usd: c.cost_usd });
    setItems(c.items as ChatItem[]);
  }, [select]);

  // A new project: its chats, and the chat that was open there last time.
  useEffect(() => {
    abort.current?.abort();
    setItems([]);
    setTitle(null);
    setUsage(null);
    conversation.current = null;
    setChatId(null);
    refreshChats();
    let last: string | null = null;
    try {
      last = localStorage.getItem(CHAT_KEY(projectId));
    } catch {
      last = null;
    }
    if (last) openChat(last).catch(() => remember(projectId, null));
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const push = (item: ChatItem) => setItems((prev) => [...prev, item]);

  const send = useCallback(
    async (text: string) => {
      const prompt = text.trim();
      if (!prompt || abort.current) return;
      const controller = new AbortController();
      abort.current = controller;
      setBusy(true);
      push({ kind: "user", text: prompt });

      let body: AssistantRequest = {
        conversation_id: conversation.current,
        text: prompt,
        context: depsRef.current.context(),
        ui_tools: depsRef.current.tools(),
      };
      try {
        for (;;) {
          const turn = await assistantChat(projectId, body, controller.signal);
          if (conversation.current !== turn.conversation_id) select(turn.conversation_id);
          setTitle(turn.title);
          setUsage(turn.usage);
          for (const block of turn.blocks) {
            if (block.type === "text") push({ kind: "assistant", text: block.text });
            else push({ kind: "tool", title: block.title, detail: describe(block.input), ok: block.ok });
          }
          if (turn.notice) push({ kind: "notice", text: turn.notice });
          if (turn.done) break;

          const results: AssistantToolResult[] = [];
          for (const call of turn.pending) {
            // After "Stop" the remaining calls are answered as cancelled, so the conversation stays valid.
            const outcome: ToolOutcome = controller.signal.aborted
              ? { title: call.name, content: "Отменено пользователем", ok: false }
              : await depsRef.current.execute(call.name, call.input);
            const detail = outcome.ok ? describe(call.input) : outcome.content;
            push({ kind: "tool", title: outcome.title, detail, ok: outcome.ok });
            results.push({ tool_use_id: call.id, content: outcome.content, is_error: !outcome.ok, title: outcome.title, detail: detail.slice(0, 2000) });
          }
          if (controller.signal.aborted) break;
          body = { conversation_id: conversation.current, tool_results: results, ui_tools: depsRef.current.tools() };
        }
      } catch (e) {
        if (controller.signal.aborted) push({ kind: "notice", text: "Остановлено." });
        else push({ kind: "error", text: (e as Error).message });
      } finally {
        abort.current = null;
        setBusy(false);
        refreshChats();
      }
    },
    [projectId, select, refreshChats],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  /** A new chat (the old one stays in the history). */
  const reset = useCallback(() => {
    abort.current?.abort();
    select(null);
    setTitle(null);
    setUsage(null);
    setItems([]);
  }, [select]);

  const renameChat = useCallback(async (id: string, name: string) => {
    await renameAssistantChat(id, name);
    if (id === conversation.current) setTitle(name);
    refreshChats();
  }, [refreshChats]);

  const deleteChat = useCallback(async (id: string) => {
    await deleteAssistantChat(id);
    if (id === conversation.current) reset();
    refreshChats();
  }, [refreshChats, reset]);

  return { items, busy, status, setStatus, refreshStatus, send, stop, reset, chats, chatId, title, usage, openChat, renameChat, deleteChat, refreshChats };
}
