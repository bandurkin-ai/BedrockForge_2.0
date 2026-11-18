import type { Command } from "../../shell/commands";

/** DTM (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5). The settings themselves are changed by `wireframe.dtm`
 * (`modules/wireframes/commands.ts`); «Построить ЦТМ» is the dialog `model.dtm` (handlers — `bind.ts`). */
export const commands: Command[] = [
  {
    id: "dtm.settings",
    title: "Настройки ЦТМ",
    description:
      "Открыть настройки ЦТМ вверху панели «Свойства»: растр на рельефе, «Скрыть данные за ЦТМ», цвет и непрозрачность растра, тени рельефа, атрибуты. Без wireframe_id — выбранная ЦТМ (или единственная ЦТМ проекта). Сами настройки меняет wireframe.dtm.",
    params: { wireframe_id: { type: "integer" } },
    stage: "Э7",
  },
];
