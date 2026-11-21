import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { EditLog } from "./EditLog";
import { TabMark } from "../composites/ResultsTree";
import { cellText, deleteRow, editCell, editText, fieldStats, findRow, filterHole, loadPage, openTable, PAGE, rowAt, rowsLocked, selectSet, setWhere, sortBy, tablesStore, type ColumnInfo, type FieldStats, type Row } from "./state";
import { ToolBar } from "./ToolBar";
import css from "./Tables.module.css";

const ROW_H = 24;

/** Нижняя панель «Таблица» (ТЗ 7.10): tabs of the active set, pages on demand, sort, hole, Σ, edits. */
export function TablePanel() {
  const app = useApp();
  const s = useStore(tablesStore, (x) => x);
  const project = useStore(app.store, (x) => x.projectName);
  const [showLog, setShowLog] = useState(false);
  const [where, setWhereText] = useState(s.where);
  useEffect(() => setWhereText(s.where), [s.where]);

  if (!project) return <div className={css.empty}>Откройте или создайте проект.</div>;
  if (!s.sets.length)
    return (
      <div className={css.empty}>
        В проекте нет наборов скважин.{" "}
        <button type="button" className={css.link} onClick={() => void app.run("import.open")}>
          Импорт скважин…
        </button>
      </div>
    );
  return (
    <div className={css.panel}>
      <div className={css.top}>
        <select className={css.setPick} value={s.setId ?? ""} onChange={(e) => void selectSet(Number(e.target.value))} aria-label="Набор скважин">
          {s.sets.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
        <div className={css.tabs} role="tablist" aria-label="Таблицы набора">
          {s.tabs.map((t) => (
            <button key={t.table} type="button" role="tab" aria-selected={t.table === s.table} className={t.table === s.table ? css.tabActive : css.tab} onClick={() => void openTable(t.table)}>
              {t.name} <span className={css.count}>{t.rows.toLocaleString("ru-RU")}</span>
              <TabMark origin={t.origin} />
            </button>
          ))}
        </div>
      </div>
      <div className={css.bar}>
        <input
          className={css.where}
          value={where}
          placeholder="Фильтр: AU > 0,5 и [Литология] = &quot;гранит&quot; (Enter)"
          aria-label="Фильтр по выражению"
          onChange={(e) => setWhereText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void setWhere(where);
            if (e.key === "Escape") {
              setWhereText("");
              void setWhere("");
            }
          }}
        />
        {s.hole && (
          <span className={css.chip}>
            Скважина {s.hole.name}
            <button type="button" aria-label="Снять фильтр по скважине" onClick={() => void filterHole(null)}>
              ✕
            </button>
          </span>
        )}
        <span className={css.note}>{s.busy ? "Загрузка…" : `строк: ${s.total.toLocaleString("ru-RU")}`}</span>
        <ToolBar onLog={() => setShowLog(!showLog)} logOpen={showLog} />
      </div>
      {s.error && <div className={css.error}>{s.error}</div>}
      <div className={css.main}>
        <Grid />
        {showLog && <EditLog onClose={() => setShowLog(false)} />}
      </div>
    </div>
  );
}

function Grid() {
  const s = useStore(tablesStore, (x) => x);
  const box = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const [height, setHeight] = useState(300);
  /** The cell being edited, by row id (a sorted row moves after a save); `initial` — not saved when unchanged. */
  const [editing, setEditing] = useState<{ id: number; key: string; value: string; initial: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [stats, setStats] = useState<{ column: ColumnInfo; data: FieldStats | null; error?: string } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    box.current?.scrollTo({ top: 0 });
    setTop(0);
  }, [s.table, s.sort, s.desc, s.hole, s.where, s.setId]);

  const first = Math.max(0, Math.floor(top / ROW_H) - 5);
  const last = Math.min(s.total, Math.ceil((top + height) / ROW_H) + 5);
  useEffect(() => {
    void loadPage(first);
    if (Math.floor(last / PAGE) !== Math.floor(first / PAGE)) void loadPage(last);
  }, [first, last, s.version, s.table]);

  const cols = s.columns;
  const locked = rowsLocked(s);
  const startEdit = (row: Row, c: ColumnInfo) => {
    if (locked) return setMessage(locked);
    const v = editText(row[c.key], c.kind);
    setEditing({ id: row.id, key: c.key, value: v, initial: v });
  };
  const save = async (row: Row, next?: number) => {
    if (!editing) return;
    if (editing.value !== editing.initial) {
      const err = await editCell(row, editing.key, editing.value);
      setMessage(err);
      if (err) return;
    }
    const found = next !== undefined && cols[next] ? findRow(tablesStore.get(), row.id) : null;
    if (found && next !== undefined) startEdit(found.row, cols[next]);
    else setEditing(null);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>, row: Row, col: number) => {
    if (!editing) return;
    if (e.key === "Enter") void save(row);
    else if (e.key === "Escape") setEditing(null);
    else if (e.key === "Tab") {
      e.preventDefault();
      void save(row, col + 1);
    }
  };
  const showStats = async (c: ColumnInfo) => {
    setStats({ column: c, data: null });
    try {
      setStats({ column: c, data: await fieldStats(c.key) });
    } catch (e) {
      setStats({ column: c, data: null, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const rows: { index: number; row: Row | undefined }[] = [];
  for (let i = first; i < last; i++) rows.push({ index: i, row: rowAt(s, i) });
  const template = `40px ${cols.map((c) => (c.kind === "number" ? "96px" : "140px")).join(" ")}`;

  return (
    <div className={css.gridWrap}>
      {message && (
        <div className={css.error} role="alert">
          {message}{" "}
          <button type="button" className={css.link} onClick={() => setMessage(null)}>
            Закрыть
          </button>
        </div>
      )}
      <div className={css.grid} ref={box} onScroll={(e) => setTop(e.currentTarget.scrollTop)} role="grid" aria-rowcount={s.total} data-testid="table-grid">
        <div className={css.head} style={{ gridTemplateColumns: template }} role="row">
          <span />
          {cols.map((c) => (
            <span key={c.key} role="columnheader" className={css.th} title={c.unit ? `${c.name}, ${c.unit}` : c.name}>
              <button type="button" className={css.sortBtn} onClick={() => void sortBy(c.key)}>
                {c.name}
                {c.required && <span className={css.req}>*</span>}
                {s.sort === c.key ? (s.desc ? " ▼" : " ▲") : ""}
              </button>
              <button type="button" className={css.sigma} title="Σ Статистика поля" aria-label={`Статистика ${c.name}`} onClick={() => void showStats(c)}>
                Σ
              </button>
            </span>
          ))}
        </div>
        <div style={{ height: s.total * ROW_H, position: "relative" }}>
          {rows.map(({ index, row }) => (
            <div key={index} role="row" className={css.row} style={{ top: index * ROW_H, gridTemplateColumns: template }}>
              <span className={css.rowNo}>
                {row && (
                  <button type="button" className={css.del} title={locked ?? "Удалить строку"} aria-label="Удалить строку" disabled={locked !== null} onClick={() => void deleteRow(row).then(setMessage)}>
                    <Icon name="trash" size={12} />
                  </button>
                )}
                {index + 1}
              </span>
              {cols.map((c, j) => {
                if (!row) return <span key={c.key} className={css.cell} />;
                const isEdit = editing && editing.id === row.id && editing.key === c.key;
                if (isEdit)
                  return (
                    <input
                      key={c.key}
                      className={css.input}
                      autoFocus
                      value={editing.value}
                      aria-label={`Правка ${c.name}`}
                      onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                      onKeyDown={(e) => onKey(e, row, j)}
                      onBlur={() => setEditing(null)}
                    />
                  );
                return (
                  <span
                    key={c.key}
                    role="gridcell"
                    className={`${css.cell} ${c.kind === "number" ? css.num : ""} ${c.key === "hole" ? css.holeCell : ""}`}
                    title={c.key === "hole" ? "Показать только эту скважину" : (locked ?? "Щелчок — правка")}
                    onClick={() => (c.key === "hole" && s.table !== "collar" ? void filterHole({ id: row.hole_id, name: row.hole }) : startEdit(row, c))}
                  >
                    {cellText(row[c.key], c.kind)}
                  </span>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {stats && <StatsBox stats={stats} onClose={() => setStats(null)} />}
    </div>
  );
}

function StatsBox({ stats, onClose }: { stats: { column: ColumnInfo; data: FieldStats | null; error?: string }; onClose: () => void }) {
  const d = stats.data;
  const f = (v?: number) => (v === undefined ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: 4 }));
  return (
    <div className={css.stats} role="dialog" aria-label={`Статистика ${stats.column.name}`}>
      <header>
        <b>Σ {stats.column.name}</b>
        <button type="button" className={css.link} onClick={onClose}>
          Закрыть
        </button>
      </header>
      {stats.error && <div className={css.error}>{stats.error}</div>}
      {!d && !stats.error && <div className={css.note}>Расчёт…</div>}
      {d && (
        <table>
          <tbody>
            <tr><th>Заполнено</th><td>{d.filled.toLocaleString("ru-RU")}</td></tr>
            <tr><th>Пусто</th><td>{d.empty.toLocaleString("ru-RU")}</td></tr>
            {d.kind === "number" ? (
              <>
                <tr><th>Мин</th><td>{f(d.min)}</td></tr>
                <tr><th>Макс</th><td>{f(d.max)}</td></tr>
                <tr><th>Среднее</th><td>{f(d.mean)}</td></tr>
                <tr><th>Медиана</th><td>{f(d.median)}</td></tr>
                <tr><th>Ст. отклонение</th><td>{f(d.std)}</td></tr>
              </>
            ) : (
              <>
                <tr><th>Уникальных</th><td>{d.unique?.toLocaleString("ru-RU")}</td></tr>
                {d.top?.map((t) => (
                  <tr key={t.value}><th>{t.value}</th><td>{t.count.toLocaleString("ru-RU")}</td></tr>
                ))}
              </>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
