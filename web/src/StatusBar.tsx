import { useEffect, useState } from "react";
import { getHealth, type AssistantStatus, type AuditRun } from "./api";

export function StatusBar({
  projectName,
  holeCount,
  setName,
  selectedHole,
  audit,
  mode,
  assistant,
  remote,
  onAssistant,
}: {
  projectName: string;
  holeCount: number;
  selectedHole: string | null;
  setName: string | null;
  audit: AuditRun | null;
  mode: string;
  assistant: AssistantStatus | null;
  /** The browser is registered with the server and can take commands from MCP clients. */
  remote: boolean;
  /** opens the window of the Claude connection */
  onAssistant?: () => void;
}) {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      getHealth()
        .then(() => !cancelled && setOnline(true))
        .catch(() => !cancelled && setOnline(false));
    check();
    const timer = window.setInterval(check, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <footer className="statusbar">
      <span className={`status-dot ${online === null ? "" : online ? "ok" : "bad"}`} aria-hidden="true" />
      <span>{online === null ? "Связь с сервером…" : online ? "Сервер доступен" : "Нет связи с сервером"}</span>
      <span className="sep">·</span>
      <span>Проект: {projectName}</span>
      <span className="sep">·</span>
      <span>Набор: {setName ?? "—"}{setName && ` (${holeCount.toLocaleString("ru-RU")} скв.)`}</span>
      {selectedHole && (
        <>
          <span className="sep">·</span>
          <span>Выбрана: <strong>{selectedHole}</strong></span>
        </>
      )}
      <span className="sep">·</span>
      <span>Вид: {mode}</span>
      <span className="spacer" />
      <button className="statusbar-link" onClick={onAssistant} title="Подключение Claude: вход, модель, расход">
        Claude: {assistant === null ? "…" : !assistant.configured ? "не подключён" : assistant.mode === "account" ? "аккаунт" : assistant.mode === "key" ? "ключ" : "подключён"}
        {assistant?.configured && assistant.models ? ` · ${assistant.models.find((m) => m.id === assistant.model)?.title.replace("Claude ", "") ?? assistant.model}` : ""}
      </button>
      <span className="sep">·</span>
      <span title="Внешние клиенты Claude управляют этим окном через MCP-сервер /mcp">MCP: {remote ? "готов" : "нет связи"}</span>
      <span className="sep">·</span>
      {audit ? (
        <span>
          Аудит: {audit.by_severity.error} / {audit.by_severity.warning} / {audit.by_severity.info}
          {audit.stale && " (устарел)"}
        </span>
      ) : (
        <span>Аудит не запускался</span>
      )}
    </footer>
  );
}
