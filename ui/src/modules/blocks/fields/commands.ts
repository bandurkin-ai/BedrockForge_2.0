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
];
