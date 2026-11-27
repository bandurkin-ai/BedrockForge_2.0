/**
 * The link «Claude: …» of the status bar (Э23, 23.17; ТЗ 7.0.5; docs/progress/e23-api.md §13) and the text/tone shared with the mark of the account
 * (`AccountBadge.tsx`). The text comes from `state`/`kind`/`model`/`online` of `GET /status` (`statusLabel`); a click opens the window «Claude: подключение»
 * (`onOpen`, the command `assistant.connect` — the shell passes it). Without a status (the core is not reached yet) the link says «не подключён».
 */
import { useEffect } from "react";
import { useStore } from "../../shell/useStore";
import { refreshStatus, statusStore } from "./state";
import type { AssistantStatus } from "./wireTypes";
import css from "./StatusLink.module.css";

export type Tone = "ok" | "warn" | "faint";

const KIND_TEXT = { account: "аккаунт", api_key: "ключ", env: "подключён" } as const;

/** «Opus 5.5» for the id of the status; an id the list does not know stays as it is. */
export function modelTitle(status: AssistantStatus): string {
  return status.models.find((m) => m.id === status.model)?.title ?? status.model;
}

/**
 * The text of the link and of the hint of the mark (§13): «Claude: не подключён», «Claude: вход…», «Claude: ключ · Opus 5.5 · нет связи». `error` — the last read of the
 * status failed and there is none to show: the core is not reached, which is not the same as «не подключён».
 */
export function statusLabel(status: AssistantStatus | null, error: string | null = null): string {
  if (!status && error) return "Claude: нет связи с ядром";
  if (!status || status.state === "none") return "Claude: не подключён";
  if (status.state === "login") return "Claude: вход…";
  const how = status.kind ? KIND_TEXT[status.kind] : "подключён";
  return `Claude: ${how} · ${modelTitle(status)}${status.online === false ? " · нет связи" : ""}`;
}

/** The colour of the mark: `--ok` — connected and the net is not known to be down, `--warn` — a login or no connection, `--text-faint` — not connected. */
export function statusTone(status: AssistantStatus | null, error: string | null = null): Tone {
  if (!status && error) return "warn";
  if (!status || status.state === "none") return "faint";
  if (status.state === "login" || status.online === false) return "warn";
  return "ok";
}

/**
 * The status of the assistant for the shell; it is read once when nobody has read it yet (the bus keeps it fresh afterwards, `bind.ts` reads it again when the
 * core comes up). `error` — the last read failed.
 */
export function useAssistantStatus(): { status: AssistantStatus | null; error: string | null } {
  const status = useStore(statusStore, (s) => s.status);
  const error = useStore(statusStore, (s) => s.error);
  useEffect(() => {
    if (statusStore.get().status === null) void refreshStatus();
  }, []);
  return { status, error };
}

export function StatusLink({ onOpen }: { onOpen: () => void }) {
  const { status, error } = useAssistantStatus();
  const label = statusLabel(status, error);
  return (
    <button type="button" className={css.link} data-testid="claude-status" data-tone={statusTone(status, error)} title="Claude: подключение, модель и расход" onClick={onOpen}>
      {label}
    </button>
  );
}
