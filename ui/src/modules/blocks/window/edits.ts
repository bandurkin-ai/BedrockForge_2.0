/**
 * The edits of the window «Блочная модель · …» (ТЗ 8.7.6; ADR 0023 §11): what every control does is one of these — it
 * reads the style of the model as it is stored now, makes a patch (`style.ts`), checks it in the words of the core and
 * writes it into `block_styles` (`actions.ts` `writeStyle`). A wrong value writes nothing and comes back as the issue
 * with its place in the form (null — written). The layer follows the view; an edit of colours or look makes no request.
 * No React.
 */
import { normalizeColor } from "../../../shell/props";
import type { BlockField, BlockModelInfo } from "../api";
import { mergeStyle, type BlockStyle, type ShowInfo, type StylePatch } from "../display";
import type { DraftCond } from "../filters/constructor";
import { currentStyle, writeStyle } from "./actions";
import { PROJECT_PALETTE } from "./palettes";
import { commitRows, type RowIssue } from "./quick";
import {
  breaksPatch,
  categoriesShown,
  categoryPatch,
  classColourPatch,
  colourIssueAt,
  fieldPatch,
  kindPatch,
  paletteFill,
  parseBound,
  parseBreaks,
  parseLimit,
  parseWidth,
  takeFromData,
  takeReason,
  type FormField,
  type FormIssue,
} from "./style";

const fieldOf = (model: Pick<BlockModelInfo, "fields"> | undefined, name: string | null): BlockField | undefined => (name ? model?.fields.find((f) => f.name === name) : undefined);

/** Writes a patch; a failure of the write (no project open) is the issue of the form. */
function put(id: number, patch: StylePatch, field: FormField = "general"): FormIssue | null {
  const bad = writeStyle(id, patch);
  return bad ? { field, text: bad } : null;
}

const asIssue = (field: FormField, text: string): FormIssue => ({ field, text });

/**
 * A patch of the colouring the core would refuse (a range backwards, a logarithm from zero, breaks out of order) is not
 * written — the person is told under the field they edited. A style that was already wrong keeps its words: an edit of
 * another field that leaves the same fault is not blamed for it.
 */
function checked(id: number, model: BlockModelInfo | undefined, patch: StylePatch, field: FormField): FormIssue | null {
  const cur = currentStyle(id);
  const next = colourIssueAt(mergeStyle(cur, patch).colour, model);
  if (next && (next.field === field || next.text !== colourIssueAt(cur.colour, model)?.text)) return asIssue(field, next.text);
  return put(id, patch, field);
}

// ------------------------------------------------------------------------------------------------ the colouring

/** The field of the colouring («— без раскраски —» is null). */
export function setField(id: number, model: BlockModelInfo | undefined, name: string | null): FormIssue | null {
  if (name !== null && !fieldOf(model, name)) return asIssue("field", `Поля «${name}» нет в этой модели. Выберите поле из списка.`);
  return put(id, fieldPatch(name, fieldOf(model, name), currentStyle(id)), "field");
}

/** «Шкала» / «Градации» for a field of a number. */
export function setKind(id: number, model: BlockModelInfo | undefined, kind: "scale" | "classes"): FormIssue | null {
  const cur = currentStyle(id);
  return put(id, kindPatch(kind, fieldOf(model, cur.colour.field), cur), "field");
}

export const setLog = (id: number, model: BlockModelInfo | undefined, on: boolean): FormIssue | null => checked(id, model, { colour: { log: on } }, "from");

/** A bound of the scale typed; empty — the core takes the minimum / maximum of the field. */
export function commitBound(id: number, model: BlockModelInfo | undefined, which: "from" | "to", text: string): FormIssue | null {
  const p = parseBound(text, which);
  if (!p.ok) return p.issue;
  return checked(id, model, { colour: { [which]: p.value } }, which);
}

/** «Взять из данных»: the range the core resolved for the field in the last answer. */
export function takeRange(id: number, model: BlockModelInfo | undefined, info: ShowInfo | undefined): FormIssue | null {
  const cur = currentStyle(id);
  const field = fieldOf(model, cur.colour.field);
  const why = takeReason(cur.colour, field, info);
  if (why) return asIssue("from", why);
  return put(id, takeFromData(cur.colour, field, info)!, "from");
}

/** «Сбросить»: the core takes the minimum and the maximum of the field again. */
export const resetRange = (id: number): FormIssue | null => put(id, { colour: { from: null, to: null } }, "from");

/** The breaks of the classes typed: numbers by «;» strictly ascending (the core's rule). */
export function commitBreaks(id: number, model: BlockModelInfo | undefined, text: string): FormIssue | null {
  const p = parseBreaks(text);
  if (!p.ok) return p.issue;
  const cur = currentStyle(id);
  return checked(id, model, breaksPatch(p.value, fieldOf(model, cur.colour.field), cur), "breaks");
}

/** A palette filled into the colours (a scale — 256, breaks — one each, categories — one per code). */
export function setPalette(id: number, model: BlockModelInfo | undefined, palette: string): FormIssue | null {
  const cur = currentStyle(id);
  const field = fieldOf(model, cur.colour.field);
  if (palette === PROJECT_PALETTE) {
    const categories = Object.fromEntries(Object.entries(cur.colour.categories).map(([k, v]) => [k, { ...v, colour: null }]));
    return put(id, { colour: { palette, colours: [], categories } }, "general");
  }
  return put(id, { colour: paletteFill(cur.colour, field, palette) }, "general");
}

/** The colour of a category (null — the brand's) or its «показать». */
export function setCategory(id: number, code: number, part: { colour?: string | null; on?: boolean }): FormIssue | null {
  let colour = part.colour;
  if (typeof colour === "string") {
    const ok = normalizeColor(colour);
    if (!ok) return asIssue("general", "Цвет — в виде #RRGGBB, например #1F6BFF.");
    colour = ok;
  }
  return put(id, categoryPatch(currentStyle(id).colour, code, { ...(colour !== undefined && { colour }), ...(part.on !== undefined && { on: part.on }) }));
}

/** «Показать все» / «Скрыть все» of the categories listed. */
export const setCategoriesShown = (id: number, codes: readonly number[], on: boolean): FormIssue | null => put(id, categoriesShown(currentStyle(id).colour, codes, on));

/** The colour of a class of the breaks. */
export function setClassColour(id: number, k: number, colour: string | null): FormIssue | null {
  const c = colour === null ? null : normalizeColor(colour);
  if (colour !== null && c === null) return asIssue("general", "Цвет — в виде #RRGGBB, например #1F6BFF.");
  return put(id, classColourPatch(currentStyle(id).colour, k, c));
}

// --------------------------------------------------------------------------------------------------- the look

export type EdgesChoice = "mode" | "on" | "off";

export const edgesChoice = (s: BlockStyle): EdgesChoice => (s.edges.on === null ? "mode" : s.edges.on ? "on" : "off");

/** The edges: by the mode of the show (the shell has none, «все блоки» have them), always, never. */
export const setEdges = (id: number, choice: EdgesChoice): FormIssue | null => put(id, { edges: { on: choice === "mode" ? null : choice === "on" } }, "edge_width");

export function setEdgeColour(id: number, colour: string | null): FormIssue | null {
  const c = colour === null ? null : normalizeColor(colour);
  if (colour !== null && c === null) return asIssue("general", "Цвет рёбер — в виде #RRGGBB, например #1B222B.");
  return put(id, { edges: { colour: c } });
}

export function commitWidth(id: number, text: string): FormIssue | null {
  const p = parseWidth(text);
  return p.ok ? put(id, { edges: { width: p.value } }, "edge_width") : p.issue;
}

export const setOpacity = (id: number, v: number): FormIssue | null => (v >= 0.05 && v <= 1 ? put(id, { opacity: v }) : asIssue("general", "Непрозрачность — число от 0,05 до 1. Например 0,6."));

/** The limit of faces in millions as typed. */
export function commitLimit(id: number, text: string): FormIssue | null {
  const p = parseLimit(text);
  return p.ok ? put(id, { limit: p.value }, "limit") : p.issue;
}

export const setMode = (id: number, mode: BlockStyle["mode"]): FormIssue | null => put(id, { mode });

// ------------------------------------------------------------------------------------------ the filter, the restrictor

/** The filter of the library for the model; null — none. */
export const setFilter = (id: number, filterId: number | null): FormIssue | null => put(id, { filter_id: filterId });

export const setJoin = (id: number, join: "and" | "or"): FormIssue | null => put(id, { quick: { join } });

export const setInvert = (id: number, invert: boolean): FormIssue | null => put(id, { quick: { invert } });

/** The rows of the quick restrictor: all right — written (a blank row waits); else the issues and nothing is written. */
export function commitQuickRows(id: number, rows: readonly DraftCond[], model: BlockModelInfo | undefined): { written: boolean; issues: RowIssue[]; failure: FormIssue | null } {
  const cur = currentStyle(id);
  const got = commitRows(rows, cur.quick.join, cur.quick.invert, model);
  if (!got.ok) return { written: false, issues: got.issues, failure: null };
  const failure = put(id, { quick: { items: got.quick.items } });
  return { written: failure === null, issues: [], failure };
}

export const clearQuick = (id: number): FormIssue | null => put(id, { quick: { items: [], join: "and", invert: false } });
