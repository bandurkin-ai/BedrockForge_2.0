import { useEffect, useRef, useState } from "react";
import type { Command } from "./commands";
import type { ToolOutcome } from "./useAssistant";

/**
 * Bridge for external Claude clients (claude.ai, Claude Desktop) connected to the MCP server.
 * The browser registers its command list, then receives commands over server-sent events and
 * posts the results back. Nothing is executed without this tab being open.
 */
export function useRemoteCommands(
  projectId: number,
  commands: Command[],
  execute: (commandId: string, args: unknown) => Promise<ToolOutcome>,
): boolean {
  const [connected, setConnected] = useState(false);
  const executeRef = useRef(execute);
  executeRef.current = execute;

  useEffect(() => {
    const session = crypto.randomUUID();
    const base = `/api/projects/${projectId}/ui/sessions/${session}`;
    const post = (path: string, body: unknown) =>
      fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

    const source = new EventSource(`${base}/events`);
    source.addEventListener("open", () => {
      const manifest = commands
        .filter((c) => !c.uiOnly)
        .map((c) => ({ id: c.id, title: c.title, description: c.description, params: c.params ?? {}, required: c.required ?? [], confirm: !!c.confirm }));
      post("/commands", { commands: manifest })
        .then((r) => setConnected(r.ok))
        .catch(() => setConnected(false));
    });
    source.addEventListener("error", () => setConnected(false));
    source.addEventListener("command", (event) => {
      const call = JSON.parse((event as MessageEvent).data) as { call_id: string; command: string; args: unknown };
      void executeRef.current(call.command, call.args).then((outcome) =>
        post("/results", { call_id: call.call_id, content: outcome.content, ok: outcome.ok }).catch(() => undefined),
      );
    });
    return () => {
      source.close();
      setConnected(false);
    };
  }, [projectId, commands]);

  return connected;
}
