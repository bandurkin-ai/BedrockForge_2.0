/**
 * The library of filters (ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8, §13): the commands `filters.manage`, `filter.save`,
 * `filter.delete`, `filter.test` (the table of §13) and `filter.usage` («Где используется»; the same read as the
 * menu of the tree and the window need — a command of the registry as everything the window does). The tree section
 * «Фильтры», «Настройки проекта → Блочные модели → Фильтры» and the keys of the user run the same commands.
 * `filter.save` and `filter.delete` — `confirm` (the assistant is asked first); a command without its required
 * arguments opens the window «Фильтры» (as ADR 0021 п. 9). No default keys. `FILTER_BUTTONS` — the buttons of the
 * ribbon (shell/ribbonConfig.ts puts them in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const FILTER_ID = P("integer", "номер фильтра (раздел «Фильтры» дерева)");
const MODEL_ID = P("integer", "номер блочной модели (раздел «Блочные модели» дерева)");

export const commands: Command[] = [
  {
    id: "filters.manage",
    title: "Фильтры",
    description:
      "Окно «Фильтры» (Д-02): библиотека фильтров проекта — список, создать (конструктор «поле — оператор — значение» и текст выражения 8.7.4), изменить, копировать, " +
      "удалить, «Где используется», «Проверить на модели». filter_id — открыть сохранённый фильтр на правку; без него — список без выбранного фильтра.",
    params: { filter_id: FILTER_ID },
    uiOnly: true,
  },
  {
    id: "filter.save",
    title: "Сохранить фильтр",
    description:
      "Сохранить фильтр в библиотеке проекта (раздел «Фильтры»). Фильтр — дерево правил (И, ИЛИ, НЕ; условия «поле — оператор — значение»; выражение 8.7.4; внутри или снаружи каркаса, " +
      "выше или ниже поверхности) или текст выражения 8.7.4, например «AU > 1 AND DOMAIN = 2». Задайте ровно одно: expr — текст, rules — дерево правил в JSON " +
      '(узлы {"op": "and"|"or"|"not", "items": [...]}, {"expr": "..."}, {"cond": {"field": "AU", "op": ">", "value": 1}}, {"wireframe": {"id": 7, "side": "inside"}}, {"surface": {"id": 8, "side": "above"}}). ' +
      "Без filter_id — новый фильтр (нужно имя), с ним — замена заданных полей (прочие остаются) по версии expected_version (по умолчанию — последняя прочитанная). " +
      "Без имени и правил — окно «Фильтры».",
    params: {
      filter_id: FILTER_ID,
      expected_version: P("integer", "версия фильтра, которую правит вызов (по умолчанию — последняя прочитанная)"),
      name: P("string", "имя фильтра (уникально без учёта регистра)"),
      expr: P("string", "текст выражения 8.7.4, например AU > 1 AND DOMAIN = 2"),
      rules: P("string", "дерево правил в JSON (вместо expr)"),
      note: P("string", "примечание к фильтру"),
      folder: P("string", "папка фильтра, например «Отчёты/Борт»"),
    },
    confirm: true,
  },
  {
    id: "filter.delete",
    title: "Удалить фильтр",
    description: "Удалить фильтр из библиотеки проекта. Фильтр, который выбран в виде блочной модели или в объектах проекта, не удаляется — ответ называет, где он используется.",
    params: { filter_id: FILTER_ID },
    required: ["filter_id"],
    confirm: true,
  },
  {
    id: "filter.test",
    title: "Проверить фильтр на модели",
    description: "Сколько блоков модели проходит фильтр: прошло, всего, вес по долям (каркас и поверхность дают доли). У модели должны быть поля, которые читает фильтр, иначе ответ называет недостающее поле.",
    params: { filter_id: FILTER_ID, model_id: MODEL_ID },
    required: ["filter_id", "model_id"],
  },
  {
    id: "filter.usage",
    title: "Где используется фильтр",
    description: "Объекты и виды блочных моделей, которые ссылаются на фильтр: имя, вид, роль. Пока такие ссылки есть, фильтр не удаляется.",
    params: { filter_id: FILTER_ID },
    required: ["filter_id"],
  },
];

/** «Настройки проекта → Блочные модели»: the button «Фильтры» (Д-02). Icon `filter` — shell/Icon.tsx. */
export interface FilterButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const FILTER_GROUP_TITLE = "Блочные модели";

export const FILTER_BUTTONS: readonly FilterButton[] = [
  { command: "filters.manage", label: "Фильтры", icon: "filter" as IconName, hint: "Библиотека фильтров проекта: создать, изменить, копировать, удалить, «Где используется», «Проверить на модели»" },
];
