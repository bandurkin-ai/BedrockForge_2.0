/**
 * The commands of the implicit modelling (ТЗ 8.9.4; ADR 0026 «Команды реестра»; docs/progress/e19-api.md §12): «Моделирование → Неявные
 * модели». The ribbon, Ctrl+K and the assistant run the same commands; no keys. A command without arguments opens its window (ADR 0021
 * п. 9), with them it executes. The pure declarations are here (the catalog reads them first); the handlers — `bind.ts` as the windows get
 * ready: `implicit.new`, `implicit.domains`, `implicit.save`, `implicit.update` (19.22); the groups, «В каркас», «Удалить» (19.24); the rest says «появится на этапе» until its
 * window (`stage`). The parameters are flat (`ParamSchema` has no objects): the box is text «x; y; z; x; y; z», the lists are text
 * («DY; SS»). The commands of Э19б (the stack, the faults, the system of veins, the planned holes) come with 19.38.
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const MODEL = P("integer", "номер неявной модели (раздел «Неявные модели» дерева)");
const SET = P("integer", "номер набора скважин");
const TABLE = P("string", "ключ или название интервальной таблицы набора");

export const commands: Command[] = [
  {
    id: "implicit.new",
    title: "Новая модель",
    description:
      "Мастер «Новая модель» неявного моделирования (8.9, RBF): граница (охват, замкнутый каркас, при желании под ЦТМ), шаг h и число ячеек → домены литологии (набор скважин, таблица и поле кодов, группы по возрасту) → интерполяция (ядро, сглаживание «Вручную» или «Авто», тренд или анизотропия) → итог. " +
      "Без параметров открывает мастер. С box или wireframe_id создаёт пустую модель с этой границей (группы добавляет implicit.domains) и ничего не строит. " +
      "box — охват «x_мин; y_мин; z_мин; x_макс; y_макс; z_макс», м; wireframe_id — замкнутый каркас границы; h — шаг, м (пусто — диагональ охвата / 200); name — название.",
    params: {
      name: P("string", "название модели"),
      box: P("string", "охват: x_мин; y_мин; z_мин; x_макс; y_макс; z_макс, м"),
      wireframe_id: P("integer", "номер замкнутого каркаса границы"),
      h: P("number", "шаг сетки изоповерхности h, м (0,1–100)"),
    },
  },
  {
    id: "implicit.save",
    title: "Сохранить неявную модель",
    description:
      "Сохраняет форму, открытую в мастере «Новая модель», моделью проекта: правку открытой модели (версия проверяется) или новую модель. " +
      "as_new=true — всегда новая модель рядом с открытой; build=true — после сохранения построить (или обновить) модель. Ошибка в поле формы остаётся под этим полем.",
    params: {
      as_new: P("boolean", "сохранить как новую модель, не заменяя открытую"),
      build: P("boolean", "после сохранения построить или обновить модель"),
    },
  },
  {
    id: "implicit.domains",
    title: "Домены литологии",
    description:
      "Домены литологии неявной модели: группы кодов таблицы скважин по возрасту (первая — самая молодая), «Прочее» — всё остальное в границе. " +
      "Без codes открывает мастер модели на шаге доменов (model_id — модель; без неё выбранная в дереве или единственная; в проекте без моделей — мастер новой модели). " +
      "С model_id и codes добавляет домен последним, самым старым: codes — коды через «;» (например DY; SS), name — название группы (по умолчанию «Домен N»).",
    params: {
      model_id: MODEL,
      codes: P("string", "коды группы через «;», например DY; SS"),
      name: P("string", "название группы"),
    },
  },
  {
    id: "implicit.shells",
    title: "Оболочки содержаний",
    description:
      "Оболочки содержаний неявной модели (8.9.2): значения поля композитов (результат обработки проб) или таблицы проб, преобразование (linear — как есть, log — логарифм, nscore — нормальные оценки), пороги по возрастанию; " +
      "оболочка порога k вложена в оболочку порога k − 1. Вид: numeric — изоповерхность f = t, indicator — значения [g ≥ t] − 0,5 и вероятность p (0,3–0,7). Группа добавляется последней (самой старой), с group_id — меняется. " +
      "Без thresholds открывает окно (model_id — модель; без неё выбранная в дереве или единственная); с thresholds записывает группу. Тела строятся командой «Обновить».",
    params: {
      model_id: MODEL,
      group_id: P("integer", "номер группы-оболочки для правки; без него добавляется новая"),
      name: P("string", "название группы (по умолчанию «Оболочка N»)"),
      source: P("string", "источник значений: composites — результат обработки проб (result_id), table — таблица проб набора (set_id, table)", { enum: ["composites", "table"] }),
      result_id: P("integer", "номер результата обработки проб с композитами (source = composites)"),
      set_id: P("integer", "номер набора скважин таблицы проб (source = table; по умолчанию набор данных модели)"),
      table: P("string", "ключ или название таблицы проб (source = table)"),
      field: P("string", "числовое поле содержания, например AU"),
      transform: P("string", "преобразование значений: linear, log или nscore (по умолчанию linear)", { enum: ["linear", "log", "nscore"] }),
      mode: P("string", "вид оболочки: numeric или indicator (по умолчанию numeric)", { enum: ["numeric", "indicator"] }),
      thresholds: P("string", "пороги через «;» по возрастанию, от 1 до 20, например 0,5; 1; 2,5"),
      p: P("number", "вероятность p индикаторной оболочки, 0,3–0,7 (по умолчанию 0,5)"),
    },
  },
  {
    id: "implicit.vein",
    title: "Жила",
    description:
      "Жила неявной модели (8.9.2): висячий и лежачий бока — кровля и подошва интервалов кодов жилы, опорная плоскость по контактам, выклинивание там, где мощность не больше t_min; граница — выпуклая оболочка контактов с отступом (стоящий в модели полигон в плоскости жилы сохраняется). " +
      "Шаг сетки (u, v) — шаг модели. Группа добавляется последней, с group_id — меняется. Без codes открывает окно (model_id — модель; без неё выбранная в дереве или единственная); с codes записывает группу.",
    params: {
      model_id: MODEL,
      group_id: P("integer", "номер группы-жилы для правки; без него добавляется новая"),
      name: P("string", "название группы (по умолчанию «Жила N»)"),
      codes: P("string", "коды жилы через «;» (из таблицы кодов модели)"),
      bound: P("string", "граница: hull — выпуклая оболочка с отступом (по умолчанию), polygon — полигон, уже хранящийся в группе", { enum: ["hull", "polygon"] }),
      offset: P("number", "отступ границы от контактов, м (0–10000; по умолчанию 0)"),
      t_min: P("number", "мощность выклинивания t_min, м (0–100; по умолчанию 0)"),
    },
  },
  {
    id: "implicit.expand",
    title: "Расширение каркаса",
    description:
      "Расширение каркаса (8.9.2): поверхность на расстоянии delta от замкнутого каркаса (delta < 0 — сжатие) как группа модели. Группа добавляется последней, с group_id — меняется. " +
      "Без wireframe_id и delta открывает окно (model_id — модель; без неё выбранная в дереве или единственная); с ними записывает группу. wireframe_id — замкнутый каркас; delta — смещение, м (−1000…1000; не 0).",
    params: {
      model_id: MODEL,
      group_id: P("integer", "номер группы-расширения для правки; без него добавляется новая"),
      name: P("string", "название группы (по умолчанию «Расширение N»)"),
      wireframe_id: P("integer", "номер замкнутого каркаса"),
      delta: P("number", "смещение Δ, м (−1000…1000; не 0)"),
    },
  },
  {
    id: "implicit.structural",
    title: "Структурные данные",
    description:
      "Структурный тренд для неявной модели (Д-15): замеры залегания таблицы скважин (или опорный каркас) → тренд с локальными осями анизотропии, который выбирается в мастере. model_id — модель, которой задать тренд; set_id и table — набор скважин и таблица замеров.",
    params: { model_id: MODEL, set_id: SET, table: TABLE },
    stage: "Э19 (19.26)",
  },
  {
    id: "implicit.update",
    title: "Обновить модель",
    description:
      "Строит неявную модель (если ещё не строилась) или обновляет её (задание implicit.update: пересчитываются только участки с изменённым набором точек и покрытые ими кирпичи; результат побитно равен полному построению). " +
      "Каркасы-выходы переписываются на месте. Если граф зависимостей считает модель устаревшей (изменена таблица, траектории, слой, тренд, каркас), ответ называет причину. model_id — модель; без неё выбранная в дереве или единственная. Ход — вкладка «Задания»; ответ — число точек, участков, время.",
    params: { model_id: MODEL },
  },
  {
    id: "implicit.to_wireframe",
    title: "В каркас",
    description:
      "Копирует выход модели (поверхность или тело) в обычный каркас проекта без связи с моделью: дальше его можно править и считать, «Обновить» его не меняет. " +
      "С wireframe_id копирует этот каркас-выход; без него открывает окно со списком выходов модели. model_id — модель (без неё выбранная в дереве или единственная); name — название копии.",
    params: { model_id: MODEL, wireframe_id: P("integer", "номер каркаса-выхода модели"), name: P("string", "название копии") },
  },
  {
    id: "implicit.delete",
    title: "Удалить неявную модель",
    description: "Удаляет неявную модель вместе с её каркасами-выходами. Перед удалением показываются объекты, которые от неё зависят. Копии, сделанные командой «В каркас», остаются.",
    params: { model_id: MODEL },
    required: ["model_id"],
    confirm: true,
  },
  {
    id: "implicit.check",
    title: "Проверка по данным",
    description: "Таблица проверки модели по точкам данных (Д-32): значение модели в точке, значение данных, невязка, расстояние до каркаса группы; сортировка по невязке; выгрузка в CSV. model_id — построенная модель.",
    params: { model_id: MODEL },
    stage: "Э19 (19.25)",
  },
  {
    id: "implicit.support",
    title: "Опора данных",
    description: "Карта опоры данных поверхности (Д-32): расстояние вершины до ближайшей точки данных; доля площади за порогом D_оп (по умолчанию 2·D_max). model_id — построенная модель; threshold — порог, м.",
    params: { model_id: MODEL, threshold: P("number", "порог D_оп, м") },
    stage: "Э19 (19.25)",
  },
  {
    id: "implicit.compare_holes",
    title: "Сверка со скважинами",
    description: "Сверка модели с кодами скважин (Д-33): матрица «код данных × код модели» по длине и числу интервалов, список несовпавших интервалов с переходом в таблицу. model_id — построенная модель; set_id, table, field — набор, таблица и поле кода.",
    params: { model_id: MODEL, set_id: SET, table: TABLE, field: P("string", "поле кода") },
    stage: "Э19 (19.25)",
  },
  {
    id: "drillholes.from_model",
    title: "Модель на скважины",
    description:
      "Записывает коды модели в скважины (Д-34): тела неявной модели или каркасов → интервальная таблица FROM, TO, CODE набора (промежутки вне тел — пустой код, не 0). " +
      "set_id — набор; model_id — неявная модель, либо mesh_ids — номера каркасов через «;»; name — название новой таблицы.",
    params: { set_id: SET, model_id: MODEL, mesh_ids: P("string", "номера каркасов через «;»"), name: P("string", "название таблицы") },
    required: ["set_id", "name"],
    stage: "Э19 (19.25)",
  },
];

/** The ribbon: the group «Неявные модели» (`shell/ribbonConfig.ts` puts it in, by the orchestrator). */
export interface ImplicitButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

const icon = (name: string): IconName => name as IconName;

export const IMPLICIT_GROUP_TITLE = "Неявные модели";

export const IMPLICIT_BUTTONS: readonly ImplicitButton[] = [
  { command: "implicit.new", label: "Новая модель", icon: icon("implicit"), hint: "Мастер: граница и шаг, домены литологии, интерполяция" },
  { command: "implicit.domains", label: "Домены литологии", icon: icon("domains"), hint: "Группы кодов таблицы скважин по возрасту; «Прочее» — остальное в границе", small: true },
  { command: "implicit.shells", label: "Оболочки содержаний", icon: icon("ellipsoid"), hint: "Вложенные оболочки по порогам содержания", small: true },
  { command: "implicit.vein", label: "Жила", icon: icon("vein"), hint: "Тело жилы по кровле и подошве интервалов", small: true },
  { command: "implicit.structural", label: "Структурные данные", icon: icon("trend"), hint: "Замеры залегания → структурный тренд модели", small: true },
  { command: "implicit.expand", label: "Расширение каркаса", icon: icon("wireframe"), hint: "Поверхность на расстоянии Δ от замкнутого каркаса", small: true },
  { command: "implicit.update", label: "Обновить", icon: icon("refresh"), hint: "Построить модель или пересчитать изменённые участки" },
  { command: "implicit.to_wireframe", label: "В каркас", icon: icon("export"), hint: "Копия выхода модели в обычный каркас", small: true },
];
