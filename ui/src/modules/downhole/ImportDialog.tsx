import { useState } from "react";
import { importStyles, type StyleImportResult } from "./library";
import { LEVEL_TITLES, type StyleLevel } from "../../viz/downhole/styles";

type Decision = "replace" | "keep_both" | "skip";

/** A style file read for import: its name, content and the «level:name» already in the library (from the core). */
export interface StyleFileToImport {
  name: string;
  file: unknown;
  conflicts: string[];
  count: number;
}

const split = (key: string): { level: StyleLevel; name: string } => {
  const i = key.indexOf(":");
  return { level: key.slice(0, i) as StyleLevel, name: key.slice(i + 1) };
};

/** Import of a style file: the conflicts by name with a decision for each, then a summary. */
export function ImportDialog({ source, onDone, onClose }: {
  source: StyleFileToImport;
  onDone?: (result: StyleImportResult) => void;
  onClose: () => void;
}) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() => Object.fromEntries(source.conflicts.map((k) => [k, "keep_both" as Decision])));
  const [result, setResult] = useState<StyleImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="dh-dialog dh-root" role="dialog" aria-label="Импорт стилей">
      <div className="dh-usage-head"><strong className="grow">Импорт: {source.name}</strong><button type="button" className="icon" aria-label="Закрыть импорт" onClick={onClose}>✕</button></div>
      <span className="muted small">В файле стилей: {source.count}; совпадают по названию с библиотекой: {source.conflicts.length}.</span>
      {source.conflicts.length > 0 && !result && (
        <table className="dh-conflicts">
          <thead><tr><th>Стиль</th><th>Что сделать</th></tr></thead>
          <tbody>
            {source.conflicts.map((k) => {
              const c = split(k);
              return (
                <tr key={k}>
                  <td>{c.name} <span className="muted small">{LEVEL_TITLES[c.level] ?? c.level}</span></td>
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
        <div className="small" role="status">
          Добавлено: {result.created}, заменено: {result.replaced}, пропущено: {result.skipped}.
          {result.renamed.length > 0 && <div className="muted">Переименованы: {result.renamed.map((r) => `«${r.from}» → «${r.to}»`).join(", ")}</div>}
        </div>
      ) : (
        <div className="dh-tools">
          <button type="button" className="primary" disabled={busy} onClick={() => {
            setBusy(true);
            setError(null);
            importStyles(source.file, decisions)
              .then((r) => { setResult(r); onDone?.(r); })
              .catch((e: Error) => setError(e.message))
              .finally(() => setBusy(false));
          }}>{busy ? "Импорт…" : "Импортировать"}</button>
          {error && <span className="small form-error" role="alert">{error}</span>}
        </div>
      )}
    </div>
  );
}
