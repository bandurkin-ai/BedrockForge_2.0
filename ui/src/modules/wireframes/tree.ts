/**
 * The sections «Каркасы» and «ЦТМ» of the data tree as data (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5). A wireframe
 * has the check box of visibility (`layer.show {layer: "wireframe", id}`, key `wf:<id>`), its colour, the
 * sign and the menu item «Перенести в ЦТМ». A DTM is a wireframe with `dtm` set; its row has NO gear and no
 * other button: a click on the row selects it and opens its settings at the top of «Свойства», the menu has
 * «Настройки ЦТМ…» and «Вернуть в каркасы». Pure.
 */
import { counted, SEPARATOR, type ObjectRow, type TreeAction } from "../../shell/treeActions";
import type { WireframeInfo } from "./state";

export const wireframeKey = (id: number): string => `wf:${id}`;

type Row = Pick<WireframeInfo, "id" | "name" | "dtm" | "triangle_count">;

const toDtm = (w: Row): TreeAction => ({ label: "Перенести в ЦТМ", icon: "dtm", command: "wireframe.dtm", args: { wireframe_id: w.id, on: true } });

const fromDtm = (w: Row): TreeAction => ({
  label: "Вернуть в каркасы",
  command: "wireframe.dtm",
  args: { wireframe_id: w.id, on: false },
  confirm: {
    title: "Вернуть в каркасы",
    message: `«${w.name}» вернётся в раздел «Каркасы». Настройки рельефа сбросятся, растр с него снимется.`,
    okLabel: "Вернуть",
  },
});

const show = (w: Row, visible: boolean): TreeAction => ({
  label: visible ? "Скрыть в 3D" : "Показать в 3D",
  command: "layer.show",
  args: { layer: "wireframe", id: w.id, visible: !visible },
});

const EDIT: TreeAction[] = [SEPARATOR, { label: "Переименовать…", local: "rename" }, { label: "Удалить…", local: "delete" }];

/** The row of a wireframe in «Каркасы», or — with `dtm` set — in «ЦТМ». */
export function wireframeRow(w: Row, visible: boolean): ObjectRow {
  const size = counted(w.triangle_count, "треугольник", "треугольника", "треугольников");
  if (w.dtm)
    return {
      testId: `tree-dtm-${w.id}`,
      title: `${w.name}: ${size}. Щелчок — настройки ЦТМ в «Свойствах», галочка — показать в 3D`,
      click: "properties",
      buttons: [], // Д-07: no gear — the click on the row opens the settings
      menu: [{ label: "Настройки ЦТМ…", local: "properties" }, show(w, visible), fromDtm(w), ...EDIT],
    };
  return {
    testId: `tree-wireframe-${w.id}`,
    title: `${w.name}: ${size}. Щелчок — выбор, галочка — показать в 3D`,
    click: "select",
    buttons: [toDtm(w)],
    menu: [{ label: "Свойства", local: "properties" }, show(w, visible), toDtm(w), ...EDIT],
  };
}
