/**
 * A click on a row of the data tree with Ctrl or Shift (Д-44 А5): the glue between the rows, the set of the chosen
 * objects (`selection.ts`) and `AppState.selected`. The main object is always chosen by the registry command
 * `object.select`, so everything that reads a single selection (3D highlight, «Свойства», commands without
 * arguments) keeps working; the set only adds the other rows. «Снять выбор» (Esc) clears both.
 */
import type { App } from "./app";
import { multiStore, pickSelection, selectedItems, type Mods, type ObjectKind, type Selected } from "./selection";

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

/** A click with Ctrl and/or Shift. */
export async function pickMulti(app: App, pick: Selected, mods: Mods, visible: readonly Selected[]): Promise<void> {
  const result = pickSelection(selectedItems(app.store.get().selected), multiStore.get().anchor, pick, mods, visible);
  if (!result.primary) {
    multiStore.set({ items: [], primary: null, anchor: result.anchor });
    await app.registry.execute("selection.clear", {});
    return;
  }
  const r = await app.registry.execute("object.select", { kind: result.primary.kind, id: result.primary.id });
  if (!r.ok) return; // the registry has logged it; the choice stays as it was
  // a copy of the main object: its identity ties the set to this choice and makes the rows draw again
  const primary = { ...result.primary };
  multiStore.set({ items: result.items, primary, anchor: result.anchor });
  app.store.set({ selected: primary });
}
