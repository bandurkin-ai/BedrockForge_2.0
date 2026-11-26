/**
 * The commands of the pit optimisation (ТЗ 8.11.5; ADR 0028 решение 29; docs/progress/e21-api.md §16): the ribbon («Карьер»), Ctrl+K and the assistant
 * run the same commands; no keys. A command without arguments opens its window (ADR 0021 п. 9), with them it executes. The pure declarations are here
 * (the catalog reads them first); the handlers — `bind.ts`. The parameters are flat (`ParamSchema` has no objects): the elements are text
 * «AU:г/т:2000:5:0,03; CU:%:9000:300» (the field, the unit, the price P, the sales S, the royalty), RF is a list «0,8; 1; 1,2» or a range
 * (`rf_from`, `rf_to`, `rf_step`), the restrictor is a tree of rules in JSON. The commands of the windows of 21.17–21.19 (`stage`) are unavailable
 * until their lanes bind them (`registry.bind` clears the stage); `pit.run` is unavailable until `validate` is `ready` (the hint is the first `missing`).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const SCENARIO = P("integer", "номер сценария карьера (раздел «Карьер» → «Сценарии» дерева; без него — выбранный в дереве или тот, с которым работают окна)");

export const commands: Command[] = [
  {
    id: "pit.new",
    title: "Новый сценарий карьера",
    description:
      "Создаёт сценарий оптимизации карьера (8.11): имя, блочная модель, рельеф (ЦТМ проекта), ограничитель (фильтр библиотеки и дерево правил, как в «Подсчёте ресурсов»), решатель. " +
      "Остальные параметры — по умолчанию: оболочки RF 0,30…1,50 шаг 0,05 (25), угол борта 45°, допуск угла 2,5°, r = 8 %, H_гл 10 м, c_гл 0. Цены, затраты на добычу и переработку, извлечения — в командах «Экономика» и «Породы»: пока они не заданы, «Запустить» недоступна. " +
      "Без параметров открывает окно «Сценарий карьера». Ответ — номер сценария. Результата у нового сценария нет; блоки модели не меняются.",
    params: {
      name: P("string", "имя сценария (по умолчанию «Сценарий карьера»; занятое получает « (2)»)"),
      model_id: P("integer", "номер блочной модели (раздел «Блочные модели» дерева); модель с переменным шагом сетки не годится: регуляризуйте её"),
      terrain_id: P("integer", "номер ЦТМ рельефа (раздел «Каркасы»): блоки выше поверхности — «воздух»; без рельефа блоки не обрезаются", { nullable: true }),
      filter_id: P("integer", "номер фильтра библиотеки: считаются блоки, которые он пропускает", { nullable: true }),
      restrict: P("string", 'быстрый ограничитель — дерево правил в JSON, например {"cond": {"field": "DOM", "op": "=", "value": "A"}}; вместе с фильтром — оба сразу (И)', { nullable: true }),
      solver: P("string", "решатель: auto (HPF), hpf или ortools; результат не зависит от решателя", { enum: ["auto", "hpf", "ortools"] }),
    },
  },
  {
    id: "pit.economics",
    title: "Экономика карьера",
    description:
      "Экономика сценария (8.11.2): элементы (поле содержания, единица, цена P, сбыт S, роялти — доля от выручки), плотность (поле или число; плотность по умолчанию — для блоков без неё), затраты на добычу c_m0 ($/т), " +
      "отметка Z_ref и рост затрат c_гл на каждые H_гл метров глубины, коэффициенты цены RF (список или диапазон: до 200 оболочек, 0,01…5), префикс имён полей PIT и ORE. " +
      "Цена — за единицу металла: $/унц для г/т, ppm, ppb; $/т для %. Содержание блока без значения не даёт выручки и не считается нулём. Параметры, которых нет в вызове, остаются как есть. Без экономики в вызове открывает окно «Экономика карьера». " +
      "Если у сценария есть результат, он помечается «параметры изменены» — нужен запуск.",
    params: {
      scenario_id: SCENARIO,
      elements: P("string", "элементы через «;», до 20: поле:единица:цена:сбыт:роялти, например AU:г/т:2000:5:0,03; CU:%:9000:300 (единицы: г/т, ppm, ppb, %; пустое место — по умолчанию: единица поля, сбыт 0, роялти 0)"),
      density: P("string", "плотность: имя числового поля или число в т/м³, например 2,7 (пусто — поле SG или с ролью «плотность»)", { nullable: true }),
      default_density: P("number", "плотность блоков, у которых поле пусто, т/м³", { nullable: true }),
      c_m0: P("number", "затраты на добычу c_m0, $/т", { nullable: true }),
      z_ref: P("number", "отметка Z_ref, м, от которой растут затраты на добычу (пусто — верх сетки модели)", { nullable: true }),
      h_gl: P("number", "глубина H_гл, м (больше 0; по умолчанию 10)"),
      c_gl: P("number", "прирост затрат на добычу c_гл за каждые H_гл метров ниже Z_ref, $/т (по умолчанию 0)"),
      rf: P("string", "коэффициенты цены RF через «;», например 0,8; 1; 1,2 (вместо rf_from, rf_to, rf_step)"),
      rf_from: P("number", "диапазон RF: от (вместе с rf_to и rf_step вместо rf)"),
      rf_to: P("number", "диапазон RF: до"),
      rf_step: P("number", "диапазон RF: шаг, больше 0 (оболочек не больше 200)"),
      prefix: P("string", "префикс имён полей: <префикс>PIT и <префикс>ORE (до 40 букв, цифр и «_»)"),
    },
  },
  {
    id: "pit.rocks",
    title: "Породы карьера",
    description: "Типы пород сценария (8.11.2): тип блока — категорийное поле или правила; у каждого типа затраты на переработку c_p, доплата за добычу руды c_mр, извлечение по каждому элементу, отметки «только пустая» и «воздух».",
    params: { scenario_id: SCENARIO },
    stage: "Э21 (21.17)",
  },
  {
    id: "pit.slopes",
    title: "Углы бортов карьера",
    description: "Углы бортов (8.11.2): таблица секторов по азимуту (до 16, 10…80°), домены с собственными таблицами, число уровней шаблона K и допуск ошибки угла.",
    params: { scenario_id: SCENARIO },
    stage: "Э21 (21.17)",
  },
  {
    id: "pit.relief",
    title: "Рельеф и ограничитель карьера",
    description:
      "Рельеф и ограничитель сценария (8.11.2): ЦТМ рельефа (блоки выше поверхности — «воздух»: вне карьера и не пустая порода), фильтр библиотеки и дерево правил. Без параметров открывает окно «Сценарий карьера» для выбранного сценария. " +
      "Параметры, которых нет в вызове, остаются как есть; null снимает значение.",
    params: {
      scenario_id: SCENARIO,
      terrain_id: P("integer", "номер ЦТМ рельефа; null — без рельефа", { nullable: true }),
      filter_id: P("integer", "номер фильтра библиотеки; null — без фильтра", { nullable: true }),
      restrict: P("string", "дерево правил ограничителя в JSON; null — без правил", { nullable: true }),
    },
  },
  {
    id: "pit.calendar",
    title: "Календарь карьера",
    description: "Календарь для NPV (8.11.4): производительность по руде Q_р, по горной массе Q_гм (т/год) и ставка r (по умолчанию 8 % в год). Календарь только для показа: сменить его можно без пересчёта оболочек.",
    params: { scenario_id: SCENARIO },
    stage: "Э21 (21.17)",
  },
  {
    id: "pit.check",
    title: "Проверить шаблон карьера",
    description:
      "Проверка шаблона дуг по углам бортов (8.11.2): число смещений, число уровней K, ошибка угла по азимутам (max и среднее) и предупреждение, если она больше допуска (тогда увеличьте K). Нужны блочная модель и её сетка; цены и породы не нужны. " +
      "Ответ — K, смещений и ошибка угла по каждому домену. Ничего не записывает.",
    params: { scenario_id: SCENARIO },
  },
  {
    id: "pit.run",
    title: "Запустить оптимизацию карьера",
    description:
      "Строит вложенные оболочки карьера по RF сценария (8.11.3: максимальное замыкание графа блоков, HPF или OR-Tools), считает pit-by-pit при RF = 1 и записывает результат: поля PIT (номер наименьшей оболочки) и ORE (1 — руда, 0 — пустая) в блочную модель и отчёт в сценарий; " +
      "счёт в фоне (стадии, отмена — ничего не пишет). Недоступна, пока не заданы цены элементов, затраты, плотность, типы пород и их извлечения: подсказка называет первое недостающее. Повторный запуск с теми же параметрами даёт тот же результат побитно.",
    params: { scenario_id: SCENARIO, solver: P("string", "решатель на этот запуск (по умолчанию из сценария)", { enum: ["auto", "hpf", "ortools"] }) },
  },
  {
    id: "pit.report",
    title: "Pit-by-pit карьера",
    description: "Таблица и график pit-by-pit: по каждой оболочке руда, пустая порода, вскрыша, содержания, металл, выручка, затраты, CF и NPV лучшего и худшего случаев по календарю.",
    params: { scenario_id: SCENARIO },
    stage: "Э21 (21.18)",
  },
  {
    id: "pit.surfaces",
    title: "Поверхности оболочек карьера",
    description: "Строит ЦТМ выбранных оболочек (низ карьера по столбцам блоков) как каркасы проекта в папке сценария; shells — номера оболочек через «;» (по умолчанию все).",
    params: { scenario_id: SCENARIO, shells: P("string", "номера оболочек 1…N через «;» (по умолчанию все)") },
    stage: "Э21 (21.19)",
  },
  {
    id: "pit.solid",
    title: "Каркас карьера",
    description: "Строит замкнутый каркас карьера между рельефом и поверхностью оболочки shell; объём каркаса сверяется с суммой объёмов вынутых блоков.",
    params: { scenario_id: SCENARIO, shell: P("integer", "номер оболочки 1…N"), name: P("string", "имя каркаса") },
    required: ["shell"],
    stage: "Э21 (21.19)",
  },
  {
    id: "pit.compare",
    title: "Сравнить сценарии карьера",
    description: "Сравнивает два сценария по оболочкам (совпадают по RF), NPV и параметрам; оболочка только в одном сценарии — пропуск, не 0; при одной сетке — блоки только в A и только в B.",
    params: { scenario_a: P("integer", "номер первого сценария (A)"), scenario_b: P("integer", "номер второго сценария (B)") },
    stage: "Э21 (21.19)",
  },
  {
    id: "pit.show",
    title: "Показать оболочку карьера в 3D",
    description: "Показывает в 3D блоки модели с PIT не больше номера оболочки shell (показ блоков Э17 с фильтром).",
    params: { scenario_id: SCENARIO, shell: P("integer", "номер оболочки 1…N") },
    required: ["shell"],
    stage: "Э21 (21.18)",
  },
  {
    id: "pit.rename",
    title: "Переименовать сценарий карьера",
    description: "Меняет имя сценария карьера (оно уникально в проекте). Расчёт не повторяется, результат и граф не меняются. scenario_id — сценарий, name — новое имя.",
    params: { scenario_id: SCENARIO, name: P("string", "новое имя") },
    required: ["name"],
  },
  {
    id: "pit.delete",
    title: "Удалить сценарий карьера",
    description:
      "Удаляет сценарий карьера из проекта вместе с отчётом pit-by-pit (поля PIT и ORE в модели и ЦТМ оболочек остаются; связи в графе помечаются «источник утрачен»). Перед удалением спрашивает, что построено из сценария. scenario_id — сценарий.",
    params: { scenario_id: SCENARIO },
    confirm: true,
  },
];

/** The ribbon: the tab «Карьер» (`shell/ribbonConfig.ts` puts it in, by the orchestrator): the groups «Сценарий», «Расчёт», «Результаты». */
export interface PitButton {
  command: string;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export interface PitGroup {
  title: string;
  buttons: readonly PitButton[];
}

export const PIT_TAB_TITLE = "Карьер";

export const PIT_GROUPS: readonly PitGroup[] = [
  {
    title: "Сценарий",
    buttons: [
      { command: "pit.new", label: "Новый сценарий", icon: "plus", hint: "Сценарий оптимизации карьера: модель, рельеф, ограничитель, решатель" },
      { command: "pit.economics", label: "Экономика", icon: "table", hint: "Цены, сбыт, роялти, затраты на добычу, RF, плотность", small: true },
      { command: "pit.rocks", label: "Породы", icon: "domains", hint: "Типы пород: переработка, извлечение, «только пустая», «воздух»", small: true },
      { command: "pit.slopes", label: "Углы бортов", icon: "section", hint: "Секторы по азимуту, домены, K и допуск угла", small: true },
      { command: "pit.relief", label: "Рельеф", icon: "plan", hint: "ЦТМ рельефа и ограничитель блоков", small: true },
      { command: "pit.calendar", label: "Календарь", icon: "log", hint: "Q_р, Q_гм и ставка для NPV", small: true },
    ],
  },
  {
    title: "Расчёт",
    buttons: [
      { command: "pit.check", label: "Проверить шаблон", icon: "audit", hint: "Число смещений, K и ошибка угла по азимутам" },
      { command: "pit.run", label: "Запустить", icon: "play", hint: "Вложенные оболочки карьера и pit-by-pit; недоступна, пока не заданы цены, извлечения и затраты" },
    ],
  },
  {
    title: "Результаты",
    buttons: [
      { command: "pit.report", label: "Pit-by-pit", icon: "report", hint: "Таблица и график оболочек, NPV лучшего и худшего случаев" },
      { command: "pit.surfaces", label: "Поверхности", icon: "vein", hint: "ЦТМ оболочек как каркасы проекта", small: true },
      { command: "pit.solid", label: "Каркас карьера", icon: "ellipsoid", hint: "Замкнутый каркас карьера между рельефом и оболочкой", small: true },
      { command: "pit.compare", label: "Сравнить", icon: "swath", hint: "Два сценария: оболочки, NPV, блоки только в одном", small: true },
    ],
  },
];
