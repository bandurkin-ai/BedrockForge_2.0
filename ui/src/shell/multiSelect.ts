/**
 * A click on a row of the data tree with Ctrl or Shift (Д-44 А5): the glue between the rows, the set of the chosen
 * objects (`selection.ts`) and `AppState.selected`. The main object of a layer of lines, a raster, a wireframe or a block
 * model is chosen by the registry command `object.select`, so everything that reads a single selection (3D highlight,
 * «Свойства», commands without arguments) keeps working; the set only adds the other rows. The rows of every other
 * section (sets of holes, trends, views, filters…) have no such selection: they are chosen as a group (`groupStore`) next
 * to the choice the section makes itself. «Снять выбор» (Esc) clears both.
 */
import type { App } from "./app";
import { needProject } from "./objectRun";
import {
  groupItems,
  groupStore,
  isObjectMulti,
  multiStore,
  NO_GROUP,
  pickSelection,
  selectedItems,
  sameItem,
  type Group,
  type Mods,
  type Picked,
  type RowRef,
  type Selected,
} from "./selection";

/** The attributes of a row that can be chosen: the tree reads the rows of a section from the page. */
export const PICK_KIND = "data-pick-kind";
export const PICK_ID = "data-pick-id";

/** The rows that can be chosen in the section of `row`, in the order they are drawn (collapsed folders are not drawn). */
export function visibleRows(row: Element | null): RowRef[] {
  const section = row?.closest("[data-tree-section]");
  if (!section) return [];
  return [...section.querySelectorAll<HTMLElement>(`[${PICK_KIND}]`)].map((el) => ({ kind: el.getAttribute(PICK_KIND) as RowRef["kind"], id: Number(el.getAttribute(PICK_ID)) }));
}

/** Is the row of the page still drawn (a deleted object, a folder or a section that was collapsed — no). */
export function rowDrawn(row: RowRef): boolean {
  if (typeof document === "undefined") return true;
  return document.querySelector(`[${PICK_KIND}="${row.kind}"][${PICK_ID}="${row.id}"]`) !== null;
}

/** The group after a row left the page: it is not chosen any more (the kind of the group stays). */
export function forgetRow(row: RowRef): void {
  const g = groupStore.get();
  if (g.items.some((x) => sameItem(x, row))) groupStore.set({ items: g.items.filter((x) => !sameItem(x, row)) });
}

export const clearGroup = (anchor: RowRef | null = null): void => {
  const g = groupStore.get();
  if (g.items.length || g.kind !== null || g.anchor !== anchor) groupStore.set({ ...NO_GROUP, anchor });
};

/** A plain click: one object, the anchor of the next Shift range. `choose` is what the row did before (`onSelect`). */
export function pickPlain(app: App, pick: RowRef, choose: (() => void) | undefined): void {
  const was = selectedItems(app.store.get().selected).length > 1;
  if (!isObjectMulti(pick.kind)) {
    // the row of a section without `object.select`: a group of one; the main object of the tree stays what the row makes it
    multiStore.set({ items: [], primary: null, anchor: null });
    groupStore.set({ items: [pick], anchor: pick, kind: pick.kind });
  } else {
    clearGroup();
    multiStore.set({ items: [], primary: null, anchor: pick as Selected });
  }
  choose?.();
  // the set was drawn on the rows: they are drawn again even when the main object stays the same
  if (was) app.store.set((s) => ({ selected: s.selected ? { ...s.selected } : s.selected }));
}

/** The rows of the clicked section while a click or a key runs the command: the handler cannot see the page. */
let rowsHint: readonly RowRef[] | null = null;

/** The rows of the section where `pick` is drawn (the first such row of the page; `rowsHint` knows the clicked one). */
function visibleOf(pick: RowRef): readonly RowRef[] {
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

/** The group after a click: the rows of its kind in the order of the section; a row of another kind begins a new group. */
export function pickGroup(g: Group, clicked: RowRef, mods: Mods, visible: readonly RowRef[]): Group {
  const same = g.kind === clicked.kind;
  const rows = visible.filter((x) => x.kind === clicked.kind);
  const picked = pickSelection(same ? g.items : [], same ? g.anchor : null, clicked, mods, rows);
  return { items: picked.items, anchor: picked.anchor, kind: clicked.kind };
}

async function applyGroup(app: App, clicked: RowRef, mods: Mods): Promise<string> {
  const next = pickGroup(groupStore.get(), clicked, mods, visibleOf(clicked));
  // the object of the tree chosen before is not a part of the group: «Свойства» are the group's now
  if (app.store.get().selected !== null || multiStore.get().items.length) {
    multiStore.set({ items: [], primary: null, anchor: null });
    app.store.set({ selected: null });
  }
  groupStore.set(next);
  return next.items.length ? `Выбрано объектов: ${next.items.length}` : "Выбор снят";
}

const pickOf = (a: Record<string, unknown>): RowRef => ({ kind: a.kind as RowRef["kind"], id: Number(a.id) });

/**
 * `tree.select_range` and `tree.select_toggle` (Shift and Ctrl on the rows of the data tree, the keys of the tree):
 * the choice of several objects is a registry command like any other (Д-44 А5; ТЗ 17: a command is a registry entry).
 */
export function bindTreeSelection(app: App): void {
  const run = (a: Record<string, unknown>, mods: Mods) => {
    const pick = pickOf(a);
    if (!isObjectMulti(pick.kind)) return applyGroup(app, pick, mods);
    // a layer, a raster, a wireframe or a model after a group: the group is dropped
    clearGroup();
    const sel = pick as Selected;
    const rows = visibleOf(pick).filter((x): x is Selected => isObjectMulti(x.kind));
    return applyPicked(app, pickSelection(selectedItems(app.store.get().selected), multiStore.get().anchor, sel, mods, rows));
  };
  app.registry.bind("tree.select_range", (a) => run(a, { shift: true, ctrl: a.add === true }), needProject);
  app.registry.bind("tree.select_toggle", (a) => run(a, { shift: false, ctrl: true }), needProject);

  // the group follows the choice of the app: any other object chosen (a click in the 3D window, a command) drops it,
  // the project that closes drops it, and the shell knows how many rows it has (the availability of «Снять выбор»)
  let selected = app.store.get().selected;
  let project = app.store.get().projectName;
  app.store.subscribe(() => {
    const s = app.store.get();
    if (s.selected !== selected) {
      selected = s.selected;
      const g = groupStore.get();
      const now = selected;
      if (now && g.kind !== null && !g.items.some((x) => sameItem(x, now))) clearGroup();
    }
    if (s.projectName !== project) {
      project = s.projectName;
      clearGroup();
    }
  });
  groupStore.subscribe(() => {
    const n = groupItems().length;
    if (app.store.get().groupSize !== n) app.store.set({ groupSize: n });
  });
}

/** `selection.clear` also drops the group. Bound after the modules have bound theirs (`createApp`). */
export function bindGroupClear(app: App): void {
  const prev = app.registry.get("selection.clear");
  if (!prev?.run) return;
  const run = prev.run;
  app.registry.bind(
    "selection.clear",
    async (a, source) => {
      const had = groupStore.get().items.length > 0 || groupStore.get().kind !== null;
      clearGroup();
      const out = await run(a, source);
      return had && out === "Ничего не выбрано" ? "Выбор снят" : out;
    },
    prev.unavailable,
  );
}

/** A click with Ctrl and/or Shift (or the same keys of the tree): by the registry commands, over the rows `visible`. */
export async function pickMulti(app: App, pick: RowRef, mods: Mods, visible: readonly RowRef[]): Promise<void> {
  const args = { kind: pick.kind, id: pick.id };
  rowsHint = visible;
  try {
    if (mods.shift) await app.registry.execute("tree.select_range", mods.ctrl ? { ...args, add: true } : args);
    else await app.registry.execute("tree.select_toggle", args);
  } finally {
    rowsHint = null;
  }
}
