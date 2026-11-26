/**
 * The cell of the result table as the panel tells it to whoever takes it (20.15, «ячейка → блоки», Д-36 / ADR 0027 решения 22–24): the group (the labels of the
 * keys, null — the total), the cut-off and the kind of the row, the column. `exact` — the cell is one cell of the table of the core (the table of the rows, or
 * the form of one key without sections): `group`, `cutoff` and `kind` go straight to `POST /resources/reports/{id}/drill`. A cell of the form with sections
 * or of a subtotal is a sum of cells: `group` is null, `members` (a subtotal) or `section` tell which. The panel calls the handler of `setCellClickHandler`
 * (one at a time; the drill window of 20.15 sets it); with none a click does nothing. No React.
 */
import type { Form, FormColumn, FormRow, ReportResult, ResultRow, RowKind } from "./answers";

export interface CellRef {
  /** the saved report; null — the preview (it cannot be drilled: the core keeps nothing of it) */
  reportId: number | null;
  source: "table" | "form";
  exact: boolean;
  /** the labels of all the keys of the cell («(пусто)» is a label); null — the total (or a cell that is not one cell: see `exact`) */
  group: string[] | null;
  label: string;
  /** the section of the form (the outer key), else null */
  section: string | null;
  /** the groups a subtotal sums, else null */
  members: string[] | null;
  /** the cut-off of the row; null — «без борта» */
  cutoff: number | null;
  kind: RowKind;
  /** `blocks`, `volume`, `tonnes`, `grade:AU`, `metal:AU` (a column of the table), the key of the column of the form */
  column: string | null;
  metric: string | null;
  element: string | null;
}

type Handler = (cell: CellRef) => void;
let handler: Handler | null = null;

/** The one taker of the clicks on the cells; returns the function that takes it away (only if it is still this one). */
export function setCellClickHandler(fn: Handler | null): () => void {
  handler = fn;
  return () => {
    if (handler === fn) handler = null;
  };
}

/** Passes the cell to the taker; false — nobody takes it. */
export function emitCellClick(cell: CellRef): boolean {
  if (!handler) return false;
  handler(cell);
  return true;
}

/** The columns of the table of the rows: the key of the column, its metric and element (`grade:AU` → grade, AU). */
export function splitColumn(column: string): { metric: string; element: string | null } {
  const i = column.indexOf(":");
  return i < 0 ? { metric: column, element: null } : { metric: column.slice(0, i), element: column.slice(i + 1) };
}

/** A cell of the table of the rows: always exact. */
export function cellOfRow(result: ReportResult, row: ResultRow, column: string): CellRef {
  const { metric, element } = splitColumn(column);
  const total = row.total || !row.g.length || row.g.every((x) => x === null);
  const group = total ? null : row.g.map((x) => x ?? "(пусто)");
  return { reportId: result.report_id, source: "table", exact: true, group, label: group ? group.join(" / ") : "Итого", section: null, members: null, cutoff: row.cutoff, kind: row.kind, column, metric, element };
}

/** The cut-off and the kind of the rows a column of the form shows: a block `balance` / `offbalance` has its own, else the cut-off of the form. */
export function rowOfForm(result: ReportResult, form: Form, col: FormColumn): { kind: RowKind; cutoff: number | null } {
  if (col.part === "balance" || col.part === "offbalance") {
    const r = result.rows.find((x) => x.kind === col.part);
    return { kind: col.part, cutoff: r?.cutoff ?? null };
  }
  return form.cutoff === null ? { kind: "all", cutoff: null } : { kind: "cutoff", cutoff: form.cutoff };
}

/** A cell of the form (one row of a section, one column). */
export function cellOfForm(result: ReportResult, form: Form, section: string | null, row: FormRow, col: FormColumn): CellRef {
  const at = rowOfForm(result, form, col);
  const one = row.kind === "row" && section === null && result.keys.length === 1;
  const total = row.kind === "total" && section === null && result.keys.length <= 1;
  return {
    reportId: result.report_id,
    source: "form",
    exact: one || total,
    group: one ? [row.label] : null,
    label: row.label,
    section,
    members: row.kind === "subtotal" ? row.members : null,
    cutoff: at.cutoff,
    kind: at.kind,
    column: col.key,
    metric: col.metric,
    element: col.element,
  };
}
