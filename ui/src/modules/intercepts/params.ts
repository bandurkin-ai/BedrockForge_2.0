/**
 * Parameters of true thickness and ore intercepts as data (ТЗ 8.5.4; ADR 0021 §4, §9–10): the block «Ориентировка»
 * (four sources and «если не найдена — глобальная»), the table of domains «код → α, Aп», the ranges of 8.5.4, the
 * check of the values before a request — a value out of range never reaches the core, empty never becomes zero —
 * and the same check of the flat arguments of the commands `intercept.tt`, `intercept.run`. Pure: no React.
 * The body of the requests: `orientation` — a list of 1–2 sources by priority (`{source: "global", alpha, dipdir}`,
 * `{source: "domain", field?, table?, wireframe_ids?, attitudes}`, `{source: "mesh", wireframe_ids, d_max, r_n}`,
 * `{source: "core", table, alpha, beta, line, d_core}`), then `eps`, `step`, `angles` and the parameters of the rule.
 */
import { checkParam, checkParams, numberText, parseIdList, parseNumber, type FormValues, type ParamSpec, type ToolSpec } from "../composites/params";

export const INTERCEPTS_API = "/intercepts";

export type OrientSource = "global" | "domain" | "mesh" | "core";
export const ORIENT_SOURCES: readonly OrientSource[] = ["global", "domain", "mesh", "core"];
export const SOURCE_TITLES: Record<OrientSource, string> = {
  global: "Глобальная",
  domain: "По домену",
  mesh: "По каркасу",
  core: "Ориентированный керн",
};

// ---- the ranges of 8.5.4 ----

const num = (key: string, label: string, min: number, max: number, def: number | null, unit?: string, hint?: string): ParamSpec => ({ key, label, kind: "number", min, max, default: def, ...(unit ? { unit } : {}), ...(hint ? { hint } : {}) });

export const ALPHA = num("alpha", "Угол падения α", 0, 90, null, "°");
export const DIPDIR = num("dipdir", "Азимут падения Aп", 0, 360, null, "°");
export const D_MAX = num("d_max", "Поиск ближайшего d_max", 0, 1000, 50, "м", "0 — только пересечения ствола с каркасом");
export const R_N = num("r_n", "Радиус осреднения нормали r_n", 0, 200, 0, "м", "0 — нормаль ближайшего треугольника");
export const D_CORE = num("d_core", "Ближайший замер d_к", 0, 1000, 10, "м", "по стволу от ближайшего конца интервала");

/** The common parameters of the calculation of TT (8.5.4). */
export const TT_PARAMS: readonly ParamSpec[] = [
  num("eps", "Порог вырождения ε_в", 0.01, 0.5, 0.1, undefined, "TT / LENGTH меньше ε_в — признак «вдоль тела» (бит 1)"),
  num("step", "Часть ствола", 0.1, 10, 1, "м", "длина части искривлённого ствола при суммировании"),
  { key: "angles", label: "Записать углы ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ", kind: "boolean", default: true },
];

/** The parameters of the rule of ore intercepts 8.5.3 (the window — 15.16; the command — here). */
export const RUN_PARAMS: readonly ParamSpec[] = [
  { key: "cutoff", label: "Бортовое содержание c", kind: "number", min: 0, default: null },
  num("l_min", "Мин. длина пересечения L_min", 0, 100, 2, "м"),
  {
    key: "min_by",
    label: "Критерий приёмки",
    kind: "choice",
    default: "length",
    choices: [
      { value: "length", label: "по длине (L_min)" },
      { value: "tt", label: "по истинной мощности (m_min)" },
    ],
  },
  num("waste_max", "Макс. пустая порода внутри L_п", 0, 50, 2, "м"),
  { key: "include_cutoff", label: "Борт «в том числе» c2", kind: "number", min: 0, optional: true, default: null, hint: "больше c; пусто — без «в том числе»" },
  {
    key: "missing",
    label: "Пропуски",
    kind: "choice",
    default: "waste",
    choices: [
      { value: "waste", label: "пустая порода (g = 0)" },
      { value: "break", label: "разрыв пересечения" },
    ],
  },
  { key: "density", label: "Поле плотности", kind: "field", numeric: true, optional: true },
  { key: "name", label: "Имя таблицы", kind: "text", optional: true, default: "Рудные пересечения" },
];

const sourceNeeds = { table: "one", fields: "none", domain: false } as const;

/** «Истинная мощность»: fields TT, TT_FLAG, TT_SRC (and the angles) in the source table — `confirm`. */
export const TT_TOOL: ToolSpec = {
  id: "tt",
  command: "intercept.tt",
  title: "Истинная мощность",
  api: INTERCEPTS_API,
  endpoint: "/tt",
  preview: () => "tt",
  source: sourceNeeds,
  params: TT_PARAMS,
  required: ["set_id", "table", "source"],
  writes: "fields",
  note: "Истинная мощность интервалов по ориентировке тела (ТЗ 8.5.1–8.5.2): поля TT, TT_FLAG, TT_SRC в таблице источника; пусто не становится нулём.",
};

/** «Рудные пересечения»: a new table of the set (409 `table_exists` → «Заменить?»). */
export const RUN_TOOL: ToolSpec = {
  id: "run",
  command: "intercept.run",
  title: "Рудные пересечения",
  api: INTERCEPTS_API,
  endpoint: "/run",
  preview: () => "run",
  source: { table: "one", fields: "one", domain: false },
  params: [...RUN_PARAMS, ...TT_PARAMS],
  required: ["set_id", "table", "field", "cutoff"],
  writes: "table",
  note: "Рудные пересечения по правилу ТЗ 8.5.3: серии рудных проб, присоединение через пустую породу, приёмка по L_min или m_min, «в том числе».",
};

// ---- the table of domains ----

export interface AttitudeRow {
  code: string;
  alpha: string;
  dipdir: string;
}

export const ATTITUDES_MAX = 1000;

/** A row with nothing typed (the last empty row of the grid) is not a row. */
const blank = (r: AttitudeRow): boolean => !r.code.trim() && !r.alpha.trim() && !r.dipdir.trim();

/** «QZ=60/120; SH=45/90» (the command) → rows; a part without «=» and «/» — null. */
export function parseAttitudes(text: string): AttitudeRow[] | null {
  const out: AttitudeRow[] = [];
  for (const part of text.split(";").map((x) => x.trim()).filter(Boolean)) {
    const m = /^(.+?)\s*=\s*([^/]*)\/(.*)$/.exec(part);
    if (!m) return null;
    out.push({ code: m[1].trim(), alpha: m[2].trim(), dipdir: m[3].trim() });
  }
  return out;
}

/** Rows → «QZ=60/120; SH=45/90». */
export const attitudesText = (rows: readonly AttitudeRow[]): string =>
  rows
    .filter((r) => !blank(r))
    .map((r) => `${r.code.trim()}=${r.alpha.trim()}/${r.dipdir.trim()}`)
    .join("; ");

/**
 * Text pasted from the clipboard (Excel: tabs; CSV: «;» or «,» between three cells) → rows «код, α, Aп». A first
 * line whose α is not a number is a header and is skipped; lines with fewer than three cells are skipped.
 */
export function parseAttitudeTable(text: string): AttitudeRow[] {
  const out: AttitudeRow[] = [];
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  lines.forEach((line, i) => {
    const cells = (line.includes("\t") ? line.split("\t") : line.includes(";") ? line.split(";") : line.split(",")).map((c) => c.trim());
    if (cells.length < 3) return;
    if (i === 0 && parseNumber(cells[1]) === null) return;
    out.push({ code: cells[0], alpha: cells[1], dipdir: cells[2] });
  });
  return out;
}

/** Codes found in a field (or the names of wireframes) added to the rows: a code already there is kept as it is. */
export function fillCodes(rows: readonly AttitudeRow[], codes: readonly string[]): AttitudeRow[] {
  const kept = rows.filter((r) => !blank(r));
  const have = new Set(kept.map((r) => r.code.trim()));
  const added = codes.map((c) => c.trim()).filter((c) => c && !have.has(c) && (have.add(c), true));
  return [...kept, ...added.map((code) => ({ code, alpha: "", dipdir: "" }))].slice(0, ATTITUDES_MAX);
}

// ---- the state of the block «Ориентировка» ----

export interface OrientationState {
  /** false — no orientation (only `intercept.run`: TT empty, TT_SRC «нет»). */
  enabled: boolean;
  source: OrientSource;
  /** global */
  alpha: string;
  dipdir: string;
  /** domain: the code is a field of an interval table (`domainTable` "" — the source table) or wireframes */
  domainBy: "field" | "wireframes";
  domainTable: string;
  domainField: string;
  domainWireframes: number[];
  attitudes: AttitudeRow[];
  /** mesh */
  meshWireframes: number[];
  dMax: string;
  rN: string;
  /** core: a table of events or intervals, the fields α_к and β_к, the reference line */
  coreTable: string;
  coreAlpha: string;
  coreBeta: string;
  coreLine: "boh" | "toh";
  dCore: string;
  /** «Если не найдена — глобальная α, Aп» */
  fallback: boolean;
  fallbackAlpha: string;
  fallbackDipdir: string;
}

export const DEFAULT_ORIENTATION: OrientationState = {
  enabled: true,
  source: "global",
  alpha: "",
  dipdir: "",
  domainBy: "field",
  domainTable: "",
  domainField: "",
  domainWireframes: [],
  attitudes: [],
  meshWireframes: [],
  dMax: numberText(50),
  rN: numberText(0),
  coreTable: "",
  coreAlpha: "",
  coreBeta: "",
  coreLine: "boh",
  dCore: numberText(10),
  fallback: false,
  fallbackAlpha: "",
  fallbackDipdir: "",
};

export interface CheckedOrientation {
  /** null — something is missing or wrong (`reason`); [] — orientation is off. */
  orientation: Record<string, unknown>[] | null;
  /** key → Russian error: `alpha`, `dipdir`, `d_max`, `r_n`, `d_core`, `fallback_alpha`, `fallback_dipdir`,
   * `attitudes`, `attitudes.<row>.code|alpha|dipdir`, `domain`, `wireframes`, `core`. */
  errors: Record<string, string>;
  reason: string | null;
}

/** Aп 360 → 0 (ADR 0021 §2). */
const az = (v: number): number => (v === 360 ? 0 : v);

function value(p: ParamSpec, raw: string, key: string, errors: Record<string, string>, label?: string): number | undefined {
  const r = checkParam(label ? { ...p, label } : p, raw);
  if (!r.ok) {
    errors[key] = r.error;
    return undefined;
  }
  return typeof r.value === "number" ? r.value : undefined;
}

function checkAttitudes(rows: readonly AttitudeRow[], errors: Record<string, string>): { code: string; alpha: number; dipdir: number }[] {
  const out: { code: string; alpha: number; dipdir: number }[] = [];
  const seen = new Set<string>();
  const real = rows.filter((r) => !blank(r));
  if (!real.length) errors.attitudes = "Таблица доменов пуста: введите коды и углы";
  if (real.length > ATTITUDES_MAX) errors.attitudes = `Таблица доменов: не больше ${ATTITUDES_MAX} строк`;
  rows.forEach((r, i) => {
    if (blank(r)) return;
    const code = r.code.trim();
    if (!code) errors[`attitudes.${i}.code`] = `Строка ${i + 1}: введите код`;
    else if (seen.has(code)) errors[`attitudes.${i}.code`] = `Строка ${i + 1}: код «${code}» повторяется`;
    seen.add(code);
    const a = value(ALPHA, r.alpha, `attitudes.${i}.alpha`, errors, `Строка ${i + 1}, α`);
    const d = value(DIPDIR, r.dipdir, `attitudes.${i}.dipdir`, errors, `Строка ${i + 1}, Aп`);
    if (code && a !== undefined && d !== undefined) out.push({ code, alpha: a, dipdir: az(d) });
  });
  return out;
}

/** The main source of the block as the object of `orientation`; undefined — not given (see `errors`). */
function mainSource(s: OrientationState, errors: Record<string, string>): Record<string, unknown> | undefined {
  if (s.source === "global") {
    const a = value(ALPHA, s.alpha, "alpha", errors);
    const d = value(DIPDIR, s.dipdir, "dipdir", errors);
    return a !== undefined && d !== undefined ? { source: "global", alpha: a, dipdir: az(d) } : undefined;
  }
  if (s.source === "domain") {
    const attitudes = checkAttitudes(s.attitudes, errors);
    let code: Record<string, unknown> | undefined;
    if (s.domainBy === "field") {
      if (!s.domainField.trim()) errors.domain = "Выберите поле кода домена";
      else code = { field: s.domainField.trim(), ...(s.domainTable.trim() ? { table: s.domainTable.trim() } : {}) };
    } else if (!s.domainWireframes.length) errors.domain = "Отметьте каркасы-домены";
    else code = { wireframe_ids: [...s.domainWireframes] };
    return code ? { source: "domain", ...code, attitudes } : undefined;
  }
  if (s.source === "mesh") {
    if (!s.meshWireframes.length) errors.wireframes = "Отметьте каркасы или поверхности (до 16)";
    else if (s.meshWireframes.length > 16) errors.wireframes = "Не больше 16 каркасов и поверхностей";
    const dMax = value(D_MAX, s.dMax, "d_max", errors);
    const rN = value(R_N, s.rN, "r_n", errors);
    return errors.wireframes || dMax === undefined || rN === undefined ? undefined : { source: "mesh", wireframe_ids: [...s.meshWireframes], d_max: dMax, r_n: rN };
  }
  if (!s.coreTable.trim()) errors.core = "Выберите таблицу замеров керна";
  else if (!s.coreAlpha.trim() || !s.coreBeta.trim()) errors.core = "Выберите поля α_к и β_к";
  const dCore = value(D_CORE, s.dCore, "d_core", errors);
  return errors.core || dCore === undefined ? undefined : { source: "core", table: s.coreTable.trim(), alpha: s.coreAlpha.trim(), beta: s.coreBeta.trim(), line: s.coreLine, d_core: dCore };
}

/** The block «Ориентировка» checked: the list `orientation` by priority, or why it cannot be sent. */
export function checkOrientation(s: OrientationState): CheckedOrientation {
  if (!s.enabled) return { orientation: [], errors: {}, reason: null };
  const errors: Record<string, string> = {};
  const main = mainSource(s, errors);
  const list: Record<string, unknown>[] = main ? [main] : [];
  if (s.fallback && s.source !== "global") {
    const a = value(ALPHA, s.fallbackAlpha, "fallback_alpha", errors, "Глобальная α");
    const d = value(DIPDIR, s.fallbackDipdir, "fallback_dipdir", errors, "Глобальная Aп");
    if (a !== undefined && d !== undefined) list.push({ source: "global", alpha: a, dipdir: az(d) });
  }
  const keys = Object.keys(errors);
  return keys.length ? { orientation: null, errors, reason: errors[keys[0]] } : { orientation: list, errors, reason: null };
}

// ---- the flat arguments of the commands ----

const text = (args: Record<string, unknown>, k: string): string => {
  const v = args[k];
  if (typeof v === "number") return numberText(v);
  return typeof v === "string" ? v.trim() : "";
};

/**
 * The block from the arguments of `intercept.tt` / `intercept.run` (ADR 0021 §9): `source`; `alpha, dipdir`;
 * `domain, domain_table?, domain_wireframes?, attitudes` («код=α/Aп; …»); `wireframe_ids, d_max?, r_n?`;
 * `core_table, core_alpha, core_beta, core_line?, d_core?`; `fallback_alpha?, fallback_dipdir?`. Without `source`
 * and for `intercept.run` — orientation off. A string — why the arguments cannot be read.
 */
export function orientationFromArgs(args: Record<string, unknown>, optional = false): OrientationState | string {
  const src = text(args, "source");
  if (!src) return { ...DEFAULT_ORIENTATION, enabled: !optional };
  if (!(ORIENT_SOURCES as readonly string[]).includes(src)) return `«Источник ориентировки»: одно из ${ORIENT_SOURCES.join(", ")}`;
  const s: OrientationState = { ...DEFAULT_ORIENTATION, source: src as OrientSource };
  s.alpha = text(args, "alpha");
  s.dipdir = text(args, "dipdir");
  const wf = text(args, "domain_wireframes");
  if (wf) {
    const ids = parseIdList(wf);
    if (!ids) return "«domain_wireframes»: номера каркасов через запятую";
    s.domainBy = "wireframes";
    s.domainWireframes = ids;
  }
  s.domainField = text(args, "domain");
  s.domainTable = text(args, "domain_table");
  const att = text(args, "attitudes");
  if (att) {
    const rows = parseAttitudes(att);
    if (!rows) return "«attitudes»: «код=α/Aп; код=α/Aп», например «QZ=60/120; SH=45/90»";
    s.attitudes = rows;
  }
  const ids = text(args, "wireframe_ids");
  if (ids) {
    const list = parseIdList(ids);
    if (!list) return "«wireframe_ids»: номера каркасов через запятую";
    s.meshWireframes = list;
  }
  if (text(args, "d_max")) s.dMax = text(args, "d_max");
  if (text(args, "r_n")) s.rN = text(args, "r_n");
  s.coreTable = text(args, "core_table");
  s.coreAlpha = text(args, "core_alpha");
  s.coreBeta = text(args, "core_beta");
  if (text(args, "core_line") === "toh") s.coreLine = "toh";
  if (text(args, "d_core")) s.dCore = text(args, "d_core");
  s.fallbackAlpha = text(args, "fallback_alpha");
  s.fallbackDipdir = text(args, "fallback_dipdir");
  s.fallback = s.source !== "global" && Boolean(s.fallbackAlpha || s.fallbackDipdir);
  return s;
}

/** The list `orientation` of a result (`params`) → the flat arguments of its command («Параметры…»). */
export function argsFromOrientation(list: unknown): Record<string, unknown> {
  if (!Array.isArray(list) || !list.length) return {};
  const out: Record<string, unknown> = {};
  const [main, next] = list as Record<string, unknown>[];
  const src = String(main?.source ?? "");
  out.source = src;
  if (src === "global") Object.assign(out, { alpha: main.alpha, dipdir: main.dipdir });
  if (src === "domain") {
    if (typeof main.field === "string") out.domain = main.field;
    if (typeof main.table === "string") out.domain_table = main.table;
    if (Array.isArray(main.wireframe_ids)) out.domain_wireframes = main.wireframe_ids.join(", ");
    if (Array.isArray(main.attitudes))
      out.attitudes = attitudesText((main.attitudes as { code?: unknown; alpha?: unknown; dipdir?: unknown }[]).map((a) => ({ code: String(a.code ?? ""), alpha: typeof a.alpha === "number" ? numberText(a.alpha) : "", dipdir: typeof a.dipdir === "number" ? numberText(a.dipdir) : "" })));
  }
  if (src === "mesh") Object.assign(out, { wireframe_ids: Array.isArray(main.wireframe_ids) ? main.wireframe_ids.join(", ") : undefined, d_max: main.d_max, r_n: main.r_n });
  if (src === "core") Object.assign(out, { core_table: main.table, core_alpha: main.alpha, core_beta: main.beta, core_line: main.line, d_core: main.d_core });
  if (next && next.source === "global" && src !== "global") Object.assign(out, { fallback_alpha: next.alpha, fallback_dipdir: next.dipdir });
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined && v !== null));
}

// ---- the whole body ----

export interface Body {
  body: Record<string, unknown> | null;
  errors: Record<string, string>;
  reason: string | null;
}

/**
 * The body of `POST /intercepts/tt` (or `/run`, with `RUN_TOOL`): `{set_id, table, [field], orientation, …params}`.
 * The source first, then the parameters, then the block; the first error is the reason.
 */
export function interceptBody(tool: ToolSpec, src: { setId: number | null; table: string | null; field?: string | null }, values: FormValues, orient: OrientationState): Body {
  const { body, errors } = checkParams(tool.params, values);
  const o = checkOrientation(orient);
  const all = { ...errors, ...o.errors };
  const first = Object.values(errors)[0] ?? o.reason;
  if (src.setId === null) return { body: null, errors: all, reason: "Выберите набор скважин" };
  if (!src.table) return { body: null, errors: all, reason: "Выберите таблицу" };
  if (tool.source.fields === "one" && !src.field) return { body: null, errors: all, reason: "Выберите поле содержания" };
  if (first || !o.orientation) return { body: null, errors: all, reason: first ?? "Задайте ориентировку" };
  if (tool.id === "tt" && !o.orientation.length) return { body: null, errors: all, reason: "Задайте ориентировку" };
  const c = body.cutoff;
  const c2 = body.include_cutoff;
  if (typeof c === "number" && typeof c2 === "number" && c2 <= c) return { body: null, errors: { ...all, include_cutoff: "«Борт „в том числе“ c2»: должен быть больше c" }, reason: "«Борт „в том числе“ c2»: должен быть больше c" };
  const out: Record<string, unknown> = { set_id: src.setId, table: src.table, ...(tool.source.fields === "one" ? { field: src.field } : {}), ...body };
  if (o.orientation.length) out.orientation = o.orientation;
  return { body: out, errors: all, reason: null };
}
