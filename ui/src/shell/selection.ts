/**
 * The object of the project chosen by `object.select` (ADR 0011 §5): a wireframe or a DTM, a layer of lines, a
 * raster. It is kept in the app state (`AppState.selected`): its row is marked in the data tree, the
 * «Свойства» panel shows it (7.27), «Снять выбор» (Esc) clears it. Pure: no React.
 */

export type ObjectKind = "wireframe" | "lines" | "raster" | "blocks" | "implicit" | "resource_report" | "pit_scenario";

export interface Selected {
  kind: ObjectKind;
  id: number;
}

export const sameObject = (a: Selected | null, b: Selected | null): boolean => a?.kind === b?.kind && a?.id === b?.id;

export const isSelected = (s: Selected | null, kind: ObjectKind, id: number): boolean => s !== null && s.kind === kind && s.id === id;

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
