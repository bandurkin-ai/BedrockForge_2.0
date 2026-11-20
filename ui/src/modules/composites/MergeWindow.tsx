/**
 * The windows «Объединить таблицы» and «Координаты проб» (ТЗ 8.2.1–8.2.3; ADR 0018 §6, §10–11). «Объединить»: the
 * interval tables of the set (two or more), the fields of the result with the names met in two tables marked (they
 * get the name of their table), the name «Объединение: …»; «Координаты»: one table, its copy gets XB…ZE. Both — the
 * preview on one hole as rows of intervals (`IntervalTable`, shared with the other tool windows), «Применить» → the
 * job, 409 `table_exists` → «Заменить?» in our own window.
 */
import { useEffect, useState, type ReactNode } from "react";
import css from "../../shell/PropsParts.module.css";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import { loadFields, loadTables, type IntervalColumns, type PreviewAnswer, type TableRow } from "./api";
import { SourcePicker } from "./SourcePicker";
import { HolePicker, JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { COORD_COLUMNS, coords, coordsName } from "./tools/coords";
import { merge, mergedFields, mergeName, type MergedField } from "./tools/merge";
import { valueText } from "../../charts/column/scale";

export const PREVIEW_ROWS = 50;

/** The columns of a preview to show: the given ones first (when present), then the rest in the order of the core. */
export function previewColumns(cols: IntervalColumns, first: readonly string[] = []): string[] {
  const names = Object.keys(cols.values);
  return [...first.filter((c) => names.includes(c)), ...names.filter((c) => !first.includes(c))];
}

/** Rows of intervals of one hole: FROM, TO and the values; a missing value — «—» (never 0). */
export function IntervalTable({ cols, first, caption, testId = "composites-rows" }: { cols: IntervalColumns; first?: readonly string[]; caption: string; testId?: string }) {
  const names = previewColumns(cols, first);
  const n = Math.min(cols.from.length, PREVIEW_ROWS);
  return (
    <div style={{ overflow: "auto", maxHeight: 260 }}>
      <table className={own.rows} data-testid={testId}>
        <caption className={css.note} style={{ textAlign: "left" }}>
          {caption}
          {cols.from.length > n ? ` (первые ${n} из ${cols.from.length})` : ""}
        </caption>
        <thead>
          <tr>
            <th>От, м</th>
            <th>До, м</th>
            {names.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: n }, (_, i) => (
            <tr key={i}>
              <td className={own.num}>{valueText(cols.from[i], 3)}</td>
              <td className={own.num}>{valueText(cols.to[i], 3)}</td>
              {names.map((c) => {
                const v = cols.values[c]?.[i];
                return (
                  <td key={c} className={typeof v === "number" ? own.num : undefined}>
                    {v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v)) ? "—" : valueText(v, 4)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The preview box of a tool window: the hole, what the core says of it, the rows of the result. */
export function PreviewBox({ t, first, children }: { t: ReturnType<typeof useTool>; first?: readonly string[]; children?: ReactNode }) {
  const a: PreviewAnswer | null = t.answer;
  return (
    <fieldset className={css.group} data-testid="composites-preview">
      <legend>Предпросмотр</legend>
      <HolePicker setId={t.src.setId} value={t.hole?.id ?? null} onChange={(id, name) => t.setHole(id === null ? null : { id, name })} />
      {t.checked.reason && <p className={css.note}>Предпросмотр — когда заданы параметры: {t.checked.reason}</p>}
      {t.previewError && <p className={css.warn}>{t.previewError}</p>}
      {children}
      {a ? <IntervalTable cols={a.result} first={first} caption={`Скважина ${a.hole}: проб ${a.samples.from.length}, результат — ${a.result.from.length}`} /> : !t.checked.reason && <p className={css.note}>{t.hole ? "Расчёт предпросмотра…" : "Выберите скважину"}</p>}
      {a && a.notes.length > 0 && (
        <ul className={own.notes}>
          {a.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

const nameOf = (tables: readonly TableRow[], key: string | null): string | null => tables.find((t) => t.table === key)?.name ?? null;

/** The tables of the set of the window (for names); re-read when the set changes. */
function useSetTables(setId: number | null): TableRow[] {
  const [tables, setTables] = useState<TableRow[]>([]);
  useEffect(() => {
    let alive = true;
    setTables([]);
    if (setId !== null) loadTables(setId).then((t) => alive && setTables(t), () => undefined);
    return () => {
      alive = false;
    };
  }, [setId]);
  return tables;
}

/** The fields of every chosen table, in the order of the choice. */
function useMergedFields(setId: number | null, chosen: readonly string[], tables: readonly TableRow[]): MergedField[] | null {
  const [out, setOut] = useState<MergedField[] | null>(null);
  const key = `${setId}:${chosen.join(",")}:${tables.length}`;
  useEffect(() => {
    let alive = true;
    setOut(null);
    if (setId === null || chosen.length < 2) return;
    Promise.all(chosen.map(async (t) => ({ name: nameOf(tables, t) ?? t, fields: (await loadFields(setId, t)).map((f) => f.name) }))).then(
      (list) => alive && setOut(mergedFields(list)),
      () => alive && setOut([]),
    );
    return () => {
      alive = false;
    };
  }, [key]);
  return out;
}

export function MergeWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(merge, args);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const tables = useSetTables(t.src.setId);
  const fields = useMergedFields(t.src.setId, t.src.tables, tables);
  const names = t.src.tables.map((k) => nameOf(tables, k) ?? k);
  const clashes = (fields ?? []).filter((f) => f.clash);
  const name = (typeof t.values.name === "string" && t.values.name.trim()) || mergeName(names);
  if (t.job) return <JobView title={merge.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-merge">
      <div className={css.winBody}>
        <p className={css.note}>{merge.note}</p>
        <SourcePicker needs={merge.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
        {fields && fields.length > 0 && (
          <fieldset className={css.group} data-testid="composites-merge-fields">
            <legend>Поля результата</legend>
            <p className={css.note}>
              {fields.length} полей из {names.length} таблиц
              {clashes.length ? `; совпадают имена: ${[...new Set(clashes.map((f) => f.field))].join(", ")} — к ним добавится имя таблицы` : ""}
            </p>
            <div className={own.checklist}>
              {fields.map((f) => (
                <span key={`${f.table}:${f.field}`} className={f.clash ? css.warn : undefined}>
                  {f.table} › {f.field}
                </span>
              ))}
            </div>
          </fieldset>
        )}
        <ParamForm params={merge.params} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} placeholders={{ name: mergeName(names) }} />
        <PreviewBox t={t} />
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={`Новая таблица «${name}» в наборе; исходные таблицы не меняются.`} />
    </div>
  );
}

export function CoordsWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(coords, args);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const tables = useSetTables(t.src.setId);
  const table = nameOf(tables, t.src.table);
  const name = (typeof t.values.name === "string" && t.values.name.trim()) || coordsName(table);
  if (t.job) return <JobView title={coords.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-coords">
      <div className={css.winBody}>
        <p className={css.note}>{coords.note}</p>
        <SourcePicker needs={coords.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
        <p className={css.note}>Новые поля: {COORD_COLUMNS.join(", ")} — начало (B), середина (M) и конец (E) пробы.</p>
        <ParamForm params={coords.params} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} placeholders={{ name: coordsName(table) }} />
        <PreviewBox t={t} first={COORD_COLUMNS} />
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={`Новая таблица «${name}» в наборе; исходная таблица не меняется.`} />
    </div>
  );
}
