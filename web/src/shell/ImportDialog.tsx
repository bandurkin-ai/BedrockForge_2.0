import { useEffect, useState } from "react";
import type { Dataset } from "../api";
import { DrillholeImport } from "../ImportPage";
import { Icon } from "./icons";
import { LogButton } from "./LogButton";
import { createLog } from "./opLog";

/** The drillhole import wizard in a window: collars, survey, interval and event tables, import. */
export function ImportDialog({ projectId, sets, initialSet, onImported, onClose }: {
  projectId: number;
  sets: Dataset[];
  /** null: a new set */
  initialSet: number | null;
  onImported: (setId: number) => void;
  onClose: () => void;
}) {
  const [log] = useState(() => createLog("import", "Импорт скважин", { "Проект": projectId }));
  const close = () => {
    log.close();
    onClose();
  };

  useEffect(() => {
    const target = sets.find((d) => d.id === initialSet);
    log.open({
      "набор": target ? { id: target.id, name: target.name, counts: target.counts } : "новый набор",
      "наборы проекта": sets.map((d) => ({ id: d.id, name: d.name, counts: d.counts })),
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="modal-backdrop">
      <div className="modal drill-import" role="dialog" aria-label="Импорт скважин">
        <header className="modal-head">
          <Icon name="import" size={16} />
          <strong>Импорт скважин</strong>
          <span className="spacer" />
          <LogButton log={log} />
          <button className="icon" onClick={close} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <DrillholeImport projectId={projectId} sets={sets} initialSet={initialSet} onImported={onImported} onClose={close} log={log} />
      </div>
    </div>
  );
}
