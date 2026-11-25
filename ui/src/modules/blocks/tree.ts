/**
 * The section «Блочные модели» of the data tree as data (ADR 0017 §3, decision 1): a row tells the grid and the
 * fields of a model; its menu — «Свойства», «Показать в 3D» / «Скрыть в 3D» (`blocks.show`, Э17: the shell of the
 * model in the 3D window, its style saved in the view of the project), «Экспорт OMF…» (`omf.export` with the dataset
 * of the model), rename and delete — `object.rename` / `object.delete` {kind: "blocks"}, asked in own windows. A row
 * has no check box of visibility: the menu and the ribbon show and hide a model. Pure.
 */
import { count, counted, SEPARATOR, type ObjectRow } from "../../shell/treeActions";
import type { BlockModelInfo } from "./api";

export const blockKey = (id: number): string => `bm:${id}`;

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
    { label: "Показать в 3D", command: "blocks.show", args: { model_id: b.id } },
    { label: "Скрыть в 3D", command: "blocks.show", args: { model_id: b.id, visible: false } },
    { label: "Экспорт OMF…", command: "omf.export", args: { dataset_ids: String(b.id) } },
    { label: "Экспорт…", command: "blocks.export", args: { model_id: String(b.id) } },
    SEPARATOR,
    { label: "Переименовать…", command: "object.rename", args: { kind: "blocks", id: b.id } },
    { label: "Удалить…", command: "object.delete", args: { kind: "blocks", id: b.id } },
  ];
}

/** The row of a model: a click selects it, Enter or a double click opens «Свойства». */
export function blockRow(b: Row): ObjectRow {
  return {
    testId: `tree-block-${b.id}`,
    title: `${b.name}: ${blockSummary(b)}. Щелчок — выбор, Enter — «Свойства», меню — показать в 3D`,
    click: "select",
    buttons: [],
    menu: blockMenu(b),
  };
}
