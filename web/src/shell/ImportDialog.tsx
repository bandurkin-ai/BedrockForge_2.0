import { useEffect, useState } from "react";
import type { Dataset, ImportTarget } from "../api";
import { ImportPage } from "../ImportPage";
import { Icon } from "./icons";

/**
 * Import of drillhole tables into a set. The target is an existing set or a new one;
 * after the first file goes into a new set, the next files go into that same set.
 */
export function ImportDialog({
  projectId,
  sets,
  initialSet,
  onImported,
  onClose,
}: {
  projectId: number;
  sets: Dataset[];
  /** null: a new set */
  initialSet: number | null;
  onImported: (setId: number) => void;
  onClose: () => void;
}) {
  const [setId, setSetId] = useState<number | null>(initialSet);
  const [newName, setNewName] = useState(() => `Скважины ${sets.length + 1}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const target: ImportTarget = setId !== null ? { set_id: setId } : { project_id: projectId, set_name: newName.trim() || "Скважины" };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-label="Импорт скважин" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="import" size={16} />
          <strong>Импорт скважин</strong>
          <span className="spacer" />
          <button className="icon" onClick={onClose} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-target">
          <label>
            Набор{" "}
            <select value={setId ?? "new"} onChange={(e) => setSetId(e.target.value === "new" ? null : Number(e.target.value))} aria-label="Набор скважин">
              <option value="new">+ Новый набор</option>
              {sets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          {setId === null && (
            <label>
              Название{" "}
              <input value={newName} onChange={(e) => setNewName(e.target.value)} aria-label="Название нового набора" maxLength={255} />
            </label>
          )}
          <span className="muted small">
            {setId === null ? "Набор будет создан при сохранении первого файла." : "Повторный импорт таблицы заменяет её данные только в этом наборе."}
          </span>
        </div>
        <div className="modal-body">
          <ImportPage
            target={target}
            setHasAssays={setId !== null && (sets.find((d) => d.id === setId)?.counts?.assay ?? 0) > 0}
            onImported={(id) => {
              setSetId(id);
              onImported(id);
            }}
          />
        </div>
      </div>
    </div>
  );
}
