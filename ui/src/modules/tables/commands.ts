import type { Command } from "../../shell/commands";

const COL = { type: "string", description: "Ключ колонки (как в ответе таблицы: hole, from, c12, a:Статус…)" } as const;

/** Tools of the «Таблица» panel (ТЗ 7.10; 7.34 п. 21, 22, 31–36): the active table of the active set. */
export const commands: Command[] = [
  { id: "table.filter", title: "Фильтр таблицы", description: "Показать строки активной таблицы по условию, например «AU > 0,5 и [Литология] = \"гранит\"»; пустое — снять фильтр.", params: { where: { type: "string" } }, required: ["where"] },
  { id: "table.replace", title: "Найти и заменить", description: "Заменить значения в колонке активной таблицы (строки по текущему фильтру).", params: { column: COL, find: { type: "string" }, replace: { type: "string" }, whole: { type: "boolean" }, case: { type: "boolean" } }, required: ["column", "find", "replace"], confirm: true },
  { id: "table.computed", title: "Вычисляемое поле", description: "Добавить колонку по выражению, например «(TO - FROM) * AU».", params: { name: { type: "string" }, expression: { type: "string" } }, required: ["name", "expression"], confirm: true },
  { id: "table.column_add", title: "Новая колонка", description: "Добавить пустую колонку (число или текст).", params: { name: { type: "string" }, kind: { type: "string", enum: ["number", "text"] }, unit: { type: "string" } }, required: ["name"], confirm: true },
  { id: "table.column_rename", title: "Переименовать колонку", description: "Переименовать колонку интервальной таблицы.", params: { column: COL, name: { type: "string" } }, required: ["column", "name"], confirm: true },
  { id: "table.column_delete", title: "Удалить колонку", description: "Скрыть колонку интервальной таблицы (отмена в журнале правок вернёт её со значениями).", params: { column: COL }, required: ["column"], confirm: true },
  { id: "table.append", title: "Объединить таблицы", description: "Добавить в активную таблицу строки другой интервальной таблицы набора.", params: { source: { type: "string", description: "id таблицы-источника" } }, required: ["source"], confirm: true },
  { id: "table.merge", title: "Слияние по ключу", description: "Перенести колонки другой таблицы набора по совпадению скважины и полей ключа (from, to, номер пробы).", params: { source: { type: "string" }, key: { type: "string", description: "Поля ключа через запятую (кроме скважины)" }, columns: { type: "string", description: "Ключи колонок источника через запятую" } }, required: ["source", "columns"], confirm: true },
  { id: "table.codes_check", title: "Проверка по справочнику", description: "Значения колонки, которых нет в привязанном справочнике кодов; строки показываются фильтром.", params: { column: COL } , required: ["column"] },
  { id: "table.undo", title: "Отменить правку", description: "Отменить последнюю (или указанную) правку набора из журнала правок.", params: { edit_id: { type: "integer" } }, confirm: true },
  { id: "table.export", title: "Экспорт таблицы", description: "Сохранить активную таблицу (строки как в панели) в DAT, CSV или XLSX.", params: { format: { type: "string", enum: ["dat", "csv", "xlsx"] } }, required: ["format"], uiOnly: true },
];
