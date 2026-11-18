import type { Command } from "../../shell/commands";

/** String editor (Э9) and the export of layers (Э7; handlers — `bind.ts`). */
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
      "Слои линий и точек в файл: Micromine STR (str), Surpac STR (surpac), DXF (dxf), CSV (csv) или Micromine DAT (dat); форматы STR пишут только линии. layer_id — один слой, file_id — все слои файла линий, без них — выбранный слой. Открывает окно «Экспорт линий» с выбором слоёв и файла; с path (полный путь к файлу) экспорт запускается сразу как задание.",
    params: { format: { type: "string", enum: ["str", "surpac", "dxf", "csv", "dat"] }, layer_id: { type: "integer" }, file_id: { type: "integer" }, path: { type: "string", description: "Полный путь к файлу; без него откроется окно" } },
    stage: "Э7",
  },
  { id: "lines.finish", title: "Завершить", description: "Выйти из правки.", uiOnly: true, stage: "Э9" },
  { id: "lines.move_selected", title: "Сдвинуть", description: "Сдвинуть выбранное.", uiOnly: true, stage: "Э9" },
  { id: "lines.versions", title: "Версии", description: "Журнал версий слоя.", uiOnly: true, stage: "Э9" },
];
