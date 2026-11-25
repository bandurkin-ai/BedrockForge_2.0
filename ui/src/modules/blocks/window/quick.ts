/**
 * The quick restrictor of the window «Блочная модель · …» (ТЗ 8.7.6; ADR 0023 §11, decision 16): rows «поле — оператор
 * — значение» joined by «И» / «ИЛИ», «обратный» (НЕ). The rows are typed (a field not chosen yet, a value half-written),
 * so the window keeps them as drafts and writes the style (`quick.items`, the restrictor of the next request) only when
 * every row is right — the same check as the window «Фильтры» (`checkDraft`) and the same words as the command
 * `blocks.quick`. A blank row waits, it is not an error. Pure: no React, no core.
 */
import type { BlockModelInfo } from "../api";
import { checkDraft, COND_OPS, type CondBody, type DraftCond } from "../filters/constructor";
import type { QuickRestrict } from "../display";

export type RowPart = "field" | "value" | "values" | "row";

/** What is wrong in a row (the place in the row and the words of the core's kind with the hint). */
export interface RowIssue {
  row: number;
  part: RowPart;
  text: string;
}

export const blankRow = (): DraftCond => ({ field: "", op: ">", value: "", list: "" });

const isBlank = (r: DraftCond): boolean => !r.field.trim() && !r.value.trim() && !r.list.trim();

const show = (v: string | number): string => String(v);

/** The rows of the form for the conditions of the style. */
export function rowsOf(q: QuickRestrict): DraftCond[] {
  return q.items.map((c) => ({ field: c.field, op: c.op, value: c.value === undefined ? "" : show(c.value), list: (c.values ?? []).map(show).join("\n") }));
}

/** The conditions of the style as the key that tells whether the form still shows what is stored. */
export const itemsKey = (q: QuickRestrict): string => JSON.stringify(q.items);

const partOf = (param: string): RowPart => (param.endsWith(".field") ? "field" : param.endsWith(".value") ? "value" : /\.values(\.|$)/.test(param) ? "values" : "row");

/** The issues of the rows; the field must be one of the model (the same words as `blocks.quick`). */
export function rowIssues(rows: readonly DraftCond[], model?: Pick<BlockModelInfo, "name" | "fields">): RowIssue[] {
  const out: RowIssue[] = [];
  rows.forEach((r, row) => {
    if (isBlank(r)) return;
    const checked = checkDraft({ cond: r });
    if (!checked.ok) {
      const i = checked.issue;
      out.push({ row, part: partOf(i.param), text: `${i.message} ${i.hint}`.trim() });
      return;
    }
    const name = r.field.trim();
    if (model && !model.fields.some((f) => f.name === name)) out.push({ row, part: "field", text: `Поля «${name}» нет в модели «${model.name}». Поля модели: ${model.fields.map((f) => f.name).join(", ") || "нет"}.` });
  });
  return out;
}

/** The restrictor of the style the rows make: all right — `quick`; else the issues (nothing is written). */
export function commitRows(rows: readonly DraftCond[], join: QuickRestrict["join"], invert: boolean, model?: Pick<BlockModelInfo, "name" | "fields">): { ok: true; quick: QuickRestrict } | { ok: false; issues: RowIssue[] } {
  const issues = rowIssues(rows, model);
  if (issues.length) return { ok: false, issues };
  const items: CondBody[] = [];
  for (const r of rows) {
    if (isBlank(r)) continue;
    const c = checkDraft({ cond: r });
    if (c.ok && "cond" in c.rule) items.push(c.rule.cond);
  }
  return { ok: true, quick: { items, join, invert } };
}

/** The operators of the row as the window words them. */
export const OPERATORS = COND_OPS;

/** The labels of a category field for the suggestions of its value (`datalist`). */
export function labelsOf(model: Pick<BlockModelInfo, "fields"> | undefined, field: string): string[] {
  return model?.fields.find((f) => f.name === field.trim())?.labels ?? [];
}
