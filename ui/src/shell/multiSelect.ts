/**
 * A click on a row of the data tree with Ctrl or Shift (Д-44 А5): the glue between the rows, the set of the chosen
 * objects (`selection.ts`) and `AppState.selected`. The main object is always chosen by the registry command
 * `object.select`, so everything that reads a single selection (3D highlight, «Свойства», commands without
 * arguments) keeps working; the set only adds the other rows. «Снять выбор» (Esc) clears both.
 */
import type { App } from "./app";
import { needProject } from "./objectRun";
import { multiStore, pickSelection, selectedItems, type Mods, type ObjectKind, type Picked, type Selected } from "./selection";

/** The attributes of a row that can be chosen: the tree reads the rows of a section from the page. */
export const PICK_KIND = "data-pick-kind";
export const PICK_ID = "data-pick-id";

/** The rows that can be chosen in the section of `row`, in the order they are drawn (collapsed folders are not drawn). */
export function visibleRows(row: Element | null): Selected[] {
  const section = row?.closest("[data-tree-section]");
  if (!section) return [];
  return [...section.querySelectorAll<HTMLElement>(`[${PICK_KIND}]`)].map((el) => ({ kind: el.getAttribute(PICK_KIND) as ObjectKind, id: Number(el.getAttribute(PICK_ID)) }));
}

/** A plain click: one object, the anchor of the next Shift range. `choose` is what the row did before (`onSelect`). */
export function pickPlain(app: App, pick: Selected, choose: (() => void) | undefined): void {
  const was = selectedItems(app.store.get().selected).length > 1;
  multiStore.set({ items: [], primary: null, anchor: pick });
  choose?.();
  // the set was drawn on the rows: they are drawn again even when the main object stays the same
  if (was) app.store.set((s) => ({ selected: s.selected ? { ...s.selected } : s.selected }));
}

/** The rows of the clicked section while a click or a key runs the command: the handler cannot see the page. */
let rowsHint: readonly Selected[] | null = null;

/** The rows of the section where `pick` is drawn (the first such row of the page; `rowsHint` knows the clicked one). */
function visibleOf(pick: Selected): readonly Selected[] {
  if (rowsHint) return rowsHint;
  if (typeof document === "undefined") return [];
  return visibleRows(document.querySelector(`[${PICK_KIND}="${pick.kind}"][${PICK_ID}="${pick.id}"]`));
}

/** Chooses `result`: the main object by `object.select`, the set next to it. */
async function applyPicked(app: App, result: Picked): Promise<string> {
  if (!result.primary) {
    multiStore.set({ items: [], primary: null, anchor: result.anchor });
    await app.registry.execute("selection.clear", {});
    return "Выбор снят";
  }
  const r = await app.registry.execute("object.select", { kind: result.primary.kind, id: result.primary.id });
  if (!r.ok) throw new Error(r.result); // the registry has logged it; the choice stays as it was
  // a copy of the main object: its identity ties the set to this choice and makes the rows draw again
  const primary = { ...result.primary };
  multiStore.set({ items: result.items, primary, anchor: result.anchor });
  app.store.set({ selected: primary });
  return `Выбрано объектов: ${result.items.length}`;
}

const pickOf = (a: Record<string, unknown>): Selected => ({ kind: a.kind as ObjectKind, id: Number(a.id) });

/**
 * `tree.select_range` and `tree.select_toggle` (Shift and Ctrl on the rows of the data tree, the keys of the tree):
 * the choice of several objects is a registry command like any other (Д-44 А5; ТЗ 17: a command is a registry entry).
 */
export function bindTreeSelection(app: App): void {
  const run = (a: Record<string, unknown>, mods: Mods) => {
    const pick = pickOf(a);
    return applyPicked(app, pickSelection(selectedItems(app.store.get().selected), multiStore.get().anchor, pick, mods, visibleOf(pick)));
  };
  app.registry.bind("tree.select_range", (a) => run(a, { shift: true, ctrl: a.add === true }), needProject);
  app.registry.bind("tree.select_toggle", (a) => run(a, { shift: false, ctrl: true }), needProject);
}

/** A click with Ctrl and/or Shift (or the same keys of the tree): by the registry commands, over the rows `visible`. */
export async function pickMulti(app: App, pick: Selected, mods: Mods, visible: readonly Selected[]): Promise<void> {
  const args = { kind: pick.kind, id: pick.id };
  rowsHint = visible;
  try {
    if (mods.shift) await app.registry.execute("tree.select_range", mods.ctrl ? { ...args, add: true } : args);
    else await app.registry.execute("tree.select_toggle", args);
  } finally {
    rowsHint = null;
  }
}
