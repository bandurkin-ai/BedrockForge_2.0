/**
 * Choosing several rows of «Менеджер данных» (Д-20) as in Explorer: a click — the row alone, Ctrl — add or take
 * away, Shift — the range from the anchor (with Ctrl — added to what is chosen), Ctrl+A — every shown row, the
 * arrows, Home, End move the focus (with Shift — the range grows). After a reload or another filter only the
 * shown rows stay chosen. Pure: the order of the rows is given by the table (`viewRows`).
 */

export interface Selection {
  /** chosen ids in the order of choosing */
  ids: number[];
  /** the row a Shift range starts from */
  anchor: number | null;
  /** the row of the keyboard focus */
  focus: number | null;
}

export const EMPTY_SELECTION: Selection = { ids: [], anchor: null, focus: null };

export interface Mods {
  shift?: boolean;
  ctrl?: boolean;
}

function range(order: readonly number[], from: number | null, to: number): number[] {
  const j = order.indexOf(to);
  const i = from === null ? -1 : order.indexOf(from);
  if (j < 0) return [];
  if (i < 0) return [to];
  return i <= j ? order.slice(i, j + 1) : order.slice(j, i + 1).reverse();
}

const union = (a: readonly number[], b: readonly number[]): number[] => {
  const seen = new Set(a);
  return [...a, ...b.filter((x) => !seen.has(x))];
};

/** A click on the row `id` of the shown `order`. */
export function clickRow(sel: Selection, order: readonly number[], id: number, mods: Mods = {}): Selection {
  if (!order.includes(id)) return sel;
  if (mods.shift) {
    const span = range(order, sel.anchor, id);
    return { ids: mods.ctrl ? union(sel.ids, span) : span, anchor: sel.anchor ?? id, focus: id };
  }
  if (mods.ctrl) {
    const on = !sel.ids.includes(id);
    return { ids: on ? [...sel.ids, id] : sel.ids.filter((x) => x !== id), anchor: id, focus: id };
  }
  return { ids: [id], anchor: id, focus: id };
}

/** Ctrl+A: every shown row. */
export function selectAll(order: readonly number[], sel: Selection = EMPTY_SELECTION): Selection {
  return { ids: [...order], anchor: sel.anchor !== null && order.includes(sel.anchor) ? sel.anchor : (order[0] ?? null), focus: sel.focus ?? order[0] ?? null };
}

/** A right click: the chosen rows stay chosen if the row is among them, else the row alone (as in Explorer). */
export function contextRow(sel: Selection, order: readonly number[], id: number): Selection {
  return sel.ids.includes(id) ? { ...sel, focus: id } : clickRow(sel, order, id);
}

export type MoveKey = "up" | "down" | "home" | "end" | "pageUp" | "pageDown";

/** The keyboard: the focus moves, the row alone is chosen; with Shift the range from the anchor. */
export function moveFocus(sel: Selection, order: readonly number[], key: MoveKey, mods: Mods = {}, page = 10): Selection {
  if (!order.length) return sel;
  const at = sel.focus === null ? -1 : order.indexOf(sel.focus);
  const step: Record<MoveKey, number> = { up: at < 0 ? 0 : at - 1, down: at + 1, home: 0, end: order.length - 1, pageUp: at - page, pageDown: at < 0 ? page - 1 : at + page };
  const id = order[Math.min(order.length - 1, Math.max(0, step[key]))];
  if (mods.shift) return { ids: range(order, sel.anchor ?? sel.focus ?? id, id), anchor: sel.anchor ?? sel.focus ?? id, focus: id };
  return { ids: [id], anchor: id, focus: id };
}

/** Only the shown rows stay chosen (a reload, a search, another folder). The same object if nothing changed. */
export function pruneSelection(sel: Selection, order: readonly number[]): Selection {
  const shown = new Set(order);
  const ids = sel.ids.filter((x) => shown.has(x));
  const anchor = sel.anchor !== null && shown.has(sel.anchor) ? sel.anchor : null;
  const focus = sel.focus !== null && shown.has(sel.focus) ? sel.focus : null;
  return ids.length === sel.ids.length && anchor === sel.anchor && focus === sel.focus ? sel : { ids, anchor, focus };
}

export const isChosen = (sel: Selection, id: number): boolean => sel.ids.includes(id);

/** A Set of the chosen ids for the rows of a long table (one lookup per drawn row). */
export const chosenSet = (sel: Selection): ReadonlySet<number> => new Set(sel.ids);
