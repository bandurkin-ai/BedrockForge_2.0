/**
 * The sections «Каркасы» and «ЦТМ» of the data tree as data (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5). A wireframe
 * has the check box of visibility (`layer.show {layer: "wireframe", id}`, key `wf:<id>`), its colour, the
 * button and the menu item «Перенести в ЦТМ». A DTM is a wireframe with `dtm` set; its row has NO gear: a
 * click on the row selects it and opens its settings at the top of «Свойства», the menu has «Настройки ЦТМ…»
 * and «Вернуть в каркасы». Pure.
 */
import { counted, SEPARATOR, type TreeAction } from "../../shell/treeActions";
import type { WireframeInfo } from "./state";

export const wireframeKey = (id: number): string => `wf:${id}`;

export function wireframeTitle(w: WireframeInfo): string {
  const size = counted(w.triangle_count, "треугольник", "треугольника", "треугольников");
  const what = w.dtm ? "Щелчок — настройки ЦТМ в «Свойствах»" : "Щелчок — выбор";
  return `${w.name}: ${size}. ${what}, галочка — показать в 3D`;
}

const toDtm = (w: Pick<WireframeInfo, "id">): TreeAction => ({ label: "Перенести в ЦТМ", command: "wireframe.dtm", args: { wireframe_id: w.id, on: true } });

const show = (w: Pick<WireframeInfo, "id">, visible: boolean): TreeAction => ({
  label: visible ? "Скрыть в 3D" : "Показать в 3D",
  command: "layer.show",
  args: { layer: "wireframe", id: w.id, visible: !visible },
});

const EDIT: TreeAction[] = [SEPARATOR, { label: "Переименовать…", local: "rename" }, { label: "Удалить…", local: "delete" }];

/** Buttons at the right of a row of «Каркасы»: the sign «Перенести в ЦТМ» (ТЗ 7.20). */
export function wireframeButtons(w: Pick<WireframeInfo, "id">): TreeAction[] {
  return [toDtm(w)];
}

export function wireframeMenu(w: Pick<WireframeInfo, "id">, visible: boolean): TreeAction[] {
  return [{ label: "Свойства", local: "properties" }, show(w, visible), toDtm(w), ...EDIT];
}

/** Buttons at the right of a row of «ЦТМ»: none — the gear is gone, the click on the row opens the settings (Д-07). */
export function dtmButtons(_w: Pick<WireframeInfo, "id">): TreeAction[] {
  return [];
}

export function dtmMenu(w: Pick<WireframeInfo, "id" | "name">, visible: boolean): TreeAction[] {
  return [
    { label: "Настройки ЦТМ…", local: "properties" },
    show(w, visible),
    {
      label: "Вернуть в каркасы",
      command: "wireframe.dtm",
      args: { wireframe_id: w.id, on: false },
      confirm: {
        title: "Вернуть в каркасы",
        message: `«${w.name}» вернётся в раздел «Каркасы». Настройки рельефа сбросятся, растр с него снимется.`,
        okLabel: "Вернуть",
      },
    },
    ...EDIT,
  ];
}
