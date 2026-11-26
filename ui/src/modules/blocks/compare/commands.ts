/**
 * The command «Сравнить оценки» (ТЗ 7.35 Д-37; ADR 0027 решения 25, 29): «Геостатистика → Проверка → Сравнить оценки». The ribbon, Ctrl+K and the assistant run the same
 * command (no keys). Without the fields it opens the window «Сравнить оценки»; with both fields it runs the job `estimate.compare` (Δ = A − B is written into the model
 * as a field) and answers with the statistics and the domains. The pure declarations are here (the catalog reads them first); the handler — `bind.ts`.
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

export const commands: Command[] = [
  {
    id: "estimate.compare",
    title: "Сравнить оценки",
    description:
      "Сравнение двух оценок блочной модели (Д-37): поле Δ = A − B записывается в модель (по умолчанию <A>_<B>_D; где в A или в B пропуск — в Δ тоже пропуск, не 0), статистика Δ (число блоков, среднее, СКО, минимум, максимум, перцентили), " +
      "гистограмма Δ по числу блоков, таблица по доменам (средние A, B и Δ взвешены объёмом блоков, число блоков со сменой STATUS оценки), предупреждения. Δ можно раскрасить в 3D (blocks.style с field = имя поля Δ). " +
      "Без field_a и field_b открывает окно «Сравнить оценки»; с ними выполняет сравнение (задание estimate.compare) и отвечает сводкой. Меняет модель: в проекте только для чтения не работает.",
    params: {
      model_id: P("integer", "номер блочной модели (раздел «Блочные модели» дерева; без него — выбранная в дереве или единственная)"),
      field_a: P("string", "поле A: числовое поле модели, например AU_OK_EST (вместе с field_b)"),
      field_b: P("string", "поле B: второе числовое поле, например AU_ID_EST"),
      domain: P("string", "категорийное поле доменов для таблицы (по умолчанию — поле domain, иначе поле с ролью кода или класса, иначе одна строка «Все»)"),
      out: P("string", "имя нового поля Δ в модели (по умолчанию <A>_<B>_D)"),
    },
  },
];

export interface CompareButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

/** The ribbon: the group «Проверка» of the tab «Геостатистика» (`shell/ribbonConfig.ts` puts the button in, by the orchestrator). */
export const COMPARE_BUTTONS: readonly CompareButton[] = [
  { command: "estimate.compare", label: "Сравнить оценки", icon: "scatter", hint: "Две оценки модели: поле разности Δ, её статистика и гистограмма, средние по доменам, смена статуса, раскраска Δ в 3D", small: true },
];
