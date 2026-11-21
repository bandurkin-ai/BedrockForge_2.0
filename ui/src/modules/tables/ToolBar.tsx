import { useState } from "react";
import { bridge } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import { askConfirm } from "../../shell/dialogs";
import { sampleSelectionStore, selectByFilter } from "../../shell/sampleSelection";
import { useStore } from "../../shell/useStore";
import { refresh as refreshJobs } from "../jobs/state";
import { addRow, loadTabs, rowsLocked, runTool, setWhere, tablesStore, type TablesState } from "./state";
import css from "./Tables.module.css";

type FieldSpec = { key: string; label: string; kind?: "text" | "column" | "table" | "kind" | "check" | "format" | "codes"; required?: boolean };
type ToolId = "row" | "replace" | "computed" | "column" | "rename" | "delete" | "append" | "merge" | "codes" | "export";

const TOOLS: Record<ToolId, { title: string; fields: FieldSpec[] }> = {
  row: { title: "+ Строка", fields: [] },
  replace: {
    title: "Найти и заменить",
    fields: [
      { key: "column", label: "Колонка", kind: "column", required: true },
      { key: "find", label: "Найти", required: true },
      { key: "replace", label: "Заменить на" },
      { key: "whole", label: "Ячейка целиком", kind: "check" },
      { key: "case", label: "С учётом регистра", kind: "check" },
    ],
  },
  computed: {
    title: "Вычисляемое поле",
    fields: [
      { key: "name", label: "Название", required: true },
      { key: "expression", label: "Выражение, например (TO - FROM) * AU", required: true },
    ],
  },
  column: {
    title: "Новая колонка",
    fields: [
      { key: "name", label: "Название", required: true },
      { key: "kind", label: "Тип", kind: "kind" },
      { key: "unit", label: "Единица" },
    ],
  },
  rename: { title: "Переименовать колонку", fields: [{ key: "column", label: "Колонка", kind: "column", required: true }, { key: "name", label: "Новое название", required: true }] },
  delete: { title: "Удалить колонку", fields: [{ key: "column", label: "Колонка", kind: "column", required: true }] },
  append: { title: "Добавить строки из таблицы", fields: [{ key: "source", label: "Добавить строки из", kind: "table", required: true }] },
  merge: {
    title: "Слияние по ключу",
    fields: [
      { key: "source", label: "Из таблицы", kind: "table", required: true },
      { key: "key", label: "Ключ (кроме скважины), через запятую: from, to или колонка" },
      { key: "columns", label: "Колонки источника, через запятую", required: true },
    ],
  },
  codes: {
    title: "Справочник кодов",
    fields: [
      { key: "column", label: "Колонка", kind: "column", required: true },
      { key: "list", label: "Справочник", kind: "codes" },
      { key: "items", label: "Новый справочник: коды через «;»" },
    ],
  },
  export: { title: "Экспорт таблицы", fields: [{ key: "format", label: "Формат", kind: "format" }] },
};

const MENU: ToolId[] = ["replace", "computed", "column", "rename", "delete", "append", "merge", "codes", "export"];
/** Tools that change cells or rows: not for a result table (ADR 0018 decision 24); columns may be added. */
const ROW_TOOLS: ToolId[] = ["replace", "append"];
const err = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** Why «Выделить на графиках» cannot be pressed now; null — it can (ТЗ 8.3, № 237: the rows of the filter). */
export function markReason(s: Pick<TablesState, "setId" | "table" | "tabs" | "selection">, busy: boolean): string | null {
  const tab = s.tabs.find((t) => t.table === s.table);
  if (s.setId === null || !tab) return "Откройте таблицу набора";
  if (tab.kind !== "assay" && tab.kind !== "interval") return "Выделяются строки интервальной таблицы (пробы, литология…)";
  if (s.selection) return "Показаны строки выделения";
  if (busy) return "Выделение делается…";
  return null;
}

/** «+ Строка», «Журнал правок», «Выделить на графиках», «Инструменты» of the panel (ТЗ 7.10; 7.34 п. 22, 31–36; 8.3). */
export function ToolBar({ onLog, logOpen }: { onLog: () => void; logOpen: boolean }) {
  const s = useStore(tablesStore, (x) => x);
  const selBusy = useStore(sampleSelectionStore, (x) => x.busy);
  const markWhy = markReason(s, selBusy);
  const [tool, setTool] = useState<ToolId | null>(null);
  const [menu, setMenu] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const open = (id: ToolId) => {
    setTool(id);
    setMenu(false);
    setResult(null);
    setValues(id === "export" ? { format: "dat" } : id === "column" ? { kind: "number" } : {});
    if (id === "codes") void api.get<{ lists: { name: string }[] }>("/drillholes/codes").then((r) => setCodes(r.lists.map((x) => x.name)));
  };
  const fields: FieldSpec[] = tool === "row" ? s.columns.map((c) => ({ key: c.key, label: c.name, required: c.required })) : tool ? TOOLS[tool].fields : [];
  const ready = fields.every((f) => !f.required || (values[f.key] ?? "").trim());

  const submit = async () => {
    if (!tool) return;
    const v = values;
    let msg: string | null = null;
    if (tool === "row") msg = await addRow(Object.fromEntries(Object.entries(v).filter(([, x]) => x !== "")));
    else if (tool === "replace") {
      const r = await runTool("replace", { column: v.column, find: v.find, replace: v.replace ?? "", whole: v.whole === "1", case: v.case === "1", hole: s.hole?.id ?? null, where: s.where || null });
      msg = r.ok ? `Заменено: ${r.result.replaced}` : r.error;
    } else if (tool === "computed" || tool === "column") {
      const r = await runTool("columns", { name: v.name, kind: v.kind ?? "number", unit: v.unit || null, expression: tool === "computed" ? v.expression : null });
      msg = r.ok ? `Колонка «${r.result.name}» добавлена` : r.error;
    } else if (tool === "rename") {
      const r = await runTool(`columns/${encodeURIComponent(v.column)}`, { name: v.name }, "patch");
      msg = r.ok ? "Колонка переименована" : r.error;
    } else if (tool === "delete") {
      if (!(await askConfirm({ title: "Удалить колонку", message: "Колонка скроется; «Отменить» в журнале правок вернёт её со значениями.", okLabel: "Удалить", danger: true }))) return;
      const r = await runTool(`columns/${encodeURIComponent(v.column)}`, {}, "del");
      msg = r.ok ? "Колонка удалена" : r.error;
    } else if (tool === "append") {
      const r = await runTool("append", { source: v.source });
      msg = r.ok ? `Добавлено строк: ${r.result.rows}` : r.error;
    } else if (tool === "merge") {
      const key = ["hole", ...(v.key ?? "").split(",").map((x) => x.trim()).filter(Boolean)];
      const r = await runTool("merge", { source: v.source, key, columns: (v.columns ?? "").split(",").map((x) => x.trim()).filter(Boolean) });
      msg = r.ok ? `Совпало строк: ${r.result.matched} из ${r.result.rows}` : r.error;
    } else if (tool === "codes") msg = await codesTool(v);
    else if (tool === "export") msg = await exportTool(v.format ?? "dat");
    if (tool === "row" && !msg) {
      setTool(null);
      return;
    }
    setResult(msg);
  };

  const codesTool = async (v: Record<string, string>): Promise<string> => {
    const base = `/drillholes/sets/${s.setId}/tables/${s.table}`;
    try {
      let list = v.list;
      if (v.items?.trim()) {
        list = v.list || "Коды";
        await api.put("/drillholes/codes", { name: list, items: v.items.split(";").map((c) => ({ code: c.trim(), title: "" })) });
      }
      if (list) await api.put(`${base}/codes`, { column: v.column, codes: list });
      const r = await api.get<{ bad: { value: string; count: number }[]; rows: number; where: string | null }>(`${base}/codes/check?column=${encodeURIComponent(v.column)}`);
      if (!r.rows) return "Справочник: все значения из справочника";
      if (r.where) await setWhere(r.where);
      return `Нет значений в справочнике: ${r.bad.map((b) => `${b.value} (${b.count})`).join(", ")} — строки показаны фильтром`;
    } catch (e) {
      return err(e);
    }
  };

  const exportTool = async (format: string): Promise<string> => {
    const tab = s.tabs.find((t) => t.table === s.table);
    const set = s.sets.find((x) => x.id === s.setId);
    const path = await bridge()?.pickSave(`${set?.name ?? "Набор"} — ${tab?.name ?? "таблица"}.${format}`, format as "dat" | "csv" | "xlsx");
    if (!path) return "Отменено";
    try {
      await api.post(`/drillholes/sets/${s.setId}/tables/${s.table}/export`, { path, format, hole: s.hole?.id ?? null, where: s.where || null, sort: s.sort, desc: s.desc });
      await refreshJobs();
      return "Экспорт запущен: ход — во вкладке «Задания»";
    } catch (e) {
      return err(e);
    }
  };

  return (
    <>
      <button type="button" className={css.btn} onClick={() => open("row")} disabled={!s.table || rowsLocked(s) !== null} title={rowsLocked(s) ?? undefined}>
        + Строка
      </button>
      <button type="button" className={logOpen ? css.btnActive : css.btn} onClick={onLog}>
        Журнал правок
      </button>
      <button
        type="button"
        className={css.btn}
        disabled={markWhy !== null}
        title={markWhy ?? "Строки текущего фильтра (выражение, скважина) — выделение проб: отмечаются на открытых графиках и в 3D"}
        onClick={() => s.setId !== null && void selectByFilter(s.setId, s.table, s.where, s.hole?.id ?? null)}
      >
        Выделить на графиках
      </button>
      <span className={css.menuWrap}>
        <button type="button" className={css.btn} onClick={() => setMenu(!menu)} aria-haspopup="menu" aria-expanded={menu} disabled={!s.table}>
          Инструменты ▾
        </button>
        {menu && (
          <div className={css.menu} role="menu">
            {MENU.map((id) => (
              <button key={id} type="button" role="menuitem" disabled={ROW_TOOLS.includes(id) && rowsLocked(s) !== null} title={ROW_TOOLS.includes(id) ? (rowsLocked(s) ?? undefined) : undefined} onClick={() => open(id)}>
                {TOOLS[id].title}…
              </button>
            ))}
          </div>
        )}
      </span>
      {tool && (
        <div className={css.form} role="dialog" aria-label={TOOLS[tool].title}>
          <header>
            <b>{tool === "row" ? "Новая строка" : TOOLS[tool].title}</b>
            <button type="button" className={css.link} onClick={() => setTool(null)}>
              Закрыть
            </button>
          </header>
          <div className={css.formFields}>
            {fields.map((f) => (
              <label key={f.key} className={f.kind === "check" ? css.checkLabel : undefined}>
                {f.kind !== "check" && (
                  <span>
                    {f.label}
                    {f.required && <span className={css.req}>*</span>}
                  </span>
                )}
                <FieldInput spec={f} value={values[f.key] ?? ""} codes={codes} onChange={(x) => setValues({ ...values, [f.key]: x })} />
                {f.kind === "check" && f.label}
              </label>
            ))}
          </div>
          {result && <div className={css.note}>{result}</div>}
          <button type="button" className={css.primary} disabled={!ready} onClick={() => void submit().then(() => void loadTabs())}>
            {tool === "row" ? "Добавить" : tool === "export" ? "Сохранить…" : "Выполнить"}
          </button>
        </div>
      )}
    </>
  );
}

function FieldInput({ spec, value, codes, onChange }: { spec: FieldSpec; value: string; codes: string[]; onChange: (v: string) => void }) {
  const s = useStore(tablesStore, (x) => x);
  if (spec.kind === "check") return <input type="checkbox" checked={value === "1"} onChange={(e) => onChange(e.target.checked ? "1" : "")} />;
  if (spec.kind === "column")
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={spec.label}>
        <option value="">—</option>
        {s.columns.map((c) => (
          <option key={c.key} value={c.key}>
            {c.name}
          </option>
        ))}
      </select>
    );
  if (spec.kind === "table")
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={spec.label}>
        <option value="">—</option>
        {s.tabs.filter((t) => t.table !== s.table && t.kind !== "collar" && t.kind !== "survey").map((t) => (
          <option key={t.table} value={t.table}>
            {t.name}
          </option>
        ))}
      </select>
    );
  if (spec.kind === "kind")
    return (
      <select value={value || "number"} onChange={(e) => onChange(e.target.value)} aria-label={spec.label}>
        <option value="number">число</option>
        <option value="text">текст</option>
      </select>
    );
  if (spec.kind === "format")
    return (
      <select value={value || "dat"} onChange={(e) => onChange(e.target.value)} aria-label={spec.label}>
        <option value="dat">Micromine DAT</option>
        <option value="csv">CSV (разделитель «;», запятая)</option>
        <option value="xlsx">Excel XLSX</option>
      </select>
    );
  if (spec.kind === "codes")
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={spec.label}>
        <option value="">— новый —</option>
        {codes.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    );
  return <input value={value} onChange={(e) => onChange(e.target.value)} aria-label={spec.label} />;
}
