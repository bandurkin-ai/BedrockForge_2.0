/**
 * The section «Блочные модели» of the data tree as data (ADR 0017 §3, decision 1): a row tells the grid and the
 * fields of a model; its menu — «Свойства», «Показать в 3D» (unavailable until Э17: the registry command
 * `blocks.show` has the stage, so the item is disabled with its reason), «Экспорт OMF…» (`omf.export` with the
 * dataset of the model), rename and delete — `object.rename` / `object.delete` {kind: "blocks"}, asked in own
 * windows. A model has no check box of visibility: it is not drawn before Э17. Pure.
 */
import { count, counted, SEPARATOR, type ObjectRow } from "../../shell/treeActions";
import type { BlockModelInfo } from "./api";

export const blockKey = (id: number): string => `bm:${id}`;

/** The reason of the disabled «Показать в 3D» as the row's tooltip tells it. */
export const SHOW_LATER = "Показ блочных моделей в 3D — этап Э17";

type Row = Pick<BlockModelInfo, "id" | "name" | "grid" | "cells" | "fields">;

/** «40 × 30 × 20» */
export const gridCounts = (b: Pick<BlockModelInfo, "grid">): string => b.grid.counts.map(count).join(" × ");

/** «40 × 30 × 20, 10 000 ячеек, 3 поля» */
export function blockSummary(b: Row): string {
  const fields = b.fields.length ? counted(b.fields.length, "поле", "поля", "полей") : "без полей";
  return `${gridCounts(b)}, ${counted(b.cells, "ячейка", "ячейки", "ячеек")}, ${fields}`;
}

export function blockMenu(b: Pick<BlockModelInfo, "id">) {
  return [
    { label: "Свойства", local: "properties" as const },
    { label: "Показать в 3D", command: "blocks.show", args: { id: b.id } },
    { label: "Экспорт OMF…", command: "omf.export", args: { dataset_ids: String(b.id) } },
    SEPARATOR,
    { label: "Переименовать…", command: "object.rename", args: { kind: "blocks", id: b.id } },
    { label: "Удалить…", command: "object.delete", args: { kind: "blocks", id: b.id } },
  ];
}

/** The row of a model: a click selects it, Enter or a double click opens «Свойства». */
export function blockRow(b: Row): ObjectRow {
  return {
    testId: `tree-block-${b.id}`,
    title: `${b.name}: ${blockSummary(b)}. Щелчок — выбор, Enter — «Свойства». ${SHOW_LATER}`,
    click: "select",
    buttons: [],
    menu: blockMenu(b),
  };
}
