import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteDataRow, getDataTable, getDataTableStats, insertDataRow, listDataEdits, listDataTables, undoDataEdit, updateDataRow,
  type DataColumn, type DataEdit, type DataRow, type DataTableInfo, type DataTablePage, type FieldStats,
} from "./api";
import { askConfirm } from "./shell/dialogs";
import { Icon } from "./shell/icons";

const PAGE_SIZES = [100, 200, 500];
const KIND_TITLES: Record<DataTableInfo["kind"], string> = { collar: "устья", survey: "замеры", assay: "пробы", interval: "интервалы", event: "события" };

export function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("ru-RU", { useGrouping: false }) : String(Number(v.toPrecision(8))).replace(".", ",");
  return String(v);
}
const editText = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "number" ? String(v).replace(".", ",") : String(v));
const statNum = (v: number | null | undefined) => (v === null || v === undefined ? "—" : formatValue(Number(v.toPrecision(6))));

/**
 * Every table of the active drillhole set in the bottom panel: collars, surveys, assays, interval and event
 * tables. Columns sort by a click, «Статистика» shows every field, «Правка» lets rows be corrected by hand
 * (each change goes to the edit log of the set and can be undone).
 */
export function DataTables({ setId, refreshKey, tableKey, holeId, onTableChange, onClearHole, onSelectHole, onChanged }: {
  setId: number;
  refreshKey: number;
  tableKey: string;
  holeId: string | null;
  onTableChange: (key: string) => void;
  onClearHole: () => void;
  onSelectHole: (holeId: string) => void;
  /** data changed: the 3D view, audit and other panels reload */
  onChanged: () => void;
}) {
  const [tables, setTables] = useState<DataTableInfo[] | null>(null);
  const [page, setPage] = useState<DataTablePage | null>(null);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [sort, setSort] = useState<{ field: string; desc: boolean } | null>(null);
  const [showStats, setShowStats] = useState(false);
  const [stats, setStats] = useState<FieldStats[] | null>(null);
  const [focusField, setFocusField] = useState<string | null>(null);
  const [edit, setEdit] = useState(false);
  const [cell, setCell] = useState<{ row: number; field: string; text: string } | null>(null);
  const [adding, setAdding] = useState<Record<string, string> | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [edits, setEdits] = useState<DataEdit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const saving = useRef(false);

  const key = tables && !tables.some((t) => t.key === tableKey) ? tables[0]?.key ?? "collar" : tableKey;
  const current = tables?.find((t) => t.key === key);

  useEffect(() => {
    listDataTables(setId).then(setTables).catch((e: Error) => setError(e.message));
  }, [setId, refreshKey, version]);

  // Another table, hole or set: from the first page, without the old sort.
  useEffect(() => {
    setOffset(0);
    setPage(null);
    setCell(null);
    setAdding(null);
  }, [key, holeId, setId]);
  useEffect(() => {
    setSort(null);
    setFocusField(null);
  }, [key, setId]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getDataTable(setId, key, { offset, limit, sort: sort?.field, desc: sort?.desc, holeId })
      .then((p) => !cancelled && setPage(p))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [setId, key, offset, limit, sort, holeId, refreshKey, version]);

  useEffect(() => {
    if (!showStats) return;
    let cancelled = false;
    setStats(null);
    getDataTableStats(setId, key, holeId).then((s) => !cancelled && setStats(s.fields)).catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [showStats, setId, key, holeId, refreshKey, version]);

  useEffect(() => {
    if (showLog) listDataEdits(setId).then(setEdits).catch(() => setEdits([]));
  }, [showLog, setId, version]);

  const changed = useCallback((text: string) => {
    setNote(text);
    setVersion((v) => v + 1);
    onChanged();
  }, [onChanged]);

  const cycleSort = (field: string) => {
    setOffset(0);
    setSort((s) => (!s || s.field !== field ? { field, desc: false } : !s.desc ? { field, desc: true } : null));
    setFocusField(field);
  };

  const saveCell = async (moveTo?: { row: number; field: string } | null) => {
    if (!cell || !page || saving.current) return;
    const row = page.rows.find((r) => r._id === cell.row);
    if (!row) return setCell(null);
    if (editText(row[cell.field]) === cell.text) {
      setCell(moveTo ? { ...moveTo, text: editText(page.rows.find((r) => r._id === moveTo.row)?.[moveTo.field]) } : null);
      return;
    }
    saving.current = true;
    try {
      const r = await updateDataRow(setId, key, cell.row, { [cell.field]: cell.text });
      setPage((p) => (p ? { ...p, rows: p.rows.map((x) => (x._id === cell.row ? r.row : x)) } : p));
      setError(null);
      setCell(moveTo ? { ...moveTo, text: editText(page.rows.find((x) => x._id === moveTo.row)?.[moveTo.field]) } : null);
      changed(`Сохранено: ${row.hole_id ?? ""} · ${cell.field}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      saving.current = false;
    }
  };

  const columns = page?.columns ?? [];
  const nextCell = (rowId: number, field: string, back: boolean) => {
    if (!page) return null;
    const ci = columns.findIndex((c) => c.name === field);
    const ri = page.rows.findIndex((r) => r._id === rowId);
    let c = ci + (back ? -1 : 1);
    let r = ri;
    if (c >= columns.length) { c = 0; r += 1; }
    if (c < 0) { c = columns.length - 1; r -= 1; }
    const row = page.rows[r];
    return row ? { row: row._id, field: columns[c].name } : null;
  };

  const removeRow = async (row: DataRow) => {
    if (!(await askConfirm({ title: "Удаление строки", message: `Удалить строку ${row.hole_id ?? ""}${row.from !== undefined ? ` ${formatValue(row.from)}–${formatValue(row.to)} м` : row.depth !== undefined ? ` ${formatValue(row.depth)} м` : ""} из таблицы «${current?.name ?? ""}»? Её можно вернуть из журнала правок.`, danger: true }))) return;
    try {
      await deleteDataRow(setId, key, row._id);
      changed(`Строка удалена: ${row.hole_id ?? ""}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const addRow = async () => {
    if (!adding) return;
    const values = Object.fromEntries(Object.entries(adding).filter(([, v]) => v.trim() !== ""));
    try {
      await insertDataRow(setId, key, values);
      setAdding(null);
      changed(`Строка добавлена: ${values.hole_id ?? ""}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const undo = async (e: DataEdit) => {
    try {
      await undoDataEdit(e.id);
      changed("Правка отменена");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const total = page?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <div className="data-tables">
      <div className="dt-tabs" role="tablist" aria-label="Таблицы набора">
        {(tables ?? []).map((t) => (
          <button key={t.key} role="tab" aria-selected={t.key === key} className={t.key === key ? "dt-tab active" : "dt-tab"}
            title={`${t.name}: ${KIND_TITLES[t.kind]}, строк ${t.rows.toLocaleString("ru-RU")}`} onClick={() => onTableChange(t.key)}>
            {t.name}<span className="muted"> · {t.rows.toLocaleString("ru-RU")}</span>
          </button>
        ))}
      </div>

      <div className="dt-toolbar">
        {holeId && (
          <span className="filter-chip">
            Скважина <strong>{holeId}</strong>
            <button className="icon" onClick={onClearHole} aria-label="Показать все скважины" title="Показать все скважины">✕</button>
          </span>
        )}
        <span className="muted small">{total === 0 ? "Нет строк" : `${from.toLocaleString("ru-RU")}–${to.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")}`}</span>
        <button className="small-btn" onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0}>← Назад</button>
        <button className="small-btn" onClick={() => setOffset(offset + limit)} disabled={to >= total}>Вперёд →</button>
        <select value={limit} aria-label="Строк на странице" title="Строк на странице" onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} строк</option>)}
        </select>
        {sort && <span className="muted small">сортировка: {sort.field} {sort.desc ? "↓" : "↑"} <button className="link" onClick={() => setSort(null)}>сбросить</button></span>}
        <span className="spacer" />
        <button className={`small-btn${showStats ? " active" : ""}`} aria-pressed={showStats} onClick={() => setShowStats((v) => !v)} title="Статистика по всем полям таблицы (с учётом выбранной скважины)">Σ Статистика</button>
        <button className={`small-btn${edit ? " active warn" : ""}`} aria-pressed={edit} onClick={() => { setEdit((v) => !v); setCell(null); setAdding(null); }}
          title="Режим ручной корректировки: щелчок по ячейке — правка, Enter — сохранить, Esc — отмена">
          <Icon name="edit" size={13} /> {edit ? "Правка включена" : "Правка"}
        </button>
        {edit && (
          <button className="small-btn" disabled={!!adding} onClick={() => setAdding(Object.fromEntries(columns.map((c) => [c.name, c.name === "hole_id" && holeId ? holeId : ""])))}>+ Строка</button>
        )}
        <button className={`small-btn${showLog ? " active" : ""}`} aria-pressed={showLog} onClick={() => setShowLog((v) => !v)} title="Журнал ручных правок набора, с отменой">Журнал правок</button>
      </div>

      {error && <div className="alert error small dt-msg">{error}<button className="icon" aria-label="Скрыть" onClick={() => setError(null)}>✕</button></div>}
      {note && !error && <div className="muted small dt-msg">{note}</div>}

      {showLog && (
        <div className="dt-log">
          {edits.length === 0 && <span className="muted small">Правок ещё не было.</span>}
          {edits.map((e) => (
            <div key={e.id} className={`dt-log-row${e.undone ? " undone" : ""}`}>
              <span className="muted small">{e.created_at ? new Date(e.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</span>
              <span className="small"><b>{e.table_name}</b> · {e.hole_id} · {e.action === "update" ? "изменение" : e.action === "insert" ? "новая строка" : "удаление"}</span>
              <span className="small grow dt-log-changes">
                {e.action === "update" ? Object.entries(e.changes).map(([f, [a, b]]) => `${f}: ${formatValue(a)} → ${formatValue(b)}`).join("; ")
                  : Object.entries(e.changes).slice(0, 6).map(([f, [a, b]]) => `${f}=${formatValue(e.action === "insert" ? b : a)}`).join(", ")}
              </span>
              {e.undone ? <span className="muted small">отменена</span> : <button className="small-btn" onClick={() => void undo(e)}>Отменить</button>}
            </div>
          ))}
        </div>
      )}

      {showStats && (
        <div className="dt-stats">
          {!stats ? <span className="muted small">Считаю…</span> : (
            <table>
              <thead>
                <tr><th>Поле</th><th>Тип</th><th className="num">Заполнено</th><th className="num">Пусто</th><th className="num">Мин</th><th className="num">Макс</th>
                  <th className="num">Среднее</th><th className="num">Медиана</th><th className="num">Ст. откл.</th><th className="num">Уникальных</th><th>Частые значения</th></tr>
              </thead>
              <tbody>
                {stats.map((f) => (
                  <tr key={f.name} className={f.name === focusField ? "focus" : ""} onClick={() => setFocusField(f.name)}>
                    <td><b>{f.name}</b>{f.unit ? <span className="muted">, {f.unit}</span> : null}</td>
                    <td className="muted">{f.kind === "number" ? "число" : "текст"}</td>
                    <td className="num">{f.count.toLocaleString("ru-RU")}</td>
                    <td className={`num${f.empty ? " dt-warn" : ""}`}>{f.empty.toLocaleString("ru-RU")}</td>
                    <td className="num">{f.kind === "number" ? statNum(f.min) : ""}</td>
                    <td className="num">{f.kind === "number" ? statNum(f.max) : ""}</td>
                    <td className="num">{f.kind === "number" ? statNum(f.mean) : ""}</td>
                    <td className="num">{f.kind === "number" ? statNum(f.median) : ""}</td>
                    <td className="num">{f.kind === "number" ? statNum(f.std) : ""}</td>
                    <td className="num">{f.kind === "text" ? (f.unique ?? 0).toLocaleString("ru-RU") : ""}</td>
                    <td className="dt-top">{f.kind === "text" ? (f.top ?? []).map(([v, n]) => `${v} (${n})`).join(", ") : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {page && (
        <div className="table-wrap dt-table">
          <table>
            <thead>
              <tr>
                {edit && <th className="dt-act" />}
                {columns.map((c) => (
                  <th key={c.name} className={c.kind === "number" ? "num" : ""} aria-sort={sort?.field === c.name ? (sort.desc ? "descending" : "ascending") : "none"}>
                    <button className="dt-head" onClick={() => cycleSort(c.name)} title={`${c.name}${c.unit ? `, ${c.unit}` : ""}: щелчок — сортировка (по возрастанию, по убыванию, снять)`}>
                      {c.name}{c.unit ? <span className="muted"> {c.unit}</span> : null}
                      <span className="dt-sort">{sort?.field === c.name ? (sort.desc ? "▼" : "▲") : ""}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {adding && (
                <tr className="dt-new">
                  {edit && <td className="dt-act">
                    <button className="small-btn primary" onClick={() => void addRow()} title="Сохранить строку">✓</button>
                    <button className="icon" onClick={() => setAdding(null)} title="Отмена" aria-label="Отменить новую строку">✕</button>
                  </td>}
                  {columns.map((c) => (
                    <td key={c.name}>
                      <input value={adding[c.name] ?? ""} placeholder={c.required ? "обязательно" : ""} aria-label={`Новая строка: ${c.name}`}
                        className={c.kind === "number" ? "num" : ""} onChange={(e) => setAdding({ ...adding, [c.name]: e.target.value })}
                        onKeyDown={(e) => { if (e.key === "Enter") void addRow(); if (e.key === "Escape") setAdding(null); }} />
                    </td>
                  ))}
                </tr>
              )}
              {page.rows.map((row) => (
                <tr key={row._id} className={!edit && holeId === null && row.hole_id ? "dt-click" : ""}
                  onClick={() => !edit && typeof row.hole_id === "string" && row.hole_id !== holeId && onSelectHole(row.hole_id)}
                  title={!edit ? "Щелчок — выбрать скважину" : undefined}>
                  {edit && (
                    <td className="dt-act">
                      <button className="icon" title="Удалить строку" aria-label={`Удалить строку ${row.hole_id}`} onClick={() => void removeRow(row)}><Icon name="trash" size={13} /></button>
                    </td>
                  )}
                  {columns.map((c: DataColumn) => {
                    const editing = cell && cell.row === row._id && cell.field === c.name;
                    return (
                      <td key={c.name} className={`${c.kind === "number" ? "num" : ""}${edit ? " dt-editable" : ""}${c.name === focusField ? " dt-focus" : ""}`}
                        onClick={(e) => { if (!edit) return; e.stopPropagation(); if (!editing) { void saveCell(); setCell({ row: row._id, field: c.name, text: editText(row[c.name]) }); } }}>
                        {editing ? (
                          <input autoFocus value={cell.text} aria-label={`${c.name}, ${row.hole_id}`} className={c.kind === "number" ? "num" : ""}
                            onChange={(e) => setCell({ ...cell, text: e.target.value })}
                            onBlur={() => void saveCell()}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); void saveCell(); }
                              else if (e.key === "Escape") setCell(null);
                              else if (e.key === "Tab") { e.preventDefault(); void saveCell(nextCell(row._id, c.name, e.shiftKey)); }
                            }} />
                        ) : formatValue(row[c.name])}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {page.total === 0 && <p className="muted small dt-msg">{holeId ? "Для этой скважины в таблице нет строк." : "В таблице нет строк."}{edit ? " Добавьте строку кнопкой «+ Строка»." : ""}</p>}
        </div>
      )}
      {edit && <p className="muted small dt-msg">Режим правки: щелчок по ячейке — изменить, Enter — сохранить, Tab — следующая ячейка, Esc — отмена. Пустое значение очищает поле. Правки попадают в журнал; аудит после них помечается устаревшим.</p>}
    </div>
  );
}
