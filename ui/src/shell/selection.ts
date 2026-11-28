/**
 * The object of the project chosen by `object.select` (ADR 0011 §5): a wireframe or a DTM, a layer of lines, a
 * raster. It is kept in the app state (`AppState.selected`): its row is marked in the data tree, the
 * «Свойства» panel shows it (7.27), «Снять выбор» (Esc) clears it. Pure: no React.
 */

import { Store } from "./store";

export type ObjectKind = "wireframe" | "lines" | "raster" | "blocks" | "implicit" | "resource_report" | "pit_scenario";

export interface Selected {
  kind: ObjectKind;
  id: number;
}

export const sameObject = (a: Selected | null, b: Selected | null): boolean => a?.kind === b?.kind && a?.id === b?.id;

/** Is the row of the object marked: it is the main one, or it belongs to the chosen set (A5, Д-44). */
export function isSelected(s: Selected | null, kind: ObjectKind, id: number, multi: Multi = multiStore.get()): boolean {
  if (s === null) return false;
  if (s.kind === kind && s.id === id) return true;
  return multi.primary === s && multi.items.some((x) => x.kind === kind && x.id === id);
}

// ---- several objects at once (Д-44 А5): Ctrl adds or takes away, Shift takes a range of the section --------

/**
 * The set of the chosen objects next to `AppState.selected`. `selected` stays the main object — what every
 * reader of a single selection sees. The set counts only while `AppState.selected` is the very object `primary`
 * (compared by identity): any other `object.select` — in the tree, in the 3D window, by a command — makes it
 * stale at once, so the set never outlives the choice it belongs to. `anchor` is where a Shift range starts.
 */
export interface Multi {
  items: readonly Selected[];
  primary: Selected | null;
  anchor: Selected | null;
}

export const NO_MULTI: Multi = { items: [], primary: null, anchor: null };
export const multiStore = new Store<Multi>(NO_MULTI);

export const itemKey = (s: Selected): string => `${s.kind}:${s.id}`;

export const sameItem = (a: Selected, b: Selected): boolean => a.kind === b.kind && a.id === b.id;

/** The chosen objects: the whole set while it is valid, else the main object alone (or nothing). */
export function selectedItems(selected: Selected | null, multi: Multi = multiStore.get()): readonly Selected[] {
  if (!selected) return [];
  return multi.primary === selected && multi.items.length > 1 ? multi.items : [selected];
}

/** «Выбранные id» of a kind: for commands that work on all the chosen objects (delete, show…). */
export function selectedIds(selected: Selected | null, kind: ObjectKind, multi: Multi = multiStore.get()): number[] {
  return selectedItems(selected, multi)
    .filter((x) => x.kind === kind)
    .map((x) => x.id);
}

export interface Mods {
  ctrl: boolean;
  shift: boolean;
}

export interface Picked {
  items: Selected[];
  /** the main object after the click; null — nothing is chosen any more */
  primary: Selected | null;
  anchor: Selected | null;
}

/**
 * What a click on a row of the tree chooses. A plain click chooses one object; Ctrl switches it in the set;
 * Shift takes the rows between the anchor and the clicked one (`visible` — the rows of one section in their order;
 * Ctrl+Shift adds the range to the set). A Shift click without an anchor in these rows is a plain click.
 */
export function pickSelection(current: readonly Selected[], anchor: Selected | null, clicked: Selected, mods: Mods, visible: readonly Selected[]): Picked {
  const one: Picked = { items: [clicked], primary: clicked, anchor: clicked };
  if (mods.shift) {
    const from = anchor ? visible.findIndex((x) => sameItem(x, anchor)) : -1;
    const to = visible.findIndex((x) => sameItem(x, clicked));
    if (from < 0 || to < 0) return one;
    const range = visible.slice(Math.min(from, to), Math.max(from, to) + 1);
    const items = mods.ctrl ? [...current.filter((c) => !range.some((r) => sameItem(r, c))), ...range] : [...range];
    return { items, primary: clicked, anchor: anchor as Selected };
  }
  if (mods.ctrl) {
    if (current.some((c) => sameItem(c, clicked))) {
      const items = current.filter((c) => !sameItem(c, clicked));
      return { items, primary: items.length ? items[items.length - 1] : null, anchor: clicked };
    }
    return { items: [...current, clicked], primary: clicked, anchor: clicked };
  }
  return one;
}

/** What the kinds are called in messages: «Выбран слой линий «Дороги»». */
export const KIND_TITLES: Record<ObjectKind, { name: string; chosen: string; missing: string }> = {
  wireframe: { name: "каркас", chosen: "Выбран", missing: "не найден" },
  lines: { name: "слой линий", chosen: "Выбран", missing: "не найден" },
  raster: { name: "растр", chosen: "Выбран", missing: "не найден" },
  blocks: { name: "блочная модель", chosen: "Выбрана", missing: "не найдена" },
  implicit: { name: "неявная модель", chosen: "Выбрана", missing: "не найдена" },
  resource_report: { name: "отчёт по ресурсам", chosen: "Выбран", missing: "не найден" },
  pit_scenario: { name: "сценарий карьера", chosen: "Выбран", missing: "не найден" },
};

/** The selection after the lists of the project changed: an object that is gone is not selected any more. */
export function keepSelected(s: Selected | null, exists: (kind: ObjectKind, id: number) => boolean): Selected | null {
  return s && exists(s.kind, s.id) ? s : null;
}

// the rows of these sections name their object in the test id; a row can also pass `pick` itself (TreeRow)
const TEST_ID_KINDS: readonly [RegExp, ObjectKind][] = [
  [/^tree-(?:wireframe|dtm)-(\d+)$/, "wireframe"],
  [/^tree-layer-(\d+)$/, "lines"],
  [/^tree-block-(\d+)$/, "blocks"],
  [/^tree-raster-(\d+)$/, "raster"],
];

/** The object of a row of the tree by its test id (`tree-layer-12` → a layer of lines №12). */
export function pickOfTestId(testId: string | undefined): Selected | undefined {
  for (const [re, kind] of TEST_ID_KINDS) {
    const m = testId ? re.exec(testId) : null;
    if (m) return { kind, id: Number(m[1]) };
  }
  return undefined;
}
