/**
 * True thickness and ore intercepts (ТЗ 8.5; ADR 0021 §9): the commands `intercept.tt`, `intercept.run` of
 * «Анализ → Пересечения» (after «Обработка проб»). Without the required arguments a command opens its window
 * (prefilled with what is given); with them it starts the job. Fields written into an existing table — `confirm`;
 * a new table — none. «Заменить» a table of the same name — only the window; the assistant takes the free name.
 * Rebuild and delete — `composite.rebuild`, `composite.delete` (Э12). No default keys: they are set in settings.
 * `INTERCEPT_BUTTONS` — the buttons of the ribbon group (shell/ribbonConfig.ts puts them in).
 */
import type { Command, ParamSchema } from "../../shell/commands";
import type { IconName } from "../../shell/Icon";
import type { PrepButton } from "../composites/commands";

const STAGE = "Э15";
const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const SET = P("integer", "номер набора скважин");
const TABLE = P("string", "интервальная таблица набора: имя («Пробы») или номер");

/** The orientation of the body (ADR 0021 §4): the same arguments for both commands. */
const ORIENTATION: Record<string, ParamSchema> = {
  source: P("string", "источник ориентировки: global — α и Aп на весь расчёт, domain — по коду домена, mesh — по каркасу, core — ориентированный керн, trend — тренд проекта (глобальный: плоскость малой оси; структурный: нормаль в середине интервала)", { enum: ["global", "domain", "mesh", "core", "trend"] }),
  alpha: P("number", "global: угол падения α, ° (0–90)"),
  dipdir: P("number", "global: азимут падения Aп, ° (0–360)"),
  domain: P("string", "domain: поле кода домена"),
  domain_table: P("string", "domain: таблица поля кода, если не та же"),
  domain_wireframes: P("string", "domain: коды — каркасы-домены, номера через запятую"),
  attitudes: P("string", "domain: таблица «код=α/Aп; …», например «QZ=60/120; SH=45/90» (до 1000 кодов)"),
  wireframe_ids: P("string", "mesh: номера каркасов и ЦТМ через запятую (до 16)"),
  d_max: P("number", "mesh: поиск ближайшего треугольника d_max, м (0–1000; 50; 0 — только пересечения)"),
  r_n: P("number", "mesh: радиус осреднения нормали r_n, м (0–200; 0)"),
  core_table: P("string", "core: таблица замеров керна (события или интервалы)"),
  core_alpha: P("string", "core: поле угла α_к к оси керна"),
  core_beta: P("string", "core: поле угла β_к от линии отсчёта"),
  core_line: P("string", "core: линия отсчёта — boh (низ керна, по умолчанию) или toh (верх)", { enum: ["boh", "toh"] }),
  d_core: P("number", "core: ближайший замер по стволу d_к, м (0–1000; 10)"),
  trend_id: P("integer", "trend: номер тренда из раздела «Тренды» (глобального или структурного)"),
  fallback_alpha: P("number", "если ориентировка не найдена — глобальная α, °"),
  fallback_dipdir: P("number", "если ориентировка не найдена — глобальный Aп, °"),
  eps: P("number", "порог вырождения ε_в (0,01–0,5; 0,1): TT / LENGTH меньше — признак «вдоль тела»"),
  step: P("number", "часть искривлённого ствола, м (0,1–10; 1)"),
  angles: P("boolean", "записать ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ (по умолчанию да)"),
};

/** `/run` has no `angles`: the table of intercepts always has the angles (e15-api.md). */
const RUN_ORIENTATION: Record<string, ParamSchema> = Object.fromEntries(Object.entries(ORIENTATION).filter(([k]) => k !== "angles"));

const WINDOW = "Без обязательных параметров — окно с предпросмотром по выбранной скважине.";

export const commands: Command[] = [
  {
    id: "intercept.tt",
    title: "Истинная мощность",
    description:
      `Истинная мощность интервалов (ТЗ 8.5.1–8.5.2): поля TT, TT_FLAG, TT_SRC и углы в таблице источника. ${WINDOW} ` +
      "С set_id, table и source — сразу задание. Нет ориентировки или траектории — TT пусто (не ноль), признак в TT_FLAG.",
    params: { set_id: SET, table: TABLE, ...ORIENTATION },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "intercept.run",
    title: "Рудные пересечения",
    description:
      `Рудные пересечения по правилу ТЗ 8.5.3 — новая таблица набора «Рудные пересечения» (LENGTH, TT, содержания, LEN_G, TT_G, LABEL, «в том числе»). ${WINDOW} ` +
      "С set_id, table, field и cutoff — сразу задание. Без source истинная мощность пуста (TT_SRC «нет»).",
    params: {
      set_id: SET,
      table: P("string", "таблица проб или композитов набора: имя или номер"),
      field: P("string", "поле содержания"),
      cutoff: P("number", "бортовое содержание c (больше нуля)"),
      l_min: P("number", "мин. длина пересечения L_min, м (0–100; 2) — или m_min при min_by = tt"),
      min_by: P("string", "критерий приёмки: length — по длине, tt — по истинной мощности", { enum: ["length", "tt"] }),
      waste_max: P("number", "макс. пустая порода внутри пересечения L_п, м (0–50; 2)"),
      include_cutoff: P("number", "борт «в том числе» c2 > c; не задан — без «в том числе»"),
      density: P("string", "поле плотности для взвешивания"),
      missing: P("string", "пропуски: waste — пустая порода с g = 0 (по умолчанию), break — разрыв", { enum: ["waste", "break"] }),
      fields: P("string", "прочие поля содержаний через запятую; не заданы — все числовые"),
      name: P("string", "имя новой таблицы (до 200 знаков); занято — « (2)»"),
      ...RUN_ORIENTATION,
    },
    stage: STAGE,
  },
];

/** The ribbon group «Анализ → Пересечения» (ТЗ 8.5.4; ADR 0021 §9). Icons `thickness`, `intercepts` — shell/Icon.tsx. */
/** The two icons are added to shell/Icon.tsx with the ribbon group (by the orchestrator, ADR 0021 §9). */
const icon = (name: string): IconName => name as IconName;

export const INTERCEPT_GROUP_TITLE = "Пересечения";
export const INTERCEPT_BUTTONS: readonly PrepButton[] = [
  { command: "intercept.tt", label: "Истинная мощность", icon: icon("thickness"), hint: "Поле TT в таблице проб по ориентировке тела: глобальной, по домену, по каркасу или по керну" },
  { command: "intercept.run", label: "Рудные пересечения", icon: icon("intercepts"), hint: "Таблица рудных пересечений по борту, L_min и L_п с подписями «12,0 м @ 2,10 г/т (ИМ 9,8 м)»" },
];
