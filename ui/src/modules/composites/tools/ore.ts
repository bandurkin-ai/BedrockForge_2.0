/**
 * «Рудные интервалы (ГКЗ)» (Д-26, ТЗ 7.35.4; ADR 0018 §8–11, решения 19–22): кондиционные интервалы по Сб, Мрт,
 * Мпп, МС (авто = Сб·Мрт), Смин (пусто — = Сб) — a new table «Рудные интервалы» and the fields ORE_ID, ORE_CLS of
 * the samples. Here: the parameters and the body of the job (`thickness {source, field?}`, `contacts_values`),
 * the answer of `POST /composites/ore/preview` and of `GET /composites/results/{id}/protocol?hole=` made safe,
 * the Russian words of the protocol, the protocol in the form И1, сл. 17 (samples as rows; N / IN₀, C₁…, IN₁…,
 * прослои, сближенные, итог), the summary by class, the body of the export. Pure except the three fetchers.
 */
import { api } from "../../../shell/api";
import { assignLanes, CLASS_STYLE, laneCount, oreClass, type OreClass } from "../../../charts/column/classes";
import type { IntervalColumns, Started } from "../api";
import { parseNumber, type Choice, type FormValues, type ParamSpec, type ToolSpec } from "../params";

export const ORE_MODES: readonly Choice[] = [
  { value: "strict_excl", label: "строгий, исключить пустую породу" },
  { value: "strict", label: "строгий" },
  { value: "soft", label: "смягчённый" },
  { value: "simple", label: "упрощённый" },
  { value: "contacts", label: "по контактам" },
];

/** Thickness, gaps and mode — shared with «Анализ кондиций». */
export const ORE_COMMON: readonly ParamSpec[] = [
  { key: "mode", label: "Режим", kind: "choice", default: "strict_excl", choices: ORE_MODES },
  { key: "contacts_values", label: "Коды тела", kind: "text", optional: true, hint: "значения домена или имена тел через «,» (QZ, 2); пусто — все непустые", when: (v) => v.mode === "contacts" },
  { key: "thickness_field", label: "Мощность пробы", kind: "field", numeric: true, optional: true, hint: "пусто — длина по стволу" },
  {
    key: "missing",
    label: "Пропуски",
    kind: "choice",
    default: "break",
    choices: [
      { value: "break", label: "разрывают интервал" },
      { value: "waste", label: "пустая порода до длины" },
    ],
  },
  { key: "missing_max", label: "Наибольший пропуск", kind: "number", unit: "м", min: 0, optional: true, when: (v) => v.missing === "waste" },
];

/**
 * The last step of the body (ТЗ 7.35.4 `composite.ore_intervals`): `thickness_field` → `thickness {source, field?}`,
 * «Коды тела» → `contacts_values`: the values of the domain as written (text, names of bodies, numbers), strings
 * without repeats — the core matches them against the domain (a numeric field by value); «по контактам» needs a
 * domain. A string — why not.
 */
export function oreFinish(body: Record<string, unknown>): Record<string, unknown> | string {
  const out: Record<string, unknown> = { ...body };
  const field = typeof out.thickness_field === "string" ? out.thickness_field.trim() : "";
  delete out.thickness_field;
  out.thickness = field ? { source: "field", field } : { source: "length" };
  const codes = typeof out.contacts_values === "string" ? out.contacts_values.trim() : "";
  delete out.contacts_values;
  if (out.mode === "contacts") {
    if (!out.domain) return "Режим «по контактам»: выберите домен — поле таблицы или каркасы";
    const list = [...new Set(codes.split(/[,;]/).map((c) => c.trim()).filter(Boolean))];
    if (list.length) out.contacts_values = list;
  }
  return out;
}

export const ore: ToolSpec = {
  id: "ore",
  command: "composite.ore_intervals",
  title: "Рудные интервалы (ГКЗ)",
  endpoint: "/ore",
  preview: () => "ore",
  source: { table: "one", fields: "one", domain: true },
  params: [
    { key: "cutoff", label: "Бортовое содержание Сб", kind: "number", min: 0 },
    { key: "m_min", label: "Мин. мощность Мрт", kind: "number", unit: "м", min: 0 },
    { key: "m_waste_max", label: "Макс. мощность прослоя Мпп", kind: "number", unit: "м", min: 0 },
    { key: "mc_min", label: "Мин. метропроцент МС", kind: "number", min: 0, optional: true, hint: "пусто — Сб·Мрт" },
    { key: "c_min", label: "Мин. содержание Смин", kind: "number", min: 0, optional: true, hint: "пусто — выключено (= Сб)" },
    ...ORE_COMMON,
    { key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «Рудные интервалы»" },
  ],
  required: ["set_id", "table", "field", "cutoff", "m_min", "m_waste_max"],
  writes: "table",
  note: "Методика ГКЗ (Д-26): равенство — руда; пропуск не превращается в ноль.",
  finish: oreFinish,
};

export const DEFAULT_TABLE = "Рудные интервалы";

/** «МС = Сб·Мрт» and «Смин = Сб» of the form: the placeholders of the empty fields. */
export function autoValues(values: FormValues): { mc_min: string; c_min: string } {
  const cb = typeof values.cutoff === "string" ? parseNumber(values.cutoff) : null;
  const mr = typeof values.m_min === "string" ? parseNumber(values.m_min) : null;
  return {
    mc_min: cb !== null && mr !== null ? `авто: ${num(cb * mr, 6)}` : "авто: Сб·Мрт",
    c_min: cb !== null ? `выключено: ${num(cb, 6)}` : "выключено (= Сб)",
  };
}

// ---- numbers ----

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** 2.8375 → «2,84» (fixed `digits`; at most when `fixed` is false); missing — «—». */
export function num(v: unknown, digits: number, fixed = false): string {
  if (!finite(v)) return "—";
  return v.toLocaleString("ru-RU", { minimumFractionDigits: fixed ? digits : 0, maximumFractionDigits: digits, useGrouping: false });
}

/** «8,0 м @ 2,84 г/т (МС 22,7)» — as the core writes LABEL (решение 20: M and MC — 1 decimal, C — 2). */
export function oreLabel(m: number, c: number, mc: number, unit: string | null): string {
  return `${num(m, 1, true)} м @ ${`${num(c, 2, true)} ${unit ?? ""}`.trim()} (МС ${num(mc, 1, true)})`;
}

// ---- the answers ----

/** An event of the protocol (ADR 0018 §9; `OreEvents.records` of the core); rows — samples of this hole, 0-based. */
export interface OreEvent {
  line: number;
  step: number;
  stage: number;
  kind: string;
  side: string;
  ore: boolean;
  compared: string;
  decision: string;
  row0: number;
  row1: number;
  from: number | null;
  to: number | null;
  m: number | null;
  c: number | null;
  mc: number | null;
}

export interface OreClassSum {
  cls: OreClass;
  nested: boolean;
  count: number;
  m: number | null;
  m_mean: number | null;
  c: number | null;
  mc: number | null;
  domain?: number;
}

/** The summary of Д-26 «Результат» (5): by class and by (domain, class); `length` — of the sampling lines. */
export interface OreSummary {
  classes: OreClassSum[];
  domains: OreClassSum[];
  length: number | null;
  rows: number;
}

export interface OreAnswer {
  hole: string;
  /** the unit of the grade field («г/т»), null — not known */
  unit: string | null;
  /** samples: the grade field, ORE_CLS, ORE_ID */
  samples: IntervalColumns;
  /** rows of «Рудные интервалы»: CLASS, M, C, MC, BASIS, NESTED, ORE_ID, N_SAMPLES, WASTE_LEN, FLAG, LABEL */
  result: IntervalColumns;
  events: OreEvent[];
  summary: OreSummary | null;
  set_summary: OreSummary | null;
  notes: string[];
}

const nums = (v: unknown): number | null => (finite(v) ? v : null);
const int = (v: unknown, d = 0): number => (typeof v === "number" && Number.isInteger(v) ? v : d);

function columns(raw: unknown): IntervalColumns {
  const r = (raw ?? {}) as Partial<IntervalColumns>;
  return { from: Array.isArray(r.from) ? r.from : [], to: Array.isArray(r.to) ? r.to : [], values: r.values && typeof r.values === "object" ? r.values : {} };
}

function event(raw: unknown): OreEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const e = raw as Record<string, unknown>;
  const row0 = int(e.row0, -1);
  const row1 = int(e.row1, -1);
  return {
    line: int(e.line),
    step: int(e.step, -1),
    stage: int(e.stage),
    kind: String(e.kind ?? ""),
    side: String(e.side ?? ""),
    ore: e.ore === true || e.ore === 1,
    compared: String(e.compared ?? ""),
    decision: String(e.decision ?? ""),
    row0,
    row1: row1 < row0 ? row0 : row1,
    from: nums(e.from),
    to: nums(e.to),
    m: nums(e.m),
    c: nums(e.c),
    mc: nums(e.mc),
  };
}

function classSum(raw: unknown): OreClassSum | null {
  const e = (raw ?? {}) as Record<string, unknown>;
  const cls = oreClass(e.cls);
  if (!cls) return null;
  return { cls, nested: e.nested === true, count: int(e.count), m: nums(e.m), m_mean: nums(e.m_mean), c: nums(e.c), mc: nums(e.mc), ...(typeof e.domain === "number" ? { domain: e.domain } : {}) };
}

export function toSummary(raw: unknown): OreSummary | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const list = (v: unknown) => (Array.isArray(v) ? v.map(classSum).filter((x): x is OreClassSum => x !== null) : []);
  return { classes: list(r.classes), domains: list(r.domains), length: nums(r.length), rows: int(r.rows) };
}

/** The answer of the preview or of the protocol of a result made safe to draw (a part left out is empty). */
export function toOreAnswer(raw: Record<string, unknown>): OreAnswer {
  const p = raw.protocol as unknown;
  const list = Array.isArray(p) ? p : p && typeof p === "object" && Array.isArray((p as { events?: unknown }).events) ? (p as { events: unknown[] }).events : [];
  return {
    hole: String(raw.hole ?? ""),
    unit: typeof raw.unit === "string" && raw.unit.trim() ? raw.unit.trim() : null,
    samples: columns(raw.samples),
    result: columns(raw.result),
    events: list.map(event).filter((e): e is OreEvent => e !== null),
    summary: toSummary(raw.summary),
    set_summary: toSummary(raw.set_summary),
    notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [],
  };
}

export async function fetchOrePreview(params: Record<string, unknown>, holeId: number): Promise<OreAnswer> {
  return toOreAnswer(await api.post<Record<string, unknown>>("/composites/ore/preview", { params, hole_id: holeId }));
}

/** The protocol of a fresh result for one hole (by name); 409 `stale` — «Результат устарел» (решение 19). */
export async function fetchProtocol(resultId: number, hole: string): Promise<OreAnswer> {
  return toOreAnswer(await api.get<Record<string, unknown>>(`/composites/results/${resultId}/protocol?hole=${encodeURIComponent(hole)}`));
}

export type ProtocolFormat = "xlsx" | "csv";

/** «Рудные интервалы · протокол.xlsx» — the name the dialog «Сохранить как…» offers. */
export const protocolFileName = (name: string, format: ProtocolFormat): string => `${(name.trim() || DEFAULT_TABLE).replace(/[\\/:*?"<>|]+/g, "_")} · протокол.${format}`;

export const MAX_SHEET_HOLES = 50;

/** The body of `POST /composites/results/{id}/protocol`: the file, the format, the holes of the sheets «Скв. ‹имя›». */
export function protocolExportBody(format: ProtocolFormat, path: string, holes: readonly string[], overwrite: boolean): Record<string, unknown> | string {
  if (!path.trim()) return "Выберите файл: «Сохранить как…».";
  const uniq = [...new Set(holes.map((h) => h.trim()).filter(Boolean))];
  if (uniq.length > MAX_SHEET_HOLES) return `Листов скважин ${uniq.length}: не больше ${MAX_SHEET_HOLES}`;
  return { format, path, ...(uniq.length ? { holes: uniq } : {}), ...(overwrite ? { overwrite: true } : {}) };
}

export const startProtocolExport = (resultId: number, body: Record<string, unknown>): Promise<Started> => api.post<Started>(`/composites/results/${resultId}/protocol`, body);

// ---- the words of the protocol ----

export const STEP_TITLES: Readonly<Record<number, string>> = {
  0: "разрыв линии",
  1: "элементарные интервалы",
  2: "прирезка",
  3: "пустой прослой",
  4: "сближенные интервалы",
  5: "класс интервала",
  6: "ручная правка",
};

export const DECISION_TITLES: Readonly<Record<string, string>> = {
  "": "",
  pass: "C ≥ Сб",
  joined: "присоединён",
  rejected_c: "отклонён — C < Сб",
  rejected_m: "отклонён — M > Мпп",
  demoted: "некондиционный → безрудный",
  interlayer: "прослой исключён",
  torn: "разорван",
  near_joined: "сближенные объединены",
  not_checked: "не проверяется в этом режиме",
  break: "линия опробования прервана",
  missing_waste: "пропуск — пустая порода (флаг)",
  ore: "кондиционный",
  subgrade: "некондиционный",
  not_selected: "не выбран",
};

export const COMPARED_TITLES: Readonly<Record<string, string>> = {
  cutoff: "Сб",
  m_waste_max: "Мпп",
  m_min: "Мрт",
  mc_min: "МС",
  c_min: "Смин",
  k: "условие К",
  layer: "прослой",
  no_value: "нет значения",
  gap: "промежуток без проб",
  domain: "граница домена",
  missing_max: "наибольший пропуск",
};

const SIDE_TITLES: Readonly<Record<string, string>> = { upper: "висячий бок", lower: "лежачий бок" };

const SUB = "₀₁₂₃₄₅₆₇₈₉";
export const sub = (n: number): string => String(n).replace(/\d/g, (d) => SUB[Number(d)]);

/** One line of the long form: «Этап 1 · прирезка · висячий бок · пробы 4–9 · 3–9 м · M 6,0 · C 2,97 · MC 17,8 — C ≥ Сб». */
export function eventText(e: OreEvent): string {
  const parts = [e.step === 2 ? `Этап ${e.stage}` : STEP_TITLES[e.step] ?? `шаг ${e.step}`];
  if (e.step === 2) parts.push(e.kind === "trim" ? "прирезка" : e.kind === "gap" ? "безрудный промежуток" : "единица");
  if (SIDE_TITLES[e.side]) parts.push(SIDE_TITLES[e.side]);
  if (e.row0 >= 0) parts.push(e.row1 > e.row0 ? `пробы ${e.row0 + 1}–${e.row1 + 1}` : `проба ${e.row0 + 1}`);
  if (e.from !== null && e.to !== null) parts.push(`${num(e.from, 2)}–${num(e.to, 2)} м`);
  if (e.m !== null) parts.push(`M ${num(e.m, 2)}`);
  if (e.c !== null) parts.push(`C ${num(e.c, 3)}`);
  if (e.mc !== null) parts.push(`MC ${num(e.mc, 2)}`);
  const what = DECISION_TITLES[e.decision] ?? e.decision;
  const cmp = COMPARED_TITLES[e.compared];
  const tail = [what, cmp && !what.includes(cmp) ? `(${cmp})` : ""].filter(Boolean).join(" ");
  return tail ? `${parts.join(" · ")} — ${tail}` : parts.join(" · ");
}

// ---- the protocol in the form И1, сл. 17 ----

export type Tone = OreClass | "pass" | "reject" | "none";

export interface ProtocolCell {
  text: string;
  title: string;
  tone: Tone;
  rowSpan: number;
}

export interface ProtocolHead {
  key: string;
  title: string;
  hint: string;
  lanes: number;
}

/** Rows — samples; a cell is a span of samples, `covered` — under a span above, null — empty. */
export interface ProtocolGrid {
  heads: ProtocolHead[];
  rows: (ProtocolCell | "covered" | null)[][];
}

interface Entry {
  e: OreEvent;
  text: string;
  tone: Tone;
}

const unitText = (e: OreEvent): string => `${e.ore ? "Р" : "Б"} ${num(e.m, 2)} м · ${num(e.c, 2)}`;

function entryOf(e: OreEvent, unit: string | null): Entry {
  if (e.kind === "unit") return { e, text: unitText(e), tone: e.ore ? "ore" : e.decision === "demoted" ? "subgrade" : "waste" };
  if (e.kind === "trim") return { e, text: `${e.side === "lower" ? "↓" : "↑"} ${num(e.c, 2)} ${e.decision === "pass" ? "✓" : "✗"}`, tone: e.decision === "pass" ? "pass" : "reject" };
  if (e.kind === "gap") return { e, text: `M ${num(e.m, 2)} > Мпп`, tone: "reject" };
  if (e.kind === "layer") return { e, text: `${num(e.m, 2)} м · ${num(e.c, 2)} — ${DECISION_TITLES[e.decision] ?? e.decision}`, tone: e.decision === "interlayer" ? "interlayer" : "none" };
  if (e.kind === "chain") return { e, text: `${num(e.m, 2)} м · ${num(e.c, 2)} — ${DECISION_TITLES[e.decision] ?? e.decision}`, tone: e.decision === "near_joined" ? "pass" : "none" };
  if (e.kind === "interval") {
    const isOre = e.decision === "ore";
    const text = isOre && e.m !== null && e.c !== null && e.mc !== null ? oreLabel(e.m, e.c, e.mc, unit) : `${CLASS_STYLE.subgrade.short} ${num(e.m, 2)} м · ${num(e.c, 2)}`;
    return { e, text, tone: isOre ? "ore" : "subgrade" };
  }
  return { e, text: DECISION_TITLES[e.decision] ?? e.decision, tone: "none" };
}

/** The columns of the form: N / IN₀; per stage C_s (прирезки и промежутки) and IN_s; прослои; сближенные; итог. */
function groups(events: readonly OreEvent[]): { key: string; title: string; hint: string; pick: (e: OreEvent) => boolean }[] {
  const stages = [...new Set(events.filter((e) => e.step === 2).map((e) => e.stage))].sort((a, b) => a - b);
  const out = [{ key: "in0", title: `N / IN${sub(0)}`, hint: "элементарные интервалы: Р — рудный, Б — безрудный (M, C)", pick: (e: OreEvent) => e.step === 1 && e.kind === "unit" }];
  for (const s of stages) {
    out.push({ key: `c${s}`, title: `C${sub(s)}`, hint: `этап ${s}: прирезки (↑ висячий, ↓ лежачий бок; C, ✓ — C ≥ Сб) и промежутки > Мпп`, pick: (e) => e.step === 2 && e.stage === s && (e.kind === "trim" || e.kind === "gap") });
    out.push({ key: `in${s}`, title: `IN${sub(s)}`, hint: `единицы после этапа ${s}`, pick: (e) => e.step === 2 && e.stage === s && e.kind === "unit" });
  }
  if (events.some((e) => e.step === 3)) out.push({ key: "layer", title: "Прослои", hint: "шаг 3: фрагменты «порода … порода» с C < Сб и M > Мпп", pick: (e) => e.step === 3 });
  if (events.some((e) => e.step === 4)) out.push({ key: "near", title: "Сближенные", hint: "шаг 4: цепочки сближенных интервалов", pick: (e) => e.step === 4 });
  out.push({ key: "final", title: "Итог", hint: "шаг 5: кондиционные и некондиционные интервалы", pick: (e) => e.step === 5 || e.step === 6 });
  return out;
}

/** The protocol of one hole as a grid of samples × columns (lanes where spans overlap). */
export function protocolGrid(events: readonly OreEvent[], samples: number, unit: string | null = null): ProtocolGrid {
  const usable = events.filter((e) => e.row0 >= 0 && e.row0 < samples);
  const heads: ProtocolHead[] = [];
  const cols: (ProtocolCell | "covered" | null)[][] = []; // column-major: one per lane
  for (const g of groups(usable)) {
    const entries = usable.filter(g.pick).map((e) => entryOf(e, unit));
    const lanes = assignLanes(entries.map((x) => ({ row0: x.e.row0, row1: Math.min(samples - 1, x.e.row1) })));
    const n = Math.max(1, laneCount(lanes));
    heads.push({ key: g.key, title: g.title, hint: g.hint, lanes: n });
    const block = Array.from({ length: n }, () => new Array<ProtocolCell | "covered" | null>(samples).fill(null));
    entries.forEach((x, i) => {
      const r1 = Math.min(samples - 1, x.e.row1);
      block[lanes[i]][x.e.row0] = { text: x.text, title: eventText(x.e), tone: x.tone, rowSpan: r1 - x.e.row0 + 1 };
      for (let r = x.e.row0 + 1; r <= r1; r++) block[lanes[i]][r] = "covered";
    });
    cols.push(...block);
  }
  const rows = Array.from({ length: samples }, (_, r) => cols.map((c) => c[r]));
  return { heads, rows };
}

// ---- the summary ----

export interface SummaryLine {
  key: string;
  title: string;
  count: string;
  m: string;
  mMean: string;
  c: string;
  mc: string;
}

const DOMAIN_TITLE = (d: number | undefined) => (d === undefined ? "" : d < 0 ? "вне доменов · " : `домен ${d} · `);

/** Rows of the tab «Сводка»: number, Σ M, средняя M, C = Σ MC / Σ M, Σ MC — by class (and by domain). */
export function summaryLines(s: OreSummary | null, byDomain = false): SummaryLine[] {
  if (!s) return [];
  const list = byDomain && s.domains.length ? s.domains : s.classes;
  return list.map((x) => ({
    key: `${x.domain ?? ""}:${x.cls}`,
    title: `${byDomain ? DOMAIN_TITLE(x.domain) : ""}${CLASS_STYLE[x.cls].title}${x.nested ? " (внутри интервалов)" : ""}`,
    count: String(x.count),
    m: num(x.m, 2),
    mMean: num(x.m_mean, 2),
    c: num(x.c, 3),
    mc: num(x.mc, 2),
  }));
}
