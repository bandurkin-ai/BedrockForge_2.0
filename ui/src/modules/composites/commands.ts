/**
 * Sample processing (ТЗ 8.2, Д-26; ADR 0018 §10): 13 commands of «Анализ → Обработка проб». Without the required
 * arguments a command opens its window (prefilled with what is given); with them it starts the job at once.
 * A new table — no confirmation (as Д-26); fields written into an existing table, rebuild and delete — `confirm`.
 * «Заменить» a result of the same name is offered only by the window; the assistant gets the free name « (2)».
 * `PREP_BUTTONS` — the nine buttons of the ribbon group (shell/ribbonConfig.ts puts them in).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";

const STAGE = "Э12";
const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const SET = P("integer", "номер набора скважин");
const TABLE = P("string", "интервальная таблица набора: имя («Пробы») или номер");
const NAME = P("string", "имя новой таблицы; занято — « (2)»");
const DOMAIN = {
  domain: P("string", "поле домена (код); композит не пересекает смену кода"),
  domain_table: P("string", "таблица поля домена, если не та же"),
  domain_wireframes: P("string", "домены — каркасы: номера через запятую (приоритет — по порядку)"),
};
const RESULT = { result_id: P("integer", "номер результата обработки проб (дерево набора › «Обработка проб»)") };
const ORE_MODE = P("string", "режим оконтуривания", { enum: ["strict_excl", "strict", "soft", "simple", "contacts"] });
const ORE_COMMON = {
  mode: ORE_MODE,
  thickness_field: P("string", "числовое поле мощности пробы; не задано — длина по стволу"),
  missing: P("string", "пропуски: break — разрывают интервал (по умолчанию), waste — пустая порода до missing_max", { enum: ["break", "waste"] }),
  missing_max: P("number", "наибольший пропуск, м, при missing = waste"),
  contacts_values: P("string", "коды домена тела через «,»; по умолчанию все непустые"),
  ...DOMAIN,
};
const WINDOW = "Без обязательных параметров — окно с предпросмотром на выбранной скважине и сводкой «до/после».";

export const commands: Command[] = [
  {
    id: "composite.run",
    title: "Композиты",
    description:
      `Композиты по длине, по доменам или по уступам (ТЗ 8.2.2) — новая таблица набора (LENGTH, X, Y, Z, поля, DENSITY, SAMPLED_LEN, N_SAMPLES, DOMAIN, FLAG). ${WINDOW} ` +
      "С set_id, table и method — сразу задание. Пропуск не становится нулём.",
    params: {
      set_id: SET,
      table: TABLE,
      method: P("string", "length — по длине, domain — по доменам, bench — по уступам", { enum: ["length", "domain", "bench"] }),
      fields: P("string", "поля через запятую; не заданы — все числовые"),
      length: P("number", "длина композита L, м (0,1–100; по умолчанию 2)"),
      remainder: P("string", "остаток: attach — присоединить к последнему, drop — отбросить, equal — равные части", { enum: ["attach", "drop", "equal"] }),
      f_min: P("number", "доля остатка f_min (0–1; 0,5): остаток ≥ f_min·L — отдельный композит"),
      p_min: P("number", "мин. доля опробованной длины (0–1; 0,5): меньше — пусто и FLAG «мало проб»"),
      origin: P("string", "отсчёт: domain — от начала участка, collar — от устья, first — от кровли первой пробы", { enum: ["domain", "collar", "first"] }),
      bench: P("number", "высота уступа H, м (1–50; 10)"),
      bench_top: P("number", "отметка верха уступов Z; не задана — максимум Z устьев, округлённый вверх до H"),
      density: P("string", "поле плотности для взвешивания"),
      unsampled: P("number", "значение непробованных участков; не задано — пусто (не ноль)"),
      name: NAME,
      ...DOMAIN,
    },
    stage: STAGE,
  },
  {
    id: "composite.code",
    title: "Кодировать каркасами",
    description: `Кодирование проб замкнутыми каркасами (ТЗ 8.2.2) — новая таблица «‹Таблица› — коды»; split — делить пробы на границе тела (по умолчанию да). ${WINDOW} С set_id, table и wireframe_ids — сразу задание.`,
    params: { set_id: SET, table: TABLE, wireframe_ids: P("string", "номера каркасов через запятую; перекрытие — по порядку"), split: P("boolean", "делить пробы на границе каркаса"), name: NAME },
    stage: STAGE,
  },
  {
    id: "composite.merge",
    title: "Объединить таблицы",
    description: `Объединение интервальных таблиц набора (ТЗ 8.2.2): все границы FROM/TO, значения покрывающих интервалов, нет покрытия — пусто. Новая таблица. ${WINDOW} С set_id и tables — сразу задание.`,
    params: { set_id: SET, tables: P("string", "таблицы через запятую (не меньше двух)"), name: NAME },
    stage: STAGE,
  },
  {
    id: "composite.coords",
    title: "Координаты проб",
    description: `Копия таблицы с координатами начала, середины и конца проб XB…ZE по траекториям (ТЗ 8.2.1). Новая таблица. ${WINDOW} С set_id и table — сразу задание.`,
    params: { set_id: SET, table: TABLE, name: NAME },
    stage: STAGE,
  },
  {
    id: "composite.topcut",
    title: "Урезка",
    description:
      `Урезка ураганных проб (ТЗ 8.2.2): поля ‹F›_CUT = min(g, T) и ‹F›_CUT_F в таблице источника; T — percentile (90–99,99, взвешенный по длине, по доменам) или value. ${WINDOW} ` +
      "С set_id, table, field и порогом — сразу задание (после подтверждения).",
    params: {
      set_id: SET,
      table: TABLE,
      field: P("string", "поле содержания"),
      percentile: P("number", "перцентиль P, % (90–99,99; 99)"),
      value: P("number", "порог T в единицах поля"),
      thresholds: P("string", "пороги по доменам: «КОД=значение; …»"),
      ...DOMAIN,
    },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "composite.equivalent",
    title: "Эквивалент металла",
    description:
      `Эквивалент металла (ТЗ 8.2.2): поле name = g₁ + Σ g_e·(P_e·R_e·k_e)/(P₁·R₁·k₁) в таблице источника; пусто в компоненте — пусто, если не nan_zero. ${WINDOW} ` +
      "С set_id, table, base и others — сразу задание (после подтверждения).",
    params: {
      set_id: SET,
      table: TABLE,
      name: P("string", "имя поля; не задано — ‹base›EQ"),
      base: P("string", "поле основного металла"),
      base_price: P("number", "цена основного металла"),
      base_recovery: P("number", "извлечение основного металла (0–1; 0,9)"),
      others: P("string", "прочие металлы: «ПОЛЕ цена извлечение; …», например «CU 9000 0,9»"),
      nan_zero: P("boolean", "пустое содержание компонента считать нулём"),
    },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "composite.declus",
    title: "Декластеризация",
    description:
      "Веса декластеризации (ТЗ 8.2.1, перенос BF): cell — ячейки в плане, polygon — полигоны, cell3d — ячейки 3D с anisy, anisz (GSLIB declus). Поле DECL_W в таблице источника и отчёт «размер → среднее». " +
      `${WINDOW} С set_id, table, field, method (и cmin, cmax у ячеек) — сразу задание (после подтверждения).`,
    params: {
      set_id: SET,
      table: TABLE,
      field: P("string", "поле содержания"),
      method: P("string", "метод", { enum: ["cell", "polygon", "cell3d"] }),
      cmin: P("number", "наименьший размер ячейки, м"),
      cmax: P("number", "наибольший размер ячейки, м"),
      ncell: P("integer", "число размеров (1–500; 20)"),
      noff: P("integer", "число сдвигов начала (1–64; 8)"),
      minmax: P("string", "критерий: min — минимум среднего, max — максимум", { enum: ["min", "max"] }),
      anisy: P("number", "анизотропия по Y (cell3d)"),
      anisz: P("number", "анизотропия по Z (cell3d)"),
    },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "composite.ore_intervals",
    title: "Рудные интервалы (ГКЗ)",
    description:
      "Кондиционные рудные интервалы по методике ГКЗ (Д-26, ТЗ 7.35): бортовое содержание cutoff (Сб), мин. мощность m_min (Мрт), макс. мощность прослоя m_waste_max (Мпп), МС = Сб·Мрт (mc_min), Смин (c_min). " +
      `Новая таблица «Рудные интервалы» и поля ORE_ID, ORE_CLS проб. ${WINDOW} С set_id, table, field, cutoff, m_min, m_waste_max — сразу задание.`,
    params: {
      set_id: SET,
      table: TABLE,
      field: P("string", "поле содержания"),
      cutoff: P("number", "бортовое содержание Сб"),
      m_min: P("number", "минимальная мощность рудного тела Мрт, м"),
      m_waste_max: P("number", "максимальная мощность пустого прослоя Мпп, м"),
      mc_min: P("number", "минимальный метропроцент МС; не задан — Сб·Мрт"),
      c_min: P("number", "минимальное содержание Смин; не задано — выключено"),
      ...ORE_COMMON,
      name: NAME,
    },
    stage: STAGE,
  },
  {
    id: "composite.conditions_analysis",
    title: "Анализ кондиций",
    description:
      "Анализ кондиций (Д-26, Т8.2-20): варианты Сб × Мрт × Мпп (до 200) тем же расчётом, что «Рудные интервалы (ГКЗ)»; таблица вариантов — число интервалов, среднее содержание, суммарная и средняя мощность, Σ метропроцента. " +
      `${WINDOW} С set_id, table, field, cutoffs, m_min, m_waste_max — сразу задание.`,
    params: {
      set_id: SET,
      table: TABLE,
      field: P("string", "поле содержания"),
      cutoffs: P("string", "бортовые содержания через «;», например «0,5; 1; 1,5»"),
      m_min: P("string", "мин. мощности Мрт через «;»"),
      m_waste_max: P("string", "макс. мощности прослоя Мпп через «;»"),
      ...ORE_COMMON,
      exclude_field: P("string", "поле для исключения проб, например CODE после «Кодировать каркасами»"),
      exclude_values: P("string", "значения поля exclude_field для исключения через «,», например «Карьер»"),
    },
    stage: STAGE,
  },
  {
    id: "composite.ore_intervals_edit",
    title: "Правка рудного интервала",
    description:
      "Ручная правка результата «Рудные интервалы» по границам проб одной скважины (Д-26): split — разделить, merge — объединить, move — сдвинуть границу from → to. Кондиции перепроверяются, MANUAL = 1.",
    params: { ...RESULT, hole: P("string", "скважина"), action: P("string", "действие", { enum: ["split", "merge", "move"] }), from: P("number", "глубина, м"), to: P("number", "глубина, м") },
    stage: STAGE,
  },
  {
    id: "composite.ore_protocol",
    title: "Протокол оконтуривания",
    description: "Протокол выделения рудных интервалов в книгу XLSX или CSV (длинная форма): параметры, интервалы, сводка, протокол, листы скважин в форме И1.",
    params: { ...RESULT, format: P("string", "формат", { enum: ["xlsx", "csv"] }), holes: P("string", "скважины для листов в форме И1, через запятую (до 50)") },
    uiOnly: true,
    stage: STAGE,
  },
  {
    id: "composite.rebuild",
    title: "Перестроить результат",
    description: "Перестроить результат обработки проб по прежним параметрам на текущих данных; с тем же хешем входов расчёт не повторяется — снимается «устарел».",
    params: RESULT,
    confirm: true,
    stage: STAGE,
  },
  {
    id: "composite.delete",
    title: "Удалить результат",
    description: "Удалить результат обработки проб: таблицу-результат или колонки, записанные в таблицу источника.",
    params: RESULT,
    confirm: true,
    stage: STAGE,
  },
];

export interface PrepButton {
  command: string;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

/** The ribbon group «Анализ → Обработка проб» (ТЗ 8.2.3; ADR 0018 §10): nine buttons. */
export const PREP_GROUP_TITLE = "Обработка проб";
export const PREP_BUTTONS: readonly PrepButton[] = [
  { command: "composite.run", label: "Композиты", icon: "striplog", hint: "Композиты по длине, по доменам или по уступам: предпросмотр на скважине и сводка «до/после»" },
  { command: "composite.code", label: "Кодировать каркасами", icon: "wireframe", hint: "Код каркаса у каждой пробы; пробы делятся на границе тела", small: true },
  { command: "composite.merge", label: "Объединить таблицы", icon: "columns", hint: "Интервальные таблицы набора в одну по всем границам FROM/TO", small: true },
  { command: "composite.coords", label: "Координаты проб", icon: "plan", hint: "Координаты начала, середины и конца проб по траекториям", small: true },
  { command: "composite.topcut", label: "Урезка", icon: "histogram", hint: "Урезка ураганных проб по перцентилю или значению, по доменам, с отчётом «до/после»" },
  { command: "composite.equivalent", label: "Эквивалент металла", icon: "spark", hint: "Поле эквивалентного содержания по ценам и извлечению", small: true },
  { command: "composite.declus", label: "Декластеризация", icon: "quad", hint: "Веса декластеризации DECL_W: ячейки и полигоны в плане, ячейки в 3D", small: true },
  { command: "composite.ore_intervals", label: "Рудные интервалы (ГКЗ)", icon: "drillholes", hint: "Кондиционные рудные интервалы по Сб, Мрт, Мпп и МС (методика ГКЗ)" },
  { command: "composite.conditions_analysis", label: "Анализ кондиций", icon: "table", hint: "Варианты кондиций Сб × Мрт × Мпп: число интервалов, мощность, содержание, метропроцент", small: true },
];
