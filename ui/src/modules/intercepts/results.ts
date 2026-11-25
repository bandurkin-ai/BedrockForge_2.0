/**
 * The results `tt` («Истинная мощность · ‹Таблица›») and `intercepts` («Рудные пересечения») in the rows «Обработка
 * проб» of a set (ADR 0021 §8, §11; decisions 15, 17, 19): the list, «устарел», «Перестроить» and «Удалить» are those
 * of Э12 (`GET /composites/results` shows them, `composite.rebuild` goes to `/intercepts` by the operation); here —
 * what differs: «Параметры…» opens the window of the tool with the values the core ran with (`params` — the
 * canonical body: tables as ids, `orientation` as a list), and «Свойства» names the parameters as the windows do and
 * reads the orientation and the summary in Russian. Pure: no React.
 */
import type { ResultRow } from "../composites/api";
import { numberText, type ParamSpec } from "../composites/params";
import { resultMenu, sourceTable, type Fact, type ResultMenuItem } from "../composites/results";
import { FLAG_BITS, toCounts } from "./api";
import { argsFromOrientation, RUN_TOOL, SOURCE_TITLES, TT_TOOL, type OrientSource } from "./params";
import { interceptToolOfOp, isInterceptOp } from "./tools";

/** The keys of `params` that are not arguments of a command (or are given apart). */
const SKIP = new Set(["set_id", "replace", "orientation"]);

/**
 * «Параметры…»: the arguments of `intercept.tt` / `intercept.run` from `params` — the flat orientation
 * (`argsFromOrientation`), `fields` as the list (`[]` — only the grade field), the table by its key.
 */
export function interceptArgs(r: ResultRow): Record<string, unknown> {
  const p = r.params ?? {};
  const args: Record<string, unknown> = { set_id: r.set_id };
  for (const [k, v] of Object.entries(p)) {
    if (SKIP.has(k) || v === null || v === undefined) continue;
    if (k === "fields") args.fields = Array.isArray(v) ? v.map(String) : v;
    else if (typeof v !== "object") args[k] = v;
  }
  Object.assign(args, argsFromOrientation(p.orientation));
  if (args.table === undefined) {
    const t = sourceTable(r);
    if (t) args.table = t;
  }
  return args;
}

/** The menu of a result of Э15: that of Э12, with «Параметры…» opening the window of the tool. */
export function interceptMenu(r: ResultRow, readOnly: boolean): ResultMenuItem[] {
  const items = resultMenu(r, readOnly);
  if (!isInterceptOp(r.op)) return items;
  const ro = readOnly ? "Проект открыт только для чтения" : null;
  return items.map((it) => {
    if (it.action === "params") return { ...it, reason: ro };
    if (it.action === "open" && r.op === "tt") return { ...it, label: "Открыть таблицу с полями" };
    return it;
  });
}

// ---- «Свойства» ----

const num = (v: unknown, unit = ""): string => (typeof v === "number" && Number.isFinite(v) ? `${numberText(v)}${unit}` : "—");
const quoted = (v: unknown): string => (typeof v === "string" && v ? `«${v}»` : "—");
const ids = (v: unknown): string => (Array.isArray(v) && v.length ? v.join(", ") : "—");

/** One source of `orientation` in Russian: «Глобальная: α 30°, Aп 120°». */
export function sourceText(s: unknown): string {
  if (!s || typeof s !== "object") return "—";
  const o = s as Record<string, unknown>;
  const src = String(o.source ?? "");
  const title = SOURCE_TITLES[src as OrientSource] ?? src;
  if (src === "global") return `${title}: α ${num(o.alpha, "°")}, Aп ${num(o.dipdir, "°")}`;
  if (src === "domain") {
    const n = Array.isArray(o.attitudes) ? o.attitudes.length : 0;
    const by = Array.isArray(o.wireframe_ids) ? `каркасы-домены ${ids(o.wireframe_ids)}` : `поле ${quoted(o.field)}`;
    return `${title}: ${by}; кодов ${n}`;
  }
  if (src === "mesh") return `${title}: каркасы ${ids(o.wireframe_ids)}; d_max ${num(o.d_max, " м")}, r_n ${num(o.r_n, " м")}`;
  if (src === "trend") return `${title}: тренд ${quoted(o.trend)}${typeof o.trend_id === "number" ? ` (№${o.trend_id})` : ""}`;
  if (src === "core") return `${title}: α_к ${quoted(o.alpha)}, β_к ${quoted(o.beta)}, линия ${o.line === "toh" ? "TOH" : "BOH"}, d_к ${num(o.d_core, " м")}`;
  return title || "—";
}

/** «код α/Aп» of the table of domains, for the facts (a long table — the first 20 and «ещё N»). */
function attitudesText(s: unknown): string | null {
  const list = (s as { attitudes?: unknown } | null)?.attitudes;
  if (!Array.isArray(list) || !list.length) return null;
  const head = (list as Record<string, unknown>[]).slice(0, 20).map((a) => `${String(a.code ?? "")} ${num(a.alpha)}/${num(a.dipdir)}`);
  return `${head.join("; ")}${list.length > 20 ? `; ещё ${list.length - 20}` : ""}`;
}

/** The facts of `orientation`: the main source, «если не найдена», the codes; empty — «нет (TT пусто)». */
export function orientationFacts(list: unknown): Fact[] {
  if (!Array.isArray(list) || !list.length) return [{ label: "Ориентировка", value: "нет (TT пусто, TT_SRC «нет»)" }];
  const out: Fact[] = [];
  list.forEach((s, i) => {
    out.push({ label: i === 0 ? "Ориентировка" : "Если не найдена", value: sourceText(s) });
    const codes = attitudesText(s);
    if (codes) out.push({ label: "Коды домена, α/Aп", value: codes });
  });
  return out;
}

function choiceText(p: ParamSpec | undefined, v: unknown): string | null {
  const c = p?.choices?.find((x) => x.value === v);
  return c ? c.label : null;
}

function paramValue(p: ParamSpec | undefined, key: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return key === "include_cutoff" ? "нет «в том числе»" : key === "density" ? "без плотности" : "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "number") return numberText(v);
  if (Array.isArray(v)) return v.length ? v.map(String).join(", ") : "—";
  return choiceText(p, v) ?? String(v);
}

/** The order of the facts of the parameters (the windows' order); the others — after, by their keys. */
const ORDER = ["table", "field", "cutoff", "include_cutoff", "l_min", "min_by", "waste_max", "missing", "density", "fields", "eps", "step", "angles", "name"];

/** The parameters of a result of Э15 as the window names them; the orientation in Russian. */
export function interceptParamFacts(r: ResultRow): Fact[] {
  const tool = interceptToolOfOp(r.op) ?? TT_TOOL;
  const p = r.params ?? {};
  const spec = (k: string) => tool.params.find((x) => x.key === k) ?? RUN_TOOL.params.find((x) => x.key === k);
  const label = (k: string): string => {
    if (k === "table") return "Таблица";
    if (k === "field") return "Поле содержания";
    if (k === "fields") return "Прочие содержания";
    if (k === "l_min" && p.min_by === "tt") return "Мин. истинная мощность m_min, м";
    const s = spec(k);
    return s ? `${s.label}${s.unit ? `, ${s.unit}` : ""}` : k;
  };
  const keys = [...ORDER.filter((k) => k in p), ...Object.keys(p).filter((k) => !ORDER.includes(k) && !SKIP.has(k))];
  const out: Fact[] = [];
  let oriented = false;
  for (const k of keys) {
    // the block «Ориентировка» stands before ε_в and the step, as in the windows
    if (!oriented && (k === "eps" || k === "step")) {
      out.push(...orientationFacts(p.orientation));
      oriented = true;
    }
    if (k === "table") out.push({ label: "Таблица", value: sourceTable(r) ?? paramValue(undefined, k, p[k]) });
    else if (k === "fields") out.push({ label: label(k), value: Array.isArray(p.fields) && !p.fields.length ? "только поле содержания" : paramValue(undefined, k, p.fields) });
    else out.push({ label: label(k), value: paramValue(spec(k), k, p[k]) });
  }
  if (!oriented) out.push(...orientationFacts(p.orientation));
  return out;
}

/** The names of the keys of `report` (e15-api.md, 15.11–15.12); the same key of `tt` and `intercepts` may differ. */
const REPORT_LABELS: Record<string, string> = {
  rows: "Строк",
  intercepts: "Пересечений",
  included: "«В том числе»",
  length: "Σ LENGTH, м",
  sum_length: "Σ LENGTH, м",
  tt: "Σ TT, м",
  sum_tt: "Σ TT, м",
  with_tt: "Строк с TT",
  len_g: "Σ LEN_G",
  sum_len_g: "Σ LEN_G",
  mean: "Среднее содержание",
  waste_len: "Σ WASTE_LEN, м",
  miss_len: "Σ MISS_LEN, м",
  excluded: "Вне расчёта (нет глубины, TO ≤ FROM)",
  skipped: "Без FROM или TO (не прочитаны, поля пусты)",
  capped: "Интервалов с увеличенной частью ствола",
  unit: "Единица содержания",
  field: "Поле содержания",
};

/** `intercepts`: Σ by the main intercepts; the samples out of the rule (overlaps too). */
const RUN_LABELS: Record<string, string> = {
  length: "Σ LENGTH основных, м",
  tt: "Σ TT основных, м",
  excluded: "Проб вне правила (нет глубины, TO ≤ FROM, перекрытия)",
  skipped: "Проб без FROM или TO (не прочитаны)",
};

/** Keys of the report not shown as facts (the columns, the time and versions — in «Результат»). */
const REPORT_SKIP = new Set(["written", "table_columns", "seconds", "versions", "edits", "summary", "counts", "src", "flags", "journal", "absent", "sources"]);

const sourceTitle = (s: unknown): string => SOURCE_TITLES[String(s) as OrientSource] ?? String(s);

/** The report of the source «тренд» (`report.sources[k]`: {source, rows, trend, no_position, nearest}): the trend, the rows without a position
 * (no trajectory, no depth — TT_FLAG has the bit), the rows that took the nearest measurement (a structural trend with no measurement in R_тр). */
export function trendFacts(o: Record<string, unknown>): Fact[] {
  const out: Fact[] = [];
  if (typeof o.trend === "string" && o.trend) out.push({ label: "Источник «тренд»: тренд", value: o.trend });
  if (typeof o.no_position === "number") out.push({ label: "Источник «тренд»: строк без положения", value: num(o.no_position) });
  if (typeof o.nearest === "number") out.push({ label: "Источник «тренд»: взят ближайший замер (нет замеров в R_тр)", value: num(o.nearest) });
  return out;
}

/**
 * The summary of a result of Э15: rows by TT_SRC and by the bits of TT_FLAG (`report.counts {src, flags}` or the
 * same keys at the top — the form of the preview), then the numbers of the report with Russian names, the rows by
 * the source of the chain (`sources`) and the kinds of sources that were not there (`absent`); an empty value stays
 * «—», never 0.
 */
export function interceptReportFacts(r: ResultRow): Fact[] {
  const rep = r.report ?? {};
  const run = r.op === "intercepts";
  const unit = typeof rep.unit === "string" && rep.unit ? rep.unit : null;
  const label = (k: string): string => {
    const base = (run ? RUN_LABELS[k] : undefined) ?? REPORT_LABELS[k] ?? k;
    return k === "mean" && unit ? `${base}, ${unit}` : base;
  };
  const out: Fact[] = [];
  const counts = toCounts(rep.counts ?? rep);
  if (counts) {
    for (const [src, n] of Object.entries(counts.bySrc)) out.push({ label: `TT_SRC «${src}»`, value: numberText(n) });
    for (const b of FLAG_BITS) if (counts.byFlag[b.bit] !== undefined) out.push({ label: `TT_FLAG ${b.bit} — ${b.title}`, value: numberText(counts.byFlag[b.bit]) });
  }
  for (const [k, v] of Object.entries(rep)) {
    if (REPORT_SKIP.has(k)) continue;
    if (v === null) out.push({ label: label(k), value: "—" });
    else if (typeof v === "number" || typeof v === "string") out.push({ label: label(k), value: typeof v === "number" ? numberText(v) : v });
  }
  if (Array.isArray(rep.sources))
    for (const s of rep.sources as unknown[]) {
      const o = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
      if (o.source !== undefined) out.push({ label: `Строк по источнику «${sourceTitle(o.source)}»`, value: num(o.rows) });
      if (o.source === "trend") out.push(...trendFacts(o));
    }
  if (Array.isArray(rep.absent) && rep.absent.length) out.push({ label: "Источников не было", value: rep.absent.map(sourceTitle).join(", ") });
  return out;
}
