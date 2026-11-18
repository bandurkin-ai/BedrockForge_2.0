/**
 * The object of the project chosen by `object.select` (ADR 0011 §5): a wireframe or a DTM, a layer of lines, a
 * raster. Its row is marked in the data tree; the «Свойства» panel shows it (7.27). Pure: no React.
 */
import { Store } from "./store";

export type ObjectKind = "wireframe" | "lines" | "raster" | "blocks";

export interface Selected {
  kind: ObjectKind;
  id: number;
}

export const selectionStore = new Store<{ selected: Selected | null }>({ selected: null });

export function selectObject(selected: Selected | null): void {
  const cur = selectionStore.get().selected;
  if (cur?.kind === selected?.kind && cur?.id === selected?.id) return;
  selectionStore.set({ selected });
}

export const isSelected = (s: Selected | null, kind: ObjectKind, id: number): boolean => s !== null && s.kind === kind && s.id === id;

/** What the kinds are called in messages: «слой линий «Дороги»». */
export const KIND_TITLES: Record<ObjectKind, string> = {
  wireframe: "каркас",
  lines: "слой линий",
  raster: "растр",
  blocks: "блочная модель",
};

/** The selection after the lists of the project changed: an object that is gone is not selected any more. */
export function keepSelected(s: Selected | null, exists: (kind: ObjectKind, id: number) => boolean): Selected | null {
  return s && exists(s.kind, s.id) ? s : null;
}
