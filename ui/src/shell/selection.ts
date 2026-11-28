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

/**
 * The rows of the tree that are not chosen by `object.select`: sets of holes, views, trends, filters, estimation rows… and the
 * models, scenarios and reports that choose themselves (`selectScenarioRow`, `selectReport`). Several of them are chosen
 * as a group (`groupStore`, Д-44 А5 for every section); the four kinds of `OBJECT_MULTI` have the set next to
 * `AppState.selected` (`multiStore`).
 */
export const TREE_KINDS = [
  "set", "view", "series", "filter", "trend", "vario", "est_row", "est_plan", "sim_row", "planned", "swath", "qaqc_data", "qaqc_report",
  "display", "chart", "result", "implicit", "pit_scenario", "resource_report", "line_file", "implicit_group", "implicit_fault", "implicit_stack", "series_section",
] as const;
export type TreeKind = (typeof TREE_KINDS)[number];

/** Every row of the tree that can be chosen with Ctrl and Shift: the one list of the type, of `tree.select_*` (enum) and of the tests. */
export const PICK_KINDS = ["wireframe", "lines", "raster", "blocks", ...TREE_KINDS] as const;

/** A row of the tree that can be chosen with Ctrl and Shift. */
export type PickKind = (typeof PICK_KINDS)[number];

export interface RowRef {
  kind: PickKind;
  id: number;
}

/** The kinds whose main object is `AppState.selected` (`object.select`); every other kind is chosen as a group. */
export type MultiObjectKind = "wireframe" | "lines" | "raster" | "blocks";
const OBJECT_MULTI: readonly PickKind[] = ["wireframe", "lines", "raster", "blocks"];
export const isObjectMulti = (k: PickKind): k is MultiObjectKind => OBJECT_MULTI.includes(k);

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

export const itemKey = (s: RowRef): string => `${s.kind}:${s.id}`;

export const sameItem = (a: RowRef, b: RowRef): boolean => a.kind === b.kind && a.id === b.id;

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

export interface Picked<T extends RowRef = Selected> {
  items: T[];
  /** the main object after the click; null — nothing is chosen any more */
  primary: T | null;
  anchor: T | null;
}

/**
 * What a click on a row of the tree chooses. A plain click chooses one object; Ctrl switches it in the set;
 * Shift takes the rows between the anchor and the clicked one (`visible` — the rows of one section in their order;
 * Ctrl+Shift adds the range to the set). A Shift click without an anchor in these rows is a plain click.
 */
export function pickSelection<T extends RowRef>(current: readonly T[], anchor: T | null, clicked: T, mods: Mods, visible: readonly T[]): Picked<T> {
  const one: Picked<T> = { items: [clicked], primary: clicked, anchor: clicked };
  if (mods.shift) {
    const from = anchor ? visible.findIndex((x) => sameItem(x, anchor)) : -1;
    const to = visible.findIndex((x) => sameItem(x, clicked));
    if (from < 0 || to < 0) return one;
    const range = visible.slice(Math.min(from, to), Math.max(from, to) + 1);
    const items = mods.ctrl ? [...current.filter((c) => !range.some((r) => sameItem(r, c))), ...range] : [...range];
    return { items, primary: clicked, anchor: anchor as T };
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

/** A row named by two numbers (a group of a model, a section of a series) has one id: `pairId(model, group)`. */
export const PAIR = 1_000_000;
export const pairId = (a: number, b: number): number => a * PAIR + b;
export const unpair = (id: number): [number, number] => [Math.floor(id / PAIR), id % PAIR];

// the rows of these sections name their object in the test id; a row can also pass `pick` itself (TreeRow)
const first = (m: RegExpExecArray): number => Number(m[1]);
const TEST_ID_KINDS: readonly [RegExp, PickKind, (m: RegExpExecArray) => number][] = [
  [/^tree-(?:wireframe|dtm)-(\d+)$/, "wireframe", first],
  [/^tree-layer-(\d+)$/, "lines", first],
  [/^tree-block-(\d+)$/, "blocks", first],
  [/^tree-raster-(\d+)$/, "raster", first],
  [/^tree-set-(\d+)$/, "set", first],
  [/^view-(\d+)$/, "view", first],
  [/^series-tree-(\d+)$/, "series", first],
  [/^series-tree-(\d+)-(\d+)$/, "series_section", (m) => pairId(Number(m[1]), Number(m[2]))],
  [/^tree-filter-(\d+)$/, "filter", first],
  [/^tree-trend-(\d+)$/, "trend", first],
  [/^tree-vario-model-(\d+)$/, "vario", first],
  [/^tree-est-row-(\d+)$/, "est_row", first],
  [/^tree-est-plan-(\d+)$/, "est_plan", first],
  [/^tree-sim-row-(\d+)$/, "sim_row", first],
  [/^tree-planned-(\d+)$/, "planned", first],
  [/^tree-swath-(\d+)$/, "swath", first],
  [/^tree-qaqc-data-(\d+)$/, "qaqc_data", first],
  [/^tree-qaqc-report-(\d+)$/, "qaqc_report", first],
  [/^tree-display-(\d+)$/, "display", first],
  [/^tree-chart-(\d+)$/, "chart", first],
  [/^tree-result-(\d+)$/, "result", first],
  [/^tree-implicit-(\d+)$/, "implicit", first],
  [/^tree-implicit-(\d+)-group-(\d+)$/, "implicit_group", (m) => pairId(Number(m[1]), Number(m[2]))],
  [/^tree-implicit-(\d+)-fault-(\d+)$/, "implicit_fault", (m) => pairId(Number(m[1]), Number(m[2]))],
  [/^tree-implicit-(\d+)-stack-(\d+)$/, "implicit_stack", (m) => pairId(Number(m[1]), Number(m[2]))],
  // the wireframes and the layers under a scenario of the pit and a model are the ordinary ones of the project
  [/^tree-pit-\d+-wireframe-(\d+)$/, "wireframe", first],
  [/^tree-implicit-out-(\d+)$/, "wireframe", first],
  [/^tree-implicit-line-(\d+)$/, "lines", first],
  [/^tree-linefile-(\d+)$/, "line_file", first],
  [/^tree-pit-scenario-(\d+)$/, "pit_scenario", first],
  [/^tree-resource-report-(\d+)$/, "resource_report", first],
];

/** The object of a row of the tree by its test id (`tree-layer-12` → a layer of lines №12). */
export function pickOfTestId(testId: string | undefined): RowRef | undefined {
  for (const [re, kind, id] of TEST_ID_KINDS) {
    const m = testId ? re.exec(testId) : null;
    if (m) return { kind, id: id(m) };
  }
  return undefined;
}

// ---- the rows that `object.select` does not choose: a group of one kind (every section of the tree, Д-44 А5) ----------------

/**
 * The chosen rows of the sections whose objects are not chosen by `object.select` (sets, trends, views…). `kind` is the kind
 * the group was begun with; the rows of this kind are marked by the group alone and not by the old choice of
 * their section (`groupMark`). The group counts as a set from two rows; the plain click and the click
 * in the 3D window drop it (`multiSelect.ts`), the rows that are not drawn any more leave it (`TreeRow`).
 */
export interface Group {
  items: readonly RowRef[];
  anchor: RowRef | null;
  kind: PickKind | null;
}

export const NO_GROUP: Group = { items: [], anchor: null, kind: null };
export const groupStore = new Store<Group>(NO_GROUP);

/**
 * How the group marks a row: 1 — it is one of the chosen rows; 2 — it is not, and the group works on its kind: only the rows
 * of the group are marked «выбран», the mark the section keeps itself (the active set, the view applied last) is shown as
 * «активный» (`TreeRow`), so taking a row away never leaves two rows marked alike; 0 — no group of this kind.
 */
export function groupMark(g: Group, row: RowRef): 0 | 1 | 2 {
  if (g.kind !== row.kind) return 0;
  return g.items.some((x) => sameItem(x, row)) ? 1 : 2;
}

/** The rows of the group: more than one of them make a set. */
export const groupItems = (g: Group = groupStore.get()): readonly RowRef[] => (g.items.length > 1 ? g.items : []);
