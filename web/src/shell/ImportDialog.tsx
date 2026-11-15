import { useEffect } from "react";
import type { Dataset } from "../api";
import { DrillholeImport } from "../ImportPage";
import { Icon } from "./icons";

/** The drillhole import wizard in a window: collars, survey, interval and event tables, import. */
export function ImportDialog({ projectId, sets, initialSet, onImported, onClose }: {
  projectId: number;
  sets: Dataset[];
  /** null: a new set */
  initialSet: number | null;
  onImported: (setId: number) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop">
      <div className="modal drill-import" role="dialog" aria-label="Импорт скважин">
        <header className="modal-head">
          <Icon name="import" size={16} />
          <strong>Импорт скважин</strong>
          <span className="spacer" />
          <button className="icon" onClick={onClose} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <DrillholeImport projectId={projectId} sets={sets} initialSet={initialSet} onImported={onImported} onClose={onClose} />
      </div>
    </div>
  );
}
