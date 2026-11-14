import { useEffect, useState } from "react";
import { getHealth, type AuditRun } from "./api";

export function StatusBar({
  projectName,
  holeCount,
  selectedHole,
  audit,
}: {
  projectName: string;
  holeCount: number;
  selectedHole: string | null;
  audit: AuditRun | null;
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
      <span>Скважин: {holeCount.toLocaleString("ru-RU")}</span>
      {selectedHole && (
        <>
          <span className="sep">·</span>
          <span>Выбрана: <strong>{selectedHole}</strong></span>
        </>
      )}
      <span className="spacer" />
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
