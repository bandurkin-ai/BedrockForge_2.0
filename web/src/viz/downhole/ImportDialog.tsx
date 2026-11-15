import { useState } from "react";
import { importStyles, type StyleImportResult } from "../../api";
import { conflictKey, fileConflicts, LEVEL_TITLES, type StyleFile, type StyleRec } from "./styles";

type Decision = "replace" | "keep_both" | "skip";

/** Import of a style file: the conflicts by name with a decision for each, then a summary. */
export function ImportDialog({ projectId, file, fileName, styles, onDone, onClose }: {
  projectId: number;
  file: StyleFile;
  fileName: string;
  styles: StyleRec[];
  onDone: (result: StyleImportResult) => void;
  onClose: () => void;
}) {
  const conflicts = fileConflicts(file, styles);
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() => Object.fromEntries(conflicts.map((c) => [conflictKey(c), "keep_both" as Decision])));
  const [result, setResult] = useState<StyleImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="dh-dialog" role="dialog" aria-label="Импорт стилей">
      <div className="dh-usage-head"><strong className="grow">Импорт: {fileName}</strong><button type="button" className="icon" aria-label="Закрыть" onClick={onClose}>✕</button></div>
      <span className="muted small">В файле стилей: {file.styles.length}; совпадают по названию с библиотекой: {conflicts.length}.</span>
      {conflicts.length > 0 && !result && (
        <table className="dh-conflicts">
          <thead><tr><th>Стиль</th><th>Что сделать</th></tr></thead>
          <tbody>
            {conflicts.map((c) => {
              const k = conflictKey(c);
              return (
                <tr key={k}>
                  <td>{c.name} <span className="muted small">{LEVEL_TITLES[c.level]}</span></td>
                  <td>
                    <select value={decisions[k]} aria-label={`Конфликт: ${c.name}`} onChange={(e) => setDecisions({ ...decisions, [k]: e.target.value as Decision })}>
                      <option value="replace">заменить</option>
                      <option value="keep_both">оставить оба</option>
                      <option value="skip">пропустить</option>
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {result ? (
        <div className="small">
          Добавлено: {result.created}, заменено: {result.replaced}, пропущено: {result.skipped}.
          {result.renamed.length > 0 && <div className="muted">Переименованы: {result.renamed.map((r) => `«${r.from}» → «${r.to}»`).join(", ")}</div>}
        </div>
      ) : (
        <div className="dh-tools">
          <button type="button" className="primary" disabled={busy} onClick={() => {
            setBusy(true);
            setError(null);
            importStyles(projectId, file, decisions)
              .then((r) => { setResult(r); onDone(r); })
              .catch((e: Error) => setError(e.message))
              .finally(() => setBusy(false));
          }}>{busy ? "Импорт…" : "Импортировать"}</button>
          {error && <span className="small form-error">{error}</span>}
        </div>
      )}
    </div>
  );
}
