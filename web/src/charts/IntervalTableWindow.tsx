import { useEffect, useState } from "react";
import { getIntervalRows, type IntervalColumn } from "../api";
import { PreviewTable } from "../Table";

const PAGE = 200;

/** Rows of an interval or event table of a drillhole set. */
export function IntervalTableWindow({ tableId, event, refreshKey }: { tableId: number; event: boolean; refreshKey: number }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ total: number; columns: IntervalColumn[]; rows: Record<string, unknown>[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    getIntervalRows(tableId, offset, PAGE).then((p) => !cancelled && setPage(p)).catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [tableId, offset, refreshKey]);
  if (error) return <div className="alert error small">{error}</div>;
  if (!page) return <p className="muted small form-note">Загрузка…</p>;
  const rows = event ? page.rows.map(({ from, to: _to, ...rest }) => ({ ...rest, depth: from })) : page.rows;
  const columns = ["hole_id", ...(event ? ["depth"] : ["from", "to"]), ...page.columns.map((c) => c.name)];
  return (
    <div className="interval-table-window">
      <div className="pager">
        <span className="muted small">{page.total === 0 ? "Нет строк" : `${offset + 1}–${Math.min(offset + PAGE, page.total)} из ${page.total.toLocaleString("ru-RU")}`}</span>
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Назад</button>
        <button disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>Вперёд</button>
      </div>
      <PreviewTable columns={columns} rows={rows} />
    </div>
  );
}
