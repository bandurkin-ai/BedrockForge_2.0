/**
 * QAQC of the control samples (ТЗ 8.4; ADR 0020 §9): the 15 commands `qaqc.*` of Э14 with their parameters — the
 * ribbon «Анализ → QAQC», the tab «QAQC» of the bottom panel, the window «Настройка QAQC», the tree «Отчёты»,
 * Ctrl+K and the assistant run the same commands. Every command is declared here at once (14.15); until its
 * handler is bound (`bind.ts` — the tab, `bindSetup.ts` — the window «Настройка») it stays unavailable with the
 * stage. `QAQC_BUTTONS` — the buttons of the ribbon group (shell/ribbonConfig.ts puts them in).
 * Lists in the parameters are text: comma-separated or a JSON array; objects — JSON text.
 */
import type { Command, ParamSchema } from "../../shell/commands";

const STAGE = "Э14";
const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

/** Subtabs of the tab «QAQC» (ADR 0020 §11) in their order. */
export type QaqcSection = "standards" | "blanks" | "duplicates" | "external" | "batches" | "report";
export const QAQC_SECTIONS: readonly QaqcSection[] = ["standards", "blanks", "duplicates", "external", "batches", "report"];
export const SECTION_TITLES: Record<QaqcSection, string> = {
  standards: "Стандарты",
  blanks: "Бланки",
  duplicates: "Дубликаты",
  external: "Внешний контроль",
  batches: "Партии",
  report: "Отчёт",
};

/** Types of the records after the mapping of the codes of the file (ADR 0020 §3). */
export const QAQC_TYPES = ["ORIG", "CRM", "BLANK", "DUP_F", "DUP_C", "DUP_P", "UMP"] as const;
export type QaqcType = (typeof QAQC_TYPES)[number];
export const TYPE_TITLES: Record<QaqcType, string> = {
  ORIG: "исходные",
  CRM: "стандарты (CRM)",
  BLANK: "бланки",
  DUP_F: "полевые дубликаты",
  DUP_C: "дроблёные дубликаты",
  DUP_P: "пульповые дубликаты",
  UMP: "внешний контроль",
};

export type DupKind = "DUP_F" | "DUP_C" | "DUP_P";
export const DUP_KINDS: readonly DupKind[] = ["DUP_F", "DUP_C", "DUP_P"];
export const DUP_TITLES: Record<DupKind, string> = { DUP_F: "полевые", DUP_C: "дроблёные", DUP_P: "пульповые" };

/** Tabs of the window «Настройка QAQC» (ADR 0020 §11). */
export type SetupTab = "data" | "types" | "crm" | "dl" | "limits";
export const SETUP_TABS: readonly SetupTab[] = ["data", "types", "crm", "dl", "limits"];

const DATA_ID = P("integer", "номер данных QAQC (без — данные, открытые во вкладке, или первые)");
const REPORT_ID = P("integer", "номер отчёта QAQC (дерево › «Отчёты»)");
const LIST = (what: string) => P("string", `${what}: через запятую или JSON-массив`);
const LIMIT = (range: string, extra = "") => P("number", `${range}${extra}`);

export const commands: Command[] = [
  {
    id: "qaqc.setup",
    title: "Настройка QAQC",
    description: "Окно «Настройка QAQC»: data — данные контрольных проб (загрузка, обновление, удаление), types — коды типов файла, crm — справочник CRM, dl — пределы обнаружения, limits — пороги.",
    params: { tab: P("string", "вкладка окна", { enum: [...SETUP_TABS] }) },
    uiOnly: true,
    stage: STAGE,
  },
  {
    id: "qaqc.import",
    title: "Загрузить контрольные пробы",
    description:
      "Загрузить данные QAQC (ТЗ 8.4.1): из файла path (CSV, TXT, DAT, XLSX, XLS) или из таблицы table набора set_id. mapping — JSON сопоставления колонок {\"SAMPLE_ID\": \"колонка\", \"TYPE\": …, \"PARENT_ID\", \"CRM_ID\", \"LAB\", \"BATCH\", \"DATE\", \"SEQ\", \"HOLE\", \"elements\": {\"AU\": \"г/т\"}}; без него — автоподбор по именам колонок. replace_id — заменить эти данные (после подтверждения пользователя). Задание «Загрузка QAQC» — на вкладке «Задания».",
    params: {
      path: P("string", "путь к файлу контрольных проб"),
      set_id: P("integer", "номер набора скважин для загрузки из таблицы набора"),
      table: P("string", "таблица набора: имя или номер (без — таблица проб)"),
      name: P("string", "имя данных QAQC (без — по имени файла или таблицы)"),
      mapping: P("string", "JSON сопоставления колонок"),
      replace_id: P("integer", "номер данных QAQC, которые заменяются"),
    },
    stage: STAGE,
  },
  {
    id: "qaqc.open",
    title: "Раздел QAQC",
    description:
      "Открыть вкладку «QAQC» нижней панели на разделе: standards — стандарты CRM и контрольная карта, blanks — бланки, duplicates — дубликаты (kind: DUP_F полевые, DUP_C дроблёные, DUP_P пульповые), external — внешний контроль, batches — партии, report — отчёты. element — элемент (без — открытый или первый). Ответ — число записей и доля отказов раздела.",
    params: {
      section: P("string", "раздел вкладки", { enum: [...QAQC_SECTIONS] }),
      data_id: DATA_ID,
      element: P("string", "элемент, например AU"),
      kind: P("string", "тип дубликатов", { enum: [...DUP_KINDS] }),
    },
    required: ["section"],
    stage: STAGE,
  },
  {
    id: "qaqc.filter",
    title: "Фильтр QAQC",
    description:
      "Фильтр записей вкладки «QAQC»: действует на все подвкладки, графики и отчёт и сохраняется в отчёте. Заданный параметр заменяет своё условие, остальные остаются; reset — сначала снять все условия. Пустая строка снимает условие. where — JSON-массив до 20 условий «любое поле» [{\"field\": \"…\", \"op\": \"eq|ne|in|prefix|contains|empty|ge|le|between\", \"value\": …}], условия соединяются «И». Ответ — условия и «записей N из M».",
    params: {
      labs: LIST("лаборатории"),
      batches: LIST("партии"),
      date_from: P("string", "дата с: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"),
      date_to: P("string", "дата по: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"),
      crm: LIST("стандарты CRM_ID"),
      types: LIST("типы ORIG, CRM, BLANK, DUP_F, DUP_C, DUP_P, UMP"),
      hole_prefix: P("string", "начало имени скважины"),
      where: P("string", "JSON-массив условий «любое поле»"),
      reset: P("boolean", "снять все условия перед применением остальных"),
    },
    stage: STAGE,
  },
  {
    id: "qaqc.config",
    title: "Настройка QAQC: типы, DL, пороги",
    description:
      "Изменить настройку QAQC проекта; готовые отчёты станут «устаревшими». types — JSON {\"код файла\": \"тип\"}, dl — JSON [{\"element\", \"lab\" (null — любая), \"value\", \"unit\"}]. Пороги (ТЗ 8.4.2): sigma_warn и sigma_fail — 1–5σ (warn < fail), run — серия 5–20, k_blank — 1–50·DL, k_dup — 0–50·DL, th_group — группа Томпсона–Ховарта 5–51 нечётная, hard_p, hard_c, hard_f, hard_ump — порог HARD 1–100 %, hard_share — доля пар 50–100 %, n_boot — 100–5000, seed — зерно.",
    params: {
      types: P("string", "JSON соответствия кодов файла типам"),
      dl: P("string", "JSON таблицы пределов обнаружения"),
      sigma_warn: LIMIT("предупреждение, σ: 1–5", " (по умолчанию 2)"),
      sigma_fail: LIMIT("отказ, σ: 1–5", " (по умолчанию 3)"),
      run: P("integer", "серия по одну сторону μ: 5–20 (по умолчанию 9)"),
      k_blank: LIMIT("бланк: отказ выше k·DL, k 1–50", " (по умолчанию 5)"),
      k_dup: LIMIT("дубликаты: пары со средним ниже k·DL не считаются, k 0–50", " (по умолчанию 5)"),
      th_group: P("integer", "пар в группе Томпсона–Ховарта: 5–51, нечётное (по умолчанию 11)"),
      hard_p: LIMIT("HARD пульповых, %: 1–100", " (по умолчанию 10)"),
      hard_c: LIMIT("HARD дроблёных, %: 1–100", " (по умолчанию 20)"),
      hard_f: LIMIT("HARD полевых, %: 1–100", " (по умолчанию 30)"),
      hard_ump: LIMIT("HARD внешнего контроля, %: 1–100", " (по умолчанию 10)"),
      hard_share: LIMIT("доля пар не выше порога HARD, %: 50–100", " (по умолчанию 90)"),
      n_boot: P("integer", "реализаций бутстрапа: 100–5000 (по умолчанию 1000)"),
      seed: P("integer", "зерно бутстрапа (по умолчанию 12345)"),
    },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "qaqc.crm",
    title: "Справочник CRM",
    description: "Добавить или изменить строки справочника CRM и удалить строки; готовые отчёты станут «устаревшими». rows — JSON [{\"crm_id\", \"element\", \"mean\" > 0, \"sd\" > 0, \"unit\", \"source\"}] (ключ — CRM_ID и элемент без учёта регистра); remove — JSON [{\"crm_id\", \"element\" (без — все элементы)}].",
    params: { rows: P("string", "JSON строк справочника"), remove: P("string", "JSON удаляемых строк") },
    confirm: true,
    stage: STAGE,
  },
  {
    id: "qaqc.report",
    title: "Сформировать отчёт QAQC",
    description:
      "Сформировать отчёт QAQC (ТЗ 8.4.3) по данным data_id с фильтром вкладки: сводка, стандарты, бланки, дубликаты, внешний контроль, партии, параметры, хеши данных; файл XLSX. elements — элементы через запятую (без — все), charts: all — все графики, flagged — с предупреждениями и отказами, none — без графиков; path — сразу сохранить XLSX по пути. Ответ — имя и номер отчёта, число партий и отказов, критерии дубликатов.",
    params: {
      data_id: DATA_ID,
      name: P("string", "имя отчёта (без — по данным и дате)"),
      elements: LIST("элементы"),
      charts: P("string", "графики отчёта", { enum: ["all", "flagged", "none"] }),
      path: P("string", "путь файла XLSX"),
    },
    stage: STAGE,
  },
  { id: "qaqc.report_open", title: "Открыть отчёт QAQC", description: "Открыть отчёт QAQC во вкладке «QAQC › Отчёт»: состояние, пометка «устарел», хеши, сводка.", params: { report_id: REPORT_ID }, required: ["report_id"], stage: STAGE },
  {
    id: "qaqc.report_save",
    title: "Сохранить отчёт QAQC",
    description: "Сохранить файл отчёта QAQC: kind xlsx (по умолчанию) — по пути path или через окно выбора файла; pdf появится на этапе Э22.",
    params: { report_id: REPORT_ID, kind: P("string", "формат файла", { enum: ["xlsx", "pdf"] }), path: P("string", "путь файла") },
    required: ["report_id"],
    stage: STAGE,
  },
  { id: "qaqc.report_rebuild", title: "Перестроить отчёт QAQC", description: "Повторить расчёт отчёта с сохранёнными фильтром и параметрами на текущих данных и настройке (номер версии отчёта растёт). Данные отчёта удалены — недоступно.", params: { report_id: REPORT_ID }, required: ["report_id"], stage: STAGE },
  { id: "qaqc.report_rename", title: "Переименовать отчёт QAQC", description: "Переименовать отчёт QAQC; занятое имя — отказ.", params: { report_id: REPORT_ID, name: P("string", "новое имя") }, required: ["report_id", "name"], stage: STAGE },
  { id: "qaqc.report_delete", title: "Удалить отчёт QAQC", description: "Удалить отчёт QAQC вместе с его файлами и копией записей (данные QAQC не меняются).", params: { report_id: REPORT_ID }, required: ["report_id"], confirm: true, stage: STAGE },
  { id: "qaqc.data_rename", title: "Переименовать данные QAQC", description: "Переименовать данные контрольных проб; занятое имя — отказ.", params: { data_id: P("integer", "номер данных QAQC"), name: P("string", "новое имя") }, required: ["data_id", "name"], stage: STAGE },
  { id: "qaqc.data_refresh", title: "Обновить данные QAQC", description: "Повторить загрузку данных QAQC из того же файла или таблицы набора с тем же сопоставлением (данные помечены «устарели»); готовые отчёты станут «устаревшими».", params: { data_id: P("integer", "номер данных QAQC") }, required: ["data_id"], confirm: true, stage: STAGE },
  { id: "qaqc.data_delete", title: "Удалить данные QAQC", description: "Удалить данные контрольных проб (отчёты остаются: у них своя копия записей, они станут «устаревшими»).", params: { data_id: P("integer", "номер данных QAQC") }, required: ["data_id"], confirm: true, stage: STAGE },
];

/**
 * A call of the assistant that needs the user's confirmation besides the commands with `confirm` (ADR 0020 §9):
 * `qaqc.import` only when it replaces data (`replace_id`) — its handler asks with its own window.
 */
export function needsConfirm(id: string, args: Record<string, unknown>): boolean {
  if (id === "qaqc.import") return args.replace_id !== undefined && args.replace_id !== null;
  return commands.find((c) => c.id === id)?.confirm === true;
}

/** Icons of the QAQC buttons: `shell/Icon.tsx` gets each of them on acceptance of 14.15. */
export type QaqcIcon = "qaqc" | "standards" | "blanks" | "duplicates" | "external" | "batches" | "report";

export interface QaqcButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: QaqcIcon;
  hint: string;
  small?: boolean;
}

/** The ribbon group «Анализ → QAQC» (ТЗ 8.4.3; ADR 0020 §9): «Настройка» and the six sections. */
export const QAQC_GROUP_TITLE = "QAQC";
const SECTION_ICONS: Record<QaqcSection, QaqcIcon> = { standards: "standards", blanks: "blanks", duplicates: "duplicates", external: "external", batches: "batches", report: "report" };
const SECTION_HINTS: Record<QaqcSection, string> = {
  standards: "Контрольные карты CRM: μ, ±2σ, ±3σ, серии, возможные подмены",
  blanks: "Бланки: отказ выше k·DL, перенос загрязнения",
  duplicates: "Полевые, дроблёные, пульповые дубликаты: HARD, рассеяние, Томпсон–Ховарт",
  external: "Внешний контроль: смещение, t-критерий, RMA",
  batches: "Партии со статусом и причинами отказа",
  report: "Отчёт QAQC: XLSX с графиками, параметрами и хешами данных",
};
export const QAQC_BUTTONS: readonly QaqcButton[] = [
  { command: "qaqc.setup", label: "Настройка", icon: "qaqc", hint: "Данные контрольных проб, типы, справочник CRM, пределы обнаружения, пороги" },
  ...QAQC_SECTIONS.map((section): QaqcButton => ({ command: "qaqc.open", args: { section }, label: SECTION_TITLES[section], icon: SECTION_ICONS[section], hint: SECTION_HINTS[section], small: true })),
];
