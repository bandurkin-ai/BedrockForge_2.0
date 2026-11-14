import { useCallback, useEffect, useRef, useState } from "react";
import { assistantChat, getAssistantStatus, type AssistantRequest, type AssistantStatus, type AssistantToolResult } from "../api";

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

/**
 * Chat with Claude through the backend. The server owns the conversation and runs the data
 * tools; interface commands come back as `pending` and are executed here, in the browser.
 */
export function useAssistant(projectId: number, deps: AssistantDeps) {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const depsRef = useRef(deps);
  depsRef.current = deps;

  useEffect(() => {
    let cancelled = false;
    getAssistantStatus()
      .then((s) => !cancelled && setStatus(s))
      .catch(() => !cancelled && setStatus({ configured: false, model: "" }));
    return () => {
      cancelled = true;
    };
  }, []);

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
          conversation.current = turn.conversation_id;
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
            push({ kind: "tool", title: outcome.title, detail: outcome.ok ? describe(call.input) : outcome.content, ok: outcome.ok });
            results.push({ tool_use_id: call.id, content: outcome.content, is_error: !outcome.ok });
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
      }
    },
    [projectId],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  const reset = useCallback(() => {
    abort.current?.abort();
    conversation.current = null;
    setItems([]);
  }, []);

  return { items, busy, status, send, stop, reset };
}
