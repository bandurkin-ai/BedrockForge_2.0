import { useEffect, useState } from "react";
import { getTable, type Summary, type TablePage } from "./api";
import type { TableKind } from "./route";
import { PreviewTable } from "./Table";

const KINDS: [TableKind, string][] = [
  ["collar", "Устья (Collar)"],
  ["survey", "Инклинометрия (Survey)"],
  ["assay", "Пробы (Assay)"],
];
const PAGE = 50;

export function DataPage({
  setId,
  refreshKey,
  kind,
  holeId,
  summary,
  onKindChange,
  onClearHole,
}: {
  setId: number;
  refreshKey: number;
  kind: TableKind;
  holeId: string | null;
  summary: Summary | null;
  onKindChange: (kind: TableKind) => void;
  onClearHole: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<TablePage | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A different table, hole or project: start from the first page and drop the old rows
  // so they are never shown under the new header while the request is in flight.
  useEffect(() => {
    setOffset(0);
    setPage(null);
  }, [kind, holeId, setId]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getTable(setId, kind, PAGE, offset, holeId)
      .then((p) => !cancelled && setPage(p))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [setId, kind, offset, holeId, refreshKey]);

  const total = page?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE, total);

  return (
    <main className="page">
      {error && <div className="alert error">{error}</div>}

      <div className="tabs" role="tablist" aria-label="Таблица данных">
        {KINDS.map(([k, label]) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === kind}
            className={k === kind ? "tab active" : "tab"}
            onClick={() => onKindChange(k)}
          >
            {label}
            {summary && <span className="muted"> · {summary.counts[k].toLocaleString("ru-RU")}</span>}
          </button>
        ))}
      </div>

      {holeId && (
        <p className="filter-chip">
          Скважина: <strong>{holeId}</strong>
          <button className="icon" onClick={onClearHole} aria-label="Показать все скважины" title="Показать все скважины">✕</button>
        </p>
      )}

      {page && page.total === 0 && (
        <p className="muted">
          {holeId ? "Для этой скважины в таблице нет строк." : "В этой таблице пока нет данных. Импортируйте файл на вкладке «Импорт»."}
        </p>
      )}
      {page && page.total > 0 && (
        <>
          <PreviewTable columns={page.columns} rows={page.rows} />
          <p className="pager">
            <button onClick={() => setOffset(Math.max(0, offset - PAGE))} disabled={offset === 0}>← Назад</button>
            <span>
              {from.toLocaleString("ru-RU")}–{to.toLocaleString("ru-RU")} из {total.toLocaleString("ru-RU")}
            </span>
            <button onClick={() => setOffset(offset + PAGE)} disabled={to >= total}>Вперёд →</button>
          </p>
        </>
      )}

      {summary && summary.imports.length > 0 && (
        <section>
          <h2>История импорта</h2>
          <ul className="issues">
            {summary.imports.map((j) => (
              <li key={j.id}>
                {new Date(j.created_at).toLocaleString("ru-RU")} · {j.kind} · {j.filename} · строк: {j.row_count.toLocaleString("ru-RU")}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
