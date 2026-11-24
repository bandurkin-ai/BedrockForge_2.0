/**
 * The report «Содержание–тоннаж» (ТЗ 7.31; ADR 0023 §10, §13): the command `blocks.report`. The ribbon (the tab «Блочные
 * модели», the group «Поля»), Ctrl+K, the keys of the user and the assistant run the same command. Without `model_id`
 * it opens the window (as ADR 0021 п. 9); with it — builds the report as the job `blocks.report` and answers with the
 * table. It writes nothing to the project, so it works in a project open for reading. `REPORT_BUTTONS` — the button of
 * the ribbon (`shell/ribbonConfig.ts` puts it in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

export const commands: Command[] = [
  {
    id: "blocks.report",
    title: "Содержание–тоннаж",
    description:
      "Отчёт «Содержание–тоннаж» по блочной модели (7.31): тоннаж = объём × доля блока × плотность, содержания взвешены по тоннажу блоков, где они есть, металл (г/т и ppm → тыс. унц, % → т, ppb → кг), " +
      "таблица по бортам (борт «≥»; борт 0 — весь тоннаж блоков со значением поля борта), кривая «содержание–тоннаж», группы и отбор по категориям. Блок без значения содержания не идёт в среднее и металл " +
      "(в таблице «нет значения», не 0). Без model_id открывает окно отчёта. По умолчанию содержания — поля *_CUT (иначе с ролью «содержание»), плотность — меняющееся поле SG (постоянное DENSITY пропускается), борт 0. " +
      "Ответ — таблица по бортам и число блоков, не вошедших в расчёт (по ограничителю, без плотности, без значения борта).",
    params: {
      model_id: P("integer", "номер блочной модели (раздел «Блочные модели» дерева)"),
      grades: P("string", "поля содержаний через запятую, до 20 (по умолчанию — поля *_CUT)"),
      density: P("string", "плотность: имя числового поля или число в т/м³, например 2,7 (по умолчанию — поле SG)"),
      cutoffs: P("string", "борта через «;», например 0; 0,5; 1 (по умолчанию 0 — весь тоннаж), до 200"),
      cutoff_field: P("string", "поле борта (по умолчанию — первое из содержаний)"),
      filter_id: P("integer", "номер фильтра библиотеки (раздел «Фильтры» дерева): считаются блоки, которые он пропускает; доли каркаса и поверхности входят в тоннаж"),
      restrict: P("string", 'быстрый ограничитель — дерево правил в JSON, например {"cond": {"field": "DOMAIN", "op": "=", "value": 2}}; вместе с фильтром — оба сразу (И)'),
      categories_field: P("string", "категорийное поле для отбора (например CLASS)"),
      categories: P("string", "подписи категорий для отбора через «;» или JSON-списком; вместе с categories_field"),
      groups: P("string", "категорийное поле, по которому таблица делится на группы (до 1000 подписей)"),
      curve: P("integer", "точек кривой: 0 — без кривой, 2…500; по умолчанию 50"),
      csv: P("boolean", "приложить таблицу как CSV («;», десятичная запятая); в окне она всегда готова"),
    },
  },
];

/** The ribbon: the tab «Блочные модели», the group «Поля» (the button follows «Калькулятор», «Плотность», «Категории», «Регуляризация»). */
export interface ReportButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const REPORT_GROUP_TITLE = "Поля";

export const REPORT_BUTTONS: readonly ReportButton[] = [
  { command: "blocks.report", label: "Содержание–тоннаж", icon: "report" as IconName, hint: "Отчёт по бортам: тоннаж, содержания, металл, группы, кривая «содержание–тоннаж», CSV" },
];
