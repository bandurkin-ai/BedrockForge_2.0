/**
 * Exchange of block models (ТЗ 8.7.7, 7.35 Д-13, Д-17; ADR 0023 §7, §12): the command `blocks.export` — a model to a file
 * of DAT, CSV, Parquet or OMF with the fields, the filter and the restrictor of the user. Without `model_id` and `path`
 * it opens the window «Экспорт блочной модели»; with them it writes the file as the job `blocks.export`. It writes
 * nothing to the project (only a file outside it), so it works in a project open for reading. The import is the
 * command `import.files {kind: "blocks"}` (`modules/importing/commands.ts`). `EXCHANGE_BUTTONS` — the buttons of the ribbon
 * (the tab «Блочные модели», the group «Обмен»; `shell/ribbonConfig.ts` puts them in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

export const commands: Command[] = [
  {
    id: "blocks.export",
    title: "Экспорт блочной модели",
    description:
      "Записать блочную модель в файл: dat (Micromine DAT), csv, parquet или omf (OMF v1). Столбцы DAT и CSV: X, Y, Z — центр блока в осях проекта, DX, DY, DZ — размер по осям модели, затем поля; " +
      "субблоки пишутся как есть (строка — блок), у Parquet добавляются idx и sx…wz. Можно выбрать поля (fields), отобрать блоки фильтром библиотеки (filter_id) и быстрым ограничителем (restrict): " +
      "записываются только прошедшие блоки. Пропуск пишется пустым значением, а не нулём; empty задаёт число вместо пропуска (DAT и CSV). OMF: нужно хотя бы одно поле, модель с субблоками — только после регуляризации. " +
      "Существующий файл заменяется после вопроса (overwrite: true — без вопроса). Без model_id и path открывает окно «Экспорт блочной модели». Datamine DM не записывается.",
    params: {
      model_id: P("integer", "номер блочной модели (раздел «Блочные модели» дерева)"),
      format: P("string", "формат файла; по умолчанию — по расширению path", { enum: ["dat", "csv", "parquet", "omf"] }),
      path: P("string", "полный путь файла (расширение формата добавляется само); не в папке проекта"),
      fields: P("string", "поля через запятую, в нужном порядке; без fields — все поля модели"),
      filter_id: P("integer", "номер фильтра библиотеки (раздел «Фильтры» дерева): пишутся блоки, которые он пропускает"),
      restrict: P("string", 'быстрый ограничитель — дерево правил в JSON, например {"cond": {"field": "AU", "op": ">", "value": 1}}; вместе с фильтром — оба сразу (И)'),
      header: P("string", "CSV: «plain» — одна строка имён; «micromine» — три строки (имена, типы, сетка)", { enum: ["plain", "micromine"] }),
      encoding: P("string", "DAT: cp1251, bocu1, utf-8; CSV: utf-8-sig, utf-8, cp1251, cp866, koi8-r, utf-16"),
      delimiter: P("string", "CSV: «;» (по умолчанию), «,», tab (табуляция), «|»"),
      decimal: P("string", "CSV: «,» (по умолчанию) или «.»", { enum: [",", "."] }),
      empty: P("number", "DAT и CSV: число, которым пишется пропуск (например -99); без него пропуск — пустое значение"),
      overwrite: P("boolean", "заменить существующий файл без вопроса"),
    },
  },
];

/** The ribbon: the tab «Блочные модели», the group «Обмен». */
export interface ExchangeButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const EXCHANGE_GROUP_TITLE = "Обмен";

export const EXCHANGE_BUTTONS: readonly ExchangeButton[] = [
  { command: "import.files", args: { kind: "blocks" }, label: "Импорт модели", icon: "import" as IconName, hint: "Блочная модель из Micromine DAT и CSV, Datamine .dm и .fbm: роли столбцов, единицы, сетка и поворот в мастере" },
  { command: "blocks.export", label: "Экспорт модели", icon: "export" as IconName, hint: "Модель в DAT, CSV, Parquet или OMF: поля, фильтр, ограничитель, кодировка" },
];
