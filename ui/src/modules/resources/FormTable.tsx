/**
 * The two tables of the result panel (ТЗ 8.10 «Вывод»; ADR 0027 решение 19): the form of the template (JORC, ГКЗ, own) with its sections, groups, subtotals and the
 * total — every cell is the text the core made (`text`: the digits of the template, a decimal comma, «—» for no value), the interface adds nothing to it — and the
 * table of the rows (a group, a cut-off, blocks, volume, tonnes, and for each element the grade, the metal and what had no grade). A gap is «—» / «нет значения»,
 * never 0. A cell of a saved report is a button: a click gives its `CellRef` (20.15: the blocks of the cell). Presentation only: no state, no requests.
 */
import { Fragment } from "react";
import type { Form, FormCell, FormColumn, ReportResult, ResultRow } from "./answers";
import { cellOfForm, cellOfRow, type CellRef } from "./cells";
import { fmt, fmtCount, groupText, rowTitle } from "./format";
import css from "./Panel.module.css";

export type CellHandler = (cell: CellRef) => void;

const DRILL_HINT = "Щелчок — блоки этой ячейки";
const PREVIEW_HINT = "Предпросмотр не записан в проект: сохраните отчёт, чтобы перейти к блокам ячейки";

/** The attributes of a cell that can be clicked: a button for the mouse and the keyboard. */
function clickable(on: CellHandler | undefined, ref: () => CellRef, saved: boolean) {
  if (!on) return { className: undefined, title: undefined };
  if (!saved) return { className: undefined, title: PREVIEW_HINT };
  return {
    className: css.click,
    title: DRILL_HINT,
    tabIndex: 0,
    role: "button" as const,
    onClick: () => on(ref()),
    onKeyDown: (e: { key: string; preventDefault: () => void }) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        on(ref());
      }
    },
  };
}

const isGap = (c: FormCell | undefined): boolean => !c || c.value === null;

/** The class names that are there, joined; none — no attribute. */
const cx = (...names: (string | false | undefined)[]): string | undefined => names.filter(Boolean).join(" ") || undefined;

/** The groups of the columns of a form with two blocks of columns («Балансовые», «Забалансовые»): [title, span] in the order of the columns. */
export function columnGroups(columns: readonly FormColumn[]): { title: string; span: number }[] {
  const out: { title: string; span: number }[] = [];
  for (const c of columns) {
    const title = c.group ?? "";
    const last = out[out.length - 1];
    if (last && last.title === title) last.span += 1;
    else out.push({ title, span: 1 });
  }
  return out;
}

export function FormTable({ result, form, onCell }: { result: ReportResult; form: Form; onCell?: CellHandler }) {
  const grouped = form.columns.some((c) => c.group);
  const spans = columnGroups(form.columns);
  const width = form.columns.length + 1;
  return (
    <div className={css.tableWrap}>
      <table className={css.table} data-testid="res-form-table" data-template={form.template_id}>
        <thead>
          <tr>
            <th rowSpan={grouped ? 2 : 1} className={css.left}>
              Категория
            </th>
            {grouped &&
              spans.map((g, i) => (
                <th key={i} colSpan={g.span} className={css.span}>
                  {g.title}
                </th>
              ))}
            {!grouped &&
              form.columns.map((c) => (
                <th key={c.key} data-testid={`res-form-col-${c.key}`}>
                  {c.label}
                </th>
              ))}
          </tr>
          {grouped && (
            <tr>
              {form.columns.map((c) => (
                <th key={c.key} data-testid={`res-form-col-${c.key}`}>
                  {c.label}
                </th>
              ))}
            </tr>
          )}
        </thead>
        <tbody>
          {form.sections.map((s, si) => (
            <Fragment key={si}>
              {s.label !== null && (
                <tr className={css.section} data-testid={`res-form-section-${si}`}>
                  <td colSpan={width}>{s.label}</td>
                </tr>
              )}
              {s.rows.map((r, ri) => (
                <tr key={ri} className={r.kind === "row" ? undefined : css[r.kind]} data-kind={r.kind} data-testid={`res-form-row-${si}-${ri}`}>
                  <td className={css.left} data-testid={`res-form-label-${si}-${ri}`}>
                    {r.label}
                  </td>
                  {form.columns.map((c) => {
                    const cell = r.cells[c.key];
                    const at = clickable(onCell, () => cellOfForm(result, form, s.label, r, c), result.saved);
                    return (
                      <td key={c.key} {...at} className={cx(at.className, isGap(cell) && css.gap)} data-testid={`res-form-cell-${si}-${ri}-${c.key}`} data-missing={isGap(cell) || undefined}>
                        {cell ? cell.text : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The columns the table of the rows has for an element: the grade, the metal and what had no grade (tonnes). */
const keyTitle = (k: ReportResult["keys"][number]): string => (k.kind === "bench" ? "Уступ" : (k.field ?? "Группа"));

export function RowsTable({ result, rows, onCell }: { result: ReportResult; rows: readonly ResultRow[]; onCell?: CellHandler }) {
  const keys = result.keys.length ? result.keys : [{ kind: "field" as const, field: null, labels: [] }];
  let prev = "";
  return (
    <div className={css.tableWrap}>
      <table className={css.table} data-testid="res-rows-table">
        <thead>
          <tr>
            {keys.map((k, i) => (
              <th key={i} rowSpan={2} className={css.left}>
                {keys.length === 1 && !result.keys.length ? "Группа" : keyTitle(k)}
              </th>
            ))}
            <th rowSpan={2}>Борт</th>
            <th rowSpan={2}>Блоков</th>
            <th rowSpan={2}>Объём, м³</th>
            <th rowSpan={2}>Тоннаж, т</th>
            {result.elements.map((e) => (
              <th key={e.field} colSpan={3} className={css.span}>
                {e.field}
              </th>
            ))}
          </tr>
          <tr>
            {result.elements.map((e) => (
              <Fragment key={e.field}>
                <th className={css.sep}>Содержание{e.unit_label ? `, ${e.unit_label}` : ""}</th>
                <th>Металл{e.metal_base ? `, ${e.metal_base}` : ""}</th>
                <th title="Блоки строки, в которых нет значения этого содержания: их тонны в тоннаже строки, а в среднее и металл они не входят">Без содержания, т</th>
              </Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const mark = `${r.kind}|${r.cutoff}`;
            const start = mark !== prev && i > 0;
            prev = mark;
            const at = (column: string) => clickable(onCell, () => cellOfRow(result, r, column), result.saved);
            const num = (column: string, v: number | null, id: string, count = false) => {
              const a = at(column);
              return (
                <td key={column} {...a} className={cx(a.className, v === null && css.gap)} data-testid={`res-row-${i}-${id}`} data-missing={v === null || undefined}>
                  {v === null ? fmt(null) : count ? fmtCount(v) : fmt(v)}
                </td>
              );
            };
            return (
              <tr key={i} className={cx(r.total && css.total, start && css.groupStart)} data-testid={`res-row-${i}`} data-total={r.total || undefined}>
                {keys.map((_k, ki) => (
                  <td key={ki} className={css.left} data-testid={ki === 0 ? `res-row-${i}-group` : undefined}>
                    {r.total || !r.g.length ? (ki === 0 ? groupText({ g: [], total: true }) : "") : (r.g[ki] ?? "(пусто)")}
                  </td>
                ))}
                <td data-testid={`res-row-${i}-cutoff`}>{rowTitle(r)}</td>
                {num("blocks", r.blocks, "blocks", true)}
                {num("volume", r.volume, "volume")}
                {num("tonnes", r.tonnes, "tonnes")}
                {result.elements.map((e) => {
                  const c = r.elements[e.field];
                  const noGrade = c?.grade == null;
                  const noUnit = !noGrade && c?.metal == null; // a grade with no metal: the unit is not known, it is not a gap of the data
                  const ag = at(`grade:${e.field}`);
                  const am = at(`metal:${e.field}`);
                  return (
                    <Fragment key={e.field}>
                      <td {...ag} className={cx(ag.className, css.sep, noGrade && css.gap)} data-testid={`res-row-${i}-grade-${e.field}`} data-missing={noGrade || undefined}>
                        {fmt(c?.grade ?? null)}
                      </td>
                      <td {...am} className={cx(am.className, noGrade && css.gap)} title={noUnit ? `Единица «${e.unit_label || "не задана"}» не распознана: металл считается для г/т, ppm, ppb и %` : am.title} data-testid={`res-row-${i}-metal-${e.field}`} data-missing={noGrade || undefined}>
                        {noUnit ? "—" : fmt(noGrade ? null : (c?.metal ?? null))}
                      </td>
                      <td data-testid={`res-row-${i}-gap-${e.field}`} title={c && c.missing_n ? `Блоков без содержания: ${fmtCount(c.missing_n)}` : undefined}>
                        {c && c.missing_t > 0 ? fmt(c.missing_t) : "0"}
                      </td>
                    </Fragment>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
