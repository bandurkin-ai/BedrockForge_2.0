/**
 * Trends (ТЗ 7.35 Д-15; ADR 0022 §10, §13, решения 20, 22): the commands `trend.*` — the tree section «Тренды» («+»,
 * the menu of a trend), «Настройки проекта → Геостатистика → Новый тренд…», «Сохранить как тренд» of the window
 * «Вариография». `trend.save` and `trend.delete` — `confirm` (the assistant is asked first); a command without its
 * required arguments opens the window «Тренд» (as ADR 0021 п. 9). No default keys. `TREND_BUTTONS` — the buttons of
 * the ribbon (shell/ribbonConfig.ts puts them in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const TREND_ID = P("integer", "номер тренда (дерево «Тренды»)");

export const commands: Command[] = [
  {
    id: "trend.open",
    title: "Тренд",
    description:
      "Окно «Тренд» (Д-15): имя, углы ang1–ang3, отношения осей a2 / a1 и a3 / a1, размер показа, центр, цвет, «Из модели вариограммы…»; " +
      "эллипсоид тренда в 3D с ручками углов и концов осей. trend_id — изменить сохранённый тренд; без него — новый тренд.",
    params: { trend_id: TREND_ID },
  },
  {
    id: "trend.save",
    title: "Сохранить тренд",
    description:
      "Сохранить глобальный тренд в разделе «Тренды»: углы ang1–ang3 (8.6.3) и отношения осей ratios. Без trend_id — новый тренд, с ним — замена заданных полей (прочие остаются). " +
      "model_id — копия осей структуры модели вариограммы (structure с 1; по умолчанию — с наибольшей a1; углы и ratios тогда берутся из модели); с trend_id — «Обновить из модели». " +
      "Без углов и без model_id — окно «Тренд». Матрица анизотропии A = diag(1, a1/a2, a1/a3)·R — у ядра.",
    params: {
      trend_id: TREND_ID,
      kind: P("string", "вид тренда: global (structural — с Э19)", { enum: ["global", "structural"] }),
      name: P("string", "имя тренда (по умолчанию «Тренд» или «Тренд · ‹модель›»)"),
      ang1: P("number", "азимут оси a1, ° (0–360)"),
      ang2: P("number", "ang2 = −погружение оси a1, ° (−90…90; 0)"),
      ang3: P("number", "поворот вокруг a1, ° (−180…180; 0)"),
      ratios: P("string", "отношения a2 / a1 и a3 / a1 через «;», например «0,5; 0,2» (числа > 0; по умолчанию 1; 1)"),
      size: P("number", "длина большой оси для показа, м (> 0; 100; из модели — её a1)"),
      centre: P("string", "центр показа «X; Y; Z», м; пусто — центр вида"),
      color: P("string", "цвет #rrggbb (по умолчанию #f46a1a)"),
      model_id: P("integer", "номер сохранённой модели вариограммы — копия её осей"),
      structure: P("integer", "номер структуры модели с 1 (вместе с model_id)"),
      origin: P("string", "происхождение без модели: manual — вручную, ellipsoid — эллипсоид вариографии", { enum: ["manual", "ellipsoid"] }),
    },
    confirm: true,
  },
  {
    id: "trend.delete",
    title: "Удалить тренд",
    description: "Удалить тренд из раздела «Тренды». Тренд, выбранный в оценке или RBF, не удаляется — ответ называет, где он используется.",
    params: { trend_id: TREND_ID },
    required: ["trend_id"],
    confirm: true,
  },
  {
    id: "trend.show",
    title: "Показать тренд",
    description: "Показать или скрыть эллипсоид тренда в 3D (запоминается в виде проекта; центр не задан — центр вида).",
    params: { trend_id: TREND_ID, visible: P("boolean", "показать (по умолчанию да)") },
    required: ["trend_id"],
  },
  {
    id: "trend.usage",
    title: "Где используется тренд",
    description: "Объекты, которые ссылаются на тренд (мастера оценки блочных моделей и RBF): имя, вид, роль. Пока такие ссылки есть, тренд не удаляется.",
    params: { trend_id: TREND_ID },
    required: ["trend_id"],
  },
];

/** «Настройки проекта → Геостатистика»: the button «Новый тренд…» (ADR 0022 §13). Icon `trend` — shell/Icon.tsx. */
export interface TrendButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const TREND_BUTTONS: readonly TrendButton[] = [{ command: "trend.open", label: "Новый тренд…", icon: "trend" as IconName, hint: "Глобальный тренд: углы и отношения осей, из модели вариограммы или вручную", small: true }];
