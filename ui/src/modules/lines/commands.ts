import type { Command } from "../../shell/commands";
import { LINES_FORMAT_IDS } from "./export";

/** String editor (Э9), the export of layers (Э7) and the map of differences of a point cloud (Э7.1, ADR 0014 §5;
 * handlers — `bind.ts`). */
export const commands: Command[] = [
  { id: "lines.new_layer", title: "Новый слой", description: "Пустой слой линий и режим правки.", params: { name: { type: "string" } }, stage: "Э9" },
  { id: "lines.save", title: "Сохранить слой", description: "Сохранить слой: новая версия.", confirm: true, stage: "Э9" },
  { id: "lines.edit", title: "Редактировать", description: "Редактор стрингов на выбранном слое.", params: { layer_id: { type: "integer" } }, stage: "Э9" },
  { id: "lines.tool", title: "Инструмент", description: "Инструмент редактора стрингов.", params: { tool: { type: "string" } }, required: ["tool"], stage: "Э9" },
  { id: "lines.add_line", title: "Добавить линию", description: "Добавить линию по координатам (JSON-массив точек).", params: { points: { type: "string" }, closed: { type: "boolean" } }, required: ["points"], stage: "Э9" },
  { id: "lines.undo", title: "Отменить", description: "Отменить последнюю правку.", stage: "Э9" },
  { id: "lines.redo", title: "Повторить", description: "Повторить отменённую правку.", stage: "Э9" },
  { id: "lines.measure", title: "Измерить", description: "Длина, площадь, азимут выбранного.", stage: "Э9" },
  {
    id: "lines.export",
    title: "Экспорт линий",
    description:
      "Слои линий и точек в файл: Micromine STR (str), Surpac STR (surpac), DXF (dxf), CSV (csv) или Micromine DAT (dat); форматы STR пишут только линии. Облако точек — отдельно, в LAS (las: по исходному файлу или новый) или CSV (X;Y;Z;класс;h). layer_id — один слой, file_id — все слои файла линий, без них — выбранный слой. Открывает окно «Экспорт линий» с этим форматом и слоями: файл выбирает пользователь, экспорт идёт как задание.",
    params: { format: { type: "string", enum: LINES_FORMAT_IDS }, layer_id: { type: "integer" }, file_id: { type: "integer" } },
    stage: "Э7",
  },
  {
    id: "cloud.dz",
    title: "Карта разностей",
    description:
      "Отклонение точек облака от поверхности: dz = Z точки − высота каркаса или ЦТМ под ней (над ней); у облака появляется режим цвета «Отклонение от ЦТМ», в «Свойствах» — СКО, P95 и наибольшее по модулю отклонение по точкам земли (класс 2) и число точек вне поверхности. Прежние отклонения облака заменяются. Только cloud_id (или без параметров) — открыть окно «Карта разностей» (поверхность по умолчанию — та, с которой сравнивали в прошлый раз, или последняя ЦТМ из этого облака); с wireframe_id (каркас или ЦТМ из раздела «Каркасы» или «ЦТМ») — сразу задание «Карта разностей» (ход — на вкладке «Задания»). Без cloud_id — выбранное облако или единственное облако проекта.",
    params: { cloud_id: { type: "integer" }, wireframe_id: { type: "integer" } },
    stage: "Э7.1",
  },
  { id: "lines.finish", title: "Завершить", description: "Выйти из правки.", uiOnly: true, stage: "Э9" },
  { id: "lines.move_selected", title: "Сдвинуть", description: "Сдвинуть выбранное.", uiOnly: true, stage: "Э9" },
  { id: "lines.versions", title: "Версии", description: "Журнал версий слоя.", uiOnly: true, stage: "Э9" },
];
