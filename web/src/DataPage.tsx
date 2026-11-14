import { useEffect, useState } from "react";
import { getSummary, getTable, type Summary, type TablePage } from "./api";
import { PreviewTable } from "./Table";

const KINDS: [string, string][] = [
  ["collar", "Устья (Collar)"],
  ["survey", "Инклинометрия (Survey)"],
  ["assay", "Пробы (Assay)"],
];
const PAGE = 50;

export function DataPage({ projectId, refreshKey }: { projectId: number; refreshKey: number }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [kind, setKind] = useState("collar");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<TablePage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSummary(projectId).then(setSummary).catch((e: Error) => setError(e.message));
  }, [projectId, refreshKey]);

  useEffect(() => {
    setPage(null);
    getTable(projectId, kind, PAGE, offset)
      .then(setPage)
      .catch((e: Error) => setError(e.message));
  }, [projectId, kind, offset, refreshKey]);

  const total = page?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE, total);

  return (
    <main className="page">
      <h1>Данные проекта</h1>
      {error && <div className="alert error">{error}</div>}

      {summary && (
        <section className="cards">
          {KINDS.map(([k, label]) => (
            <div key={k} className="card">
              <div className="muted">{label}</div>
              <div className="big">{summary.counts[k].toLocaleString("ru-RU")}</div>
            </div>
          ))}
          <div className="card">
            <div className="muted">Компоненты</div>
            <div>
              {summary.components.length === 0
                ? "—"
                : summary.components.map((c) => `${c.name} (${c.unit})`).join(", ")}
            </div>
          </div>
        </section>
      )}

      <div className="tabs">
        {KINDS.map(([k, label]) => (
          <button
            key={k}
            className={k === kind ? "tab active" : "tab"}
            onClick={() => {
              setKind(k);
              setOffset(0);
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {page && page.total === 0 && <p className="muted">В этой таблице пока нет данных. Импортируйте файл на вкладке «Импорт».</p>}
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
