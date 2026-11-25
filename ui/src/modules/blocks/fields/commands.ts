/**
 * The fields of a block model (ТЗ 8.7.4, 8.7.5; ADR 0023 §7, §9, §13): the commands `blocks.calc`, `blocks.density`,
 * `blocks.classify`, `blocks.scheme.save`, `blocks.field.props`, `blocks.field.rebuild`, `blocks.field.delete`. The ribbon
 * (the tab «Блочные модели», the group «Поля»), Ctrl+K, the keys of the user and the assistant run the same commands.
 * A command without its required arguments opens the window «Поля блочной модели» on its tab (as ADR 0021 п. 9). The
 * commands that write are `confirm` (a call of the assistant is asked first); a field that is there is replaced only
 * after a question to the person (№ 69) or with `replace: true`. Parameters are flat (`ParamSchema` has no objects):
 * trees and lists are JSON texts. `FIELD_BUTTONS` — the buttons of the ribbon (`shell/ribbonConfig.ts` puts them in,
 * by the orchestrator).
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const MODEL_ID = P("integer", "номер блочной модели (раздел «Блочные модели» дерева)");
const FILTER_ID = P("integer", "номер фильтра библиотеки (раздел «Фильтры» дерева): считаются блоки, которые он пропускает");
const RESTRICT = P("string", 'быстрый ограничитель — дерево правил в JSON, например {"cond": {"field": "DOM", "op": "=", "value": "Руда"}}; вместе с фильтром — оба сразу (И)');
const REPLACE = P("boolean", "заменить поле, если оно уже есть (без этого ответ 409: «поле уже есть»); старые единица и описание не переносятся");

export const commands: Command[] = [
  {
    id: "blocks.calc",
    title: "Калькулятор полей",
    description:
      "Калькулятор полей блочной модели (8.7.4): новое поле по выражению. Операторы + − * / ^ ( ); = <> < <= > >=; AND OR NOT; IN ('A', 'B'); функции ABS SQRT LN LOG10 EXP MIN MAX ROUND(x, n) " +
      "FLOOR CEIL CLIP(x, a, b) IF(условие, a, b) ISNULL(x) COALESCE(x, y); поле — имя или [имя с пробелами]; строки в одинарных кавычках; до 4000 символов. Пропуск (нет значения) не превращается в ноль: " +
      "арифметика с пропуском даёт пропуск, сравнение с пропуском — ложь, IF с пропуском в условии — пропуск. Тип поля: f8 (по умолчанию для числа), f4, i4, i2, i1 (по умолчанию для условия), " +
      "cat (категория — из IF с текстом: IF(AU > 1, 'руда', 'порода')). Вне ограничителя (фильтр библиотеки и/или быстрый) значение остаётся прежним (outside: keep; у нового поля — пусто) или пусто (nan). " +
      "Перед записью выражение проверяется на модели; ошибка названа позицией в тексте (строка, столбец, символ). Без model_id открывает окно «Поля блочной модели».",
    params: {
      model_id: MODEL_ID,
      expr: P("string", "выражение 8.7.4, например IF(AU > 1, AU * 2, 0)"),
      field: P("string", "имя нового поля, до 100 знаков (не «idx»)"),
      dtype: P("string", "тип поля (по умолчанию — по выражению)", { enum: ["f8", "f4", "i4", "i2", "i1", "cat"] }),
      unit: P("string", "единица измерения, до 40 знаков, например г/т"),
      description: P("string", "описание поля, до 500 знаков"),
      filter_id: FILTER_ID,
      restrict: RESTRICT,
      outside: P("string", "вне ограничителя: keep — оставить прежнее значение (новое поле — пусто), nan — пусто", { enum: ["keep", "nan"] }),
      replace: REPLACE,
    },
    confirm: true,
  },
  {
    id: "blocks.calc.check",
    title: "Проверить выражение калькулятора",
    description:
      "Проверяет выражение 8.7.4 на блочной модели, ничего не записывая: ответ называет тип результата (число, условие 0/1 или категория), тип поля, прочитанные поля и каноническую запись выражения; " +
      "ошибка названа позицией в тексте (строка, столбец, символ) и подсказкой. Так выражение отлаживают до расчёта поля.",
    params: {
      model_id: MODEL_ID,
      expr: P("string", "выражение 8.7.4, например IF(AU > 1, AU * 2, 0)"),
      dtype: P("string", "проверить запись в поле этого типа", { enum: ["f8", "f4", "i4", "i2", "i1", "cat"] }),
    },
    required: ["model_id", "expr"],
  },
  {
    id: "blocks.density",
    title: "Плотность блоков",
    description:
      "Плотность блочной модели (8.7.5): два новых поля — плотность (DENS, т/м³, роль «плотность») и её источник (DENS_SRC: 0 — значение блока, 1 — подставлено «по умолчанию»). Способ method: constant — одно число value; " +
      "field — числовое поле field; table — таблица «домен → плотность» по полю table_field (категория — по подписи, число — по точному значению; table — JSON-список [{\"value\": \"Руда\", \"density\": 3.1}], " +
      "table_default — плотность домена, которого в таблице нет); formula — выражение 8.7.4 expr над полями модели. Блок без значения (пропуск в поле или формуле, домен вне таблицы) получает default; " +
      "без default его плотность остаётся пустой (не 0), в DENS_SRC — 1, в ответе предупреждение. Плотность — число больше нуля: 0, отрицательная или бесконечная внутри ограничителя — ошибка. " +
      "Вне ограничителя оба поля пусты. Без model_id и method открывает окно «Поля блочной модели».",
    params: {
      model_id: MODEL_ID,
      method: P("string", "способ задания плотности", { enum: ["constant", "field", "table", "formula"] }),
      value: P("string", "constant: число т/м³, например 2,7"),
      field: P("string", "field: имя числового поля плотности"),
      table_field: P("string", "table: поле домена (категория или число)"),
      table: P("string", 'table: JSON-список строк [{"value": "Руда", "density": 3.1}]'),
      table_default: P("string", "table: плотность домена, которого нет в таблице, т/м³"),
      expr: P("string", "formula: выражение 8.7.4, например 2.5 + CU / 10"),
      default: P("string", "плотность блока без значения, т/м³ (пусто — плотность остаётся пустой)"),
      out: P("string", "имя поля плотности (по умолчанию DENS)"),
      src_field: P("string", "имя поля источника (по умолчанию DENS_SRC)"),
      filter_id: FILTER_ID,
      restrict: RESTRICT,
      replace: REPLACE,
    },
    confirm: true,
  },
  {
    id: "blocks.classify",
    title: "Категории блоков",
    description:
      "Категории блочной модели (8.7.5): новое категорийное поле (CLASS, роль «категория»). Ровно одно из scheme (схема проекта: jorc, gkz, своя — вкладка «Категории» окна) и rules (список правил по порядку в JSON " +
      '[{"label": "Измеренные", "expr": "PASS = 1 AND SLOPE >= 0.8"}]): первое выполненное правило задаёт категорию. estimate — поле оценки: блок, у которого оно пусто, категории не получает. ' +
      'prefix — «Префикс полей оценки»: поля оценки в правилах (PASS, SLOPE, NH, AD, KV, NS и др.) читаются как <prefix>PASS и т. д. (до 40 знаков: латиница, цифры, «_»; пусто — без префикса). ' +
      'overrides — каркасы категорий JSON-списком [{"wireframe_id": 7, "label": "Измеренные"}]: блок с оценкой внутри каркаса получает его категорию. majority — проходов мажоритарного фильтра 3×3×3 по родителям (0–10, до 16 категорий). ' +
      "Схема ГКЗ — шаблон A, B, C1, C2 без порогов: пока пороги не заданы, она не считает (сохраните схему с условиями — blocks.scheme.save). Без model_id и схемы открывает окно «Поля блочной модели».",
    params: {
      model_id: MODEL_ID,
      scheme: P("string", "код схемы проекта: jorc, gkz или своя"),
      rules: P("string", 'правила по порядку в JSON: [{"label": "Руда", "expr": "AU >= 1"}] (вместо scheme)'),
      estimate: P("string", "поле оценки: пустое значение — «нет оценки», категория не присваивается"),
      prefix: P("string", "префикс полей оценки: PASS схемы читается как <префикс>PASS (пусто — без префикса)"),
      overrides: P("string", 'каркасы категорий в JSON: [{"wireframe_id": 7, "label": "Измеренные"}]'),
      majority: P("integer", "проходов мажоритарного фильтра 3×3×3 по родителям: 0–10 (по умолчанию 0)"),
      out: P("string", "имя поля категорий (по умолчанию CLASS)"),
      filter_id: FILTER_ID,
      restrict: RESTRICT,
      replace: REPLACE,
    },
    confirm: true,
  },
  {
    id: "blocks.scheme.save",
    title: "Сохранить схему категорий",
    description:
      "Сохраняет схему категорий в справочнике проекта (8.7.5, № 68): код id (латиница, цифры, «_», «-», до 40), название name и правила rules по порядку в JSON " +
      '[{"label": "A", "expr": "AU >= 1"}, {"label": "B", "expr": null}]; правило без условия (null) — порог не задан, такая схема не считает. ' +
      "Новый код — новая схема; существующий (jorc и gkz тоже) — замена по версии expected_version (по умолчанию — последняя прочитанная). Схемы jorc и gkz можно переписать, но не удалить. Без id и rules открывает окно.",
    params: {
      id: P("string", "код схемы: jorc, gkz или свой"),
      name: P("string", "название схемы"),
      rules: P("string", "правила по порядку в JSON: [{label, expr}]; expr может быть null"),
      expected_version: P("integer", "версия схемы, которую правит вызов (по умолчанию — последняя прочитанная)"),
    },
    confirm: true,
  },
  {
    id: "blocks.field.info",
    title: "Поля блочной модели",
    description:
      "Читает поля блочной модели, ничего не меняя (8.7.2): для каждого — тип, единица, роль, источник (калькулятор, плотность, категории, файл импорта), «устарел» (поля или фильтр, из которых считалось, " +
      "изменились) и где поле используется (другие поля, виды проекта, фильтры). С field — подробно об одном поле; без него — список всех полей модели. Свойства меняет blocks.field.props.",
    params: { model_id: MODEL_ID, field: P("string", "имя поля (без него — все поля модели)") },
    required: ["model_id"],
  },
  {
    id: "blocks.field.props",
    title: "Свойства поля блочной модели",
    description:
      "Меняет свойства поля блочной модели (8.7.2): единицу, описание, роль — только названные, пустая строка очищает. Роль числового поля — grade (содержание), density (плотность), " +
      "frac (доля объёма: одно поле на модель, значения 0…1), категории — code (код каркаса) или class (категория). Поле переименовать нельзя (имя — ключ файлов, фильтров, видов и выражений): " +
      "создайте поле калькулятором под новым именем и удалите старое. Прочитать поля — blocks.field.info. Без model_id, field и свойств открывает окно «Поля блочной модели».",
    params: {
      model_id: MODEL_ID,
      field: P("string", "имя поля модели"),
      unit: P("string", "единица измерения, до 40 знаков; пустая строка очищает"),
      description: P("string", "описание поля, до 500 знаков; пустая строка очищает"),
      role: P("string", "роль поля: grade, density, frac (число), code, class (категория); пустая строка снимает роль"),
    },
    confirm: true,
  },
  {
    id: "blocks.field.rebuild",
    title: "Перестроить поле",
    description:
      "Перестраивает поле блочной модели — считает его заново по тем же параметрам (калькулятор, плотность, категории) и записывает поверх прежнего. Нужно полю с пометкой «устарел»: изменились поля, фильтр " +
      "или каркасы, из которых оно считалось. Поля из файла импорта, заполнения и регуляризации перестроить нельзя (регуляризацию — вместе с моделью). Если поля, фильтра или каркаса, из которых считалось поле, " +
      "больше нет, ответ говорит об этом. Без model_id и field открывает окно «Поля блочной модели».",
    params: { model_id: MODEL_ID, field: P("string", "имя поля модели") },
    confirm: true,
  },
  {
    id: "blocks.field.delete",
    title: "Удалить поле блочной модели",
    description:
      "Удаляет поле из блочной модели. Поле, которое читает другое поле модели (калькулятор, плотность, категории), вид проекта, сохранённый вид или фильтр библиотеки, не удаляется — ответ называет, где оно используется. " +
      "Поле с ролью «доля объёма» (frac) задаёт объём блоков: сначала снимите роль. Без model_id и field открывает окно «Поля блочной модели».",
    params: { model_id: MODEL_ID, field: P("string", "имя поля модели") },
    confirm: true,
  },
];

/** The ribbon: the tab «Блочные модели», the group «Поля» (the buttons follow `blocks.report`'s neighbours: «Регуляризация», «Содержание–тоннаж»). */
export interface FieldButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const FIELD_GROUP_TITLE = "Поля";

export const FIELD_BUTTONS: readonly FieldButton[] = [
  { command: "blocks.calc", label: "Калькулятор", icon: "table" as IconName, hint: "Новое поле модели по выражению: IF, ROUND, COALESCE…; проверка с позицией ошибки, ограничитель, замена с подтверждением" },
  { command: "blocks.density", label: "Плотность", icon: "cube" as IconName, hint: "Плотность блоков: постоянная, поле, таблица по домену, формула; по умолчанию и поле-источник DENS_SRC" },
  { command: "blocks.classify", label: "Категории", icon: "domains" as IconName, hint: "Категории блоков по схеме JORC, ГКЗ или своей: правила по порядку, каркасы категорий, мажоритарный фильтр" },
  { command: "blocks.field.props", label: "Свойства полей", icon: "properties" as IconName, hint: "Единица, описание, роль, «устарел», «Где используется», перестроить и удалить поле" },
];
