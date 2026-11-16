import type { Command } from "../../shell/commands";

/** String editor (Э9). */
export const commands: Command[] = [
  { id: "lines.new_layer", title: "Новый слой", description: "Пустой слой линий и режим правки.", params: { name: { type: "string" } }, stage: "Э9" },
  { id: "lines.save", title: "Сохранить слой", description: "Сохранить слой: новая версия.", confirm: true, stage: "Э9" },
  { id: "lines.edit", title: "Редактировать", description: "Редактор стрингов на выбранном слое.", params: { layer_id: { type: "integer" } }, stage: "Э9" },
  { id: "lines.tool", title: "Инструмент", description: "Инструмент редактора стрингов.", params: { tool: { type: "string" } }, required: ["tool"], stage: "Э9" },
  { id: "lines.add_line", title: "Добавить линию", description: "Добавить линию по координатам (JSON-массив точек).", params: { points: { type: "string" }, closed: { type: "boolean" } }, required: ["points"], stage: "Э9" },
  { id: "lines.undo", title: "Отменить", description: "Отменить последнюю правку.", stage: "Э9" },
  { id: "lines.redo", title: "Повторить", description: "Повторить отменённую правку.", stage: "Э9" },
  { id: "lines.measure", title: "Измерить", description: "Длина, площадь, азимут выбранного.", stage: "Э9" },
  { id: "lines.export", title: "Экспорт линий", description: "Экспорт слоя в STR или DXF.", params: { format: { type: "string", enum: ["str", "dxf"] }, layer_id: { type: "integer" } }, required: ["format"], stage: "Э7" },
  { id: "lines.finish", title: "Завершить", description: "Выйти из правки.", uiOnly: true, stage: "Э9" },
  { id: "lines.move_selected", title: "Сдвинуть", description: "Сдвинуть выбранное.", uiOnly: true, stage: "Э9" },
  { id: "lines.versions", title: "Версии", description: "Журнал версий слоя.", uiOnly: true, stage: "Э9" },
];
