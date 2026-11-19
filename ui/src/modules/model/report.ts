/**
 * Reports of the operations and the record of an operation in «Свойства» as data (ТЗ 7.23; ADR 0015 §6
 * «Запись операции»). A report: «Исправить» — «было → стало» of the checks of the wireframe and what every step
 * changed; «Упростить», «Сгладить» — the volume before and after, its change and the greatest deviation;
 * «Объём» — cut, fill, their difference and the area; the rest — the numbers the operation gave. The record
 * (`objects.meta.built`): the operation, when, the sources (deleted ones keep their name), the parameters as the
 * window names them, the tolerances. A number that is not known is «—», never 0. Pure: no React.
 */
import { num, type Fact } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { checkedText } from "../wireframes/checks";
import type { OpField, OpSpec } from "./opForms";

type Rec = Record<string, unknown>;
const known = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isRec = (v: unknown): v is Rec => !!v && typeof v === "object" && !Array.isArray(v);

const SUP: Record<string, string> = { "-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹" };

/** A length or a tolerance: «0,25» · «1,5·10⁻⁹» for the tiny ones. */
export function small(v: number): string {
  if (v !== 0 && Math.abs(v) < 1e-3) {
    const [m, e] = v.toExponential(1).split("e");
    return `${m.replace(".", ",").replace(/,0$/, "")}·10${String(Number(e)).replace(/./g, (c) => SUP[c] ?? c)}`;
  }
  return num(v, Math.abs(v) < 10 ? 4 : 2);
}

const m3 = (v: unknown): string => (known(v) ? `${num(v, Math.abs(v) < 10 ? 4 : 2)} м³` : "—");
const m2 = (v: unknown): string => (known(v) ? `${num(v, Math.abs(v) < 10 ? 4 : 2)} м²` : "—");
const metres = (v: unknown): string => (known(v) ? `${small(v)} м` : "—");
const n = (v: unknown): string => (known(v) ? count(v) : "—");
const yes = (v: unknown): string => (v === true ? "да" : v === false ? "нет" : "—");
const closedText = (v: unknown): string => (v === true ? "замкнут" : v === false ? "не замкнут" : "—");
/** «+0,12 %» · «−3,4 %» */
export const percent = (v: unknown): string => (known(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), 2)} %` : "—");

/** One row of «было → стало». */
export interface CompareRow {
  label: string;
  before: string;
  after: string;
  changed: boolean;
}

export interface ReportView {
  /** «было → стало»; null — the operation compares nothing */
  compare: CompareRow[] | null;
  facts: Fact[];
  /** what every step of «Исправить» changed */
  steps: string[];
  warnings: string[];
}

const CHECK_ROWS: [string, string, (v: unknown) => string][] = [
  ["triangles", "Треугольников", n],
  ["vertices", "Вершин", n],
  ["closed", "Замкнутость", closedText],
  ["open_edges", "Открытых рёбер", n],
  ["nonmanifold_edges", "Рёбер с тремя и более треугольниками", n],
  ["degenerate", "Вырожденных треугольников", n],
  ["duplicate", "Повторных треугольников", n],
  ["orientation_conflicts", "Несогласованных нормалей (рёбер)", n],
  ["self_intersections", "Самопересечений", n],
  ["components", "Связных частей", n],
  ["area", "Площадь", m2],
  ["volume", "Объём", m3],
];

function row(label: string, before: unknown, after: unknown, fmt: (v: unknown) => string): CompareRow {
  const b = fmt(before);
  const a = fmt(after);
  return { label, before: b, after: a, changed: b !== a };
}

/** «Исправить»: the checks before and after; a count not measured on either side is left out. */
export function repairCompare(report: Rec): CompareRow[] {
  const before = isRec(report.before) ? report.before : {};
  const after = isRec(report.after) ? report.after : {};
  return CHECK_ROWS.filter(([k]) => k in before || k in after).map(([k, label, fmt]) => row(label, before[k], after[k], fmt));
}

/** «Сшивка совпадающих вершин: изменено 12 — …» of the steps that ran. */
export function repairSteps(report: Rec): string[] {
  const steps = Array.isArray(report.steps) ? (report.steps as Rec[]) : [];
  return steps.map((s) => `${String(s.title ?? s.step ?? "Шаг")}: ${known(s.changed) && s.changed > 0 ? `изменено ${count(s.changed)}` : "без изменений"}${s.note ? ` — ${String(s.note)}` : ""}`);
}

const METHODS: Record<string, string> = { quadric: "квадратичные метрики ошибки (pyfqmr)", manifold: "manifold3d", none: "без упрощения: цель уже достигнута" };
const methodText = (v: unknown): string | null => (typeof v !== "string" || !v ? null : /[А-Яа-яЁё]/.test(v) ? v : (METHODS[v] ?? null));

/** «Упростить», «Сгладить»: volume and triangles before → after, the change of the volume, the deviation. */
function reshapeView(op: string, r: Rec): Pick<ReportView, "compare" | "facts"> {
  const compare = [
    ...(op === "simplify" ? [row("Треугольников", r.triangles_before, r.triangles_after, n), row("Замкнутость", r.closed_before, r.closed_after, closedText)] : []),
    row("Объём", r.volume_before, r.volume_after, (v) => (v === null || v === undefined ? "нет: не замкнут" : m3(v))),
  ];
  const facts: Fact[] = [
    { label: "Изменение объёма", value: known(r.volume_change_percent) ? percent(r.volume_change_percent) : "нет: каркас не замкнут" },
    { label: "Наибольшее отклонение", value: metres(r.max_deviation) },
  ];
  if (op === "smooth" && known(r.iterations)) facts.push({ label: "Проходов", value: count(r.iterations) });
  if (op === "smooth" && known(r.scale)) facts.push({ label: "Поправка объёма (масштаб)", value: num(r.scale, 6) });
  const m = methodText(r.method);
  if (m) facts.push({ label: "Метод", value: m });
  return { compare, facts };
}

/** «Объём»: выемка, насыпь, разность, площадь. */
export function volumeFacts(r: Rec): Fact[] {
  const facts: Fact[] = [
    { label: "Выемка", value: m3(r.cut) },
    { label: "Насыпь", value: m3(r.fill) },
    { label: "Разность (выемка − насыпь)", value: known(r.net) ? `${r.net < 0 ? "−" : ""}${m3(Math.abs(r.net))}` : "—" },
    { label: "Площадь в плане", value: m2(r.area) },
  ];
  if (known(r.tolerance)) facts.push({ label: "Допуск", value: metres(r.tolerance) });
  const m = methodText(r.method);
  if (m) facts.push({ label: "Метод", value: m });
  return facts;
}

const PARTS: Record<string, string> = { positive: "по нормали", negative: "против нормали", inside: "внутри", outside: "снаружи", above: "над поверхностью", below: "под поверхностью" };

/** The numbers of the other operations, in Russian; what has no name here is not shown. */
const GENERIC: [string, string, (v: unknown) => string][] = [
  ["triangles", "Треугольников", n],
  ["volume", "Объём", m3],
  ["area", "Площадь", m2],
  ["parts", "Частей", n],
  ["kept", "Создано частей", n],
  ["contours", "Контуров", n],
  ["ties", "Тай-линий", n],
  ["ties_used", "Тай-линий использовано", (v) => (Array.isArray(v) ? count(v.length) : n(v))],
  ["polygons", "Полигонов", n],
  ["distance", "Расстояние", metres],
  ["both_sides", "В обе стороны", yes],
  ["lines", "Линий", n],
  ["length", "Длина", metres],
  ["levels", "Уровней", n],
  ["major", "Основных линий", n],
  ["interval", "Шаг", metres],
  ["base", "Отсчёт от", metres],
  ["major_every", "Основная — каждая", n],
  ["removed", "Удалено треугольников", n],
  ["added", "Добавлено треугольников", n],
  ["sum_error", "Невязка площадей частей", m2],
  ["tolerance", "Допуск", metres],
  ["section_tolerance", "Допуск разрезов", metres],
];

function genericFacts(r: Rec): Fact[] {
  const facts: Fact[] = [];
  for (const [k, label, fmt] of GENERIC) {
    if (!(k in r) || r[k] === null || r[k] === undefined) continue;
    facts.push({ label, value: fmt(r[k]) });
  }
  if (typeof r.closed === "boolean") facts.push({ label: "Замкнутость", value: closedText(r.closed) });
  else if (known(r.closed)) facts.push({ label: "Замкнутых линий", value: count(r.closed) });
  for (const [part, title] of Object.entries(PARTS)) {
    if (known(r[`triangles_${part}`])) facts.push({ label: `Треугольников ${title}`, value: count(r[`triangles_${part}`] as number) });
    if (known(r[`volume_${part}`])) facts.push({ label: `Объём ${title}`, value: m3(r[`volume_${part}`]) });
    if (known(r[`area_${part}`])) facts.push({ label: `Площадь ${title}`, value: m2(r[`area_${part}`]) });
  }
  if (Array.isArray(r.volumes) && r.volumes.length) facts.push({ label: "Объёмы исходных тел", value: r.volumes.map(m3).join("; ") });
  return facts;
}

/** The report of an operation. `warnings` — of the job (the report may hold some too). */
export function reportView(op: string, report: Rec, warnings: readonly string[] = []): ReportView {
  const own = Array.isArray(report.warnings) ? report.warnings.map(String) : typeof report.warning === "string" ? [report.warning] : [];
  const all = [...new Set([...warnings, ...own])];
  if (op === "repair") {
    const facts: Fact[] = known(report.tolerance) ? [{ label: "Допуск сшивки", value: metres(report.tolerance) }] : [];
    return { compare: repairCompare(report), facts, steps: repairSteps(report), warnings: all };
  }
  if (op === "simplify" || op === "smooth") return { ...reshapeView(op, report), steps: [], warnings: all };
  if (op === "volume_between") return { compare: null, facts: volumeFacts(report), steps: [], warnings: all };
  return { compare: null, facts: genericFacts(report), steps: [], warnings: all };
}

/** The report as text (for «Скопировать»). */
export function reportText(title: string, view: ReportView, made: readonly string[] = []): string {
  const lines = [title];
  if (made.length) lines.push(`Создано: ${made.join(", ")}`);
  if (view.compare?.length) lines.push("Было → стало:", ...view.compare.map((r) => `  ${r.label}: ${r.before} → ${r.after}`));
  for (const f of view.facts) lines.push(`${f.label}: ${f.value}`);
  if (view.steps.length) lines.push("Шаги:", ...view.steps.map((s) => `  ${s}`));
  if (view.warnings.length) lines.push("Предупреждения:", ...view.warnings.map((w) => `  ${w}`));
  return lines.join("\n");
}

// ---------------------------------------------------------------- the record of an operation

/** A source as the record keeps it; `id` null — deleted from the project. */
export interface BuiltSource {
  role?: string;
  kind?: string;
  id?: number | null;
  name?: string;
  version?: number;
  lines?: number[] | null;
  points?: number;
  segments?: number;
  collars?: number;
  boundary?: number;
}

/** `objects.meta.built`: of an operation, or of «Построить ЦТМ» (`kind: "dtm"`). */
export interface Built {
  kind: "operation" | "dtm";
  op?: string;
  title?: string;
  method?: string;
  params?: Rec;
  sources?: BuiltSource[];
  tolerances?: Rec;
  report?: Rec;
  at?: string;
  rebuild_of?: number | null;
}

export function asBuilt(v: unknown): Built | null {
  return isRec(v) && (v.kind === "operation" || v.kind === "dtm") ? (v as unknown as Built) : null;
}

/** «1, 3–5» of `lid`s (from 0) shown from 1. */
export function lineNumbers(lids: readonly number[]): string {
  const sorted = [...new Set(lids)].sort((a, b) => a - b).map((x) => x + 1);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    if (j > i + 1) parts.push(`${sorted[i]}–${sorted[j]}`); // a range of three or more; «1, 2» — as they are
    else parts.push(...sorted.slice(i, j + 1).map(String));
    i = j + 1;
  }
  return parts.join(", ");
}

const ROLE_TITLES: Record<string, string> = { wireframe: "Каркас", surface: "Поверхность", a: "Тело A", b: "Тело B", top: "Верхняя поверхность", bottom: "Нижняя поверхность", polygon: "Полигон", boundary: "Граница", contours: "Контуры", wireframes: "Каркасы" };

/** The field of the window a source came from: `wireframe` → «Каркас», `b[1]` → «Тело B (одно или несколько)». */
export function roleTitle(role: string | undefined, spec: OpSpec | undefined): string {
  if (!role) return "Исходный";
  const base = role.replace(/\[\d+\]$/, "");
  const keys = base === "wireframes" ? ["wireframe_ids"] : [base, `${base}_id`];
  const field = spec?.fields.find((f) => keys.includes(f.key));
  return field?.label ?? ROLE_TITLES[base] ?? "Исходный";
}

/** «Куб (№41, версия 2)» · «Контуры › линии 1, 3–5» · «Тело 2 — удалён из проекта». */
export function sourceText(s: BuiltSource): string {
  const name = `«${s.name ?? "?"}»`;
  if (s.id === null || s.id === undefined) return `${name} — удалён из проекта`;
  const parts = [`№${s.id}`, ...(known(s.version) ? [`версия ${s.version}`] : [])];
  const lines = Array.isArray(s.lines) ? (s.lines.length ? `линии ${lineNumbers(s.lines)}` : null) : s.kind === "line_layer" && s.lines === null ? "все линии" : null;
  const more = [lines, known(s.points) ? `точек: ${count(s.points)}` : null, known(s.segments) ? `отрезков: ${count(s.segments)}` : null, known(s.collars) ? `устьев: ${count(s.collars)}` : null, known(s.boundary) ? "граница" : null].filter(Boolean);
  return `${name} (${parts.join(", ")})${more.length ? ` — ${more.join(", ")}` : ""}`;
}

/** Names of the objects a parameter refers to. */
export interface Names {
  wireframe: (id: number) => string | null;
  layer: (id: number) => string | null;
}

function paramText(f: OpField, v: unknown, names: Names): string | null {
  if (v === null || v === undefined) return null;
  const wf = (id: unknown) => (typeof id === "number" ? `«${names.wireframe(id) ?? `№${id}`}»` : "—");
  const layer = (id: unknown) => (typeof id === "number" ? `«${names.layer(id) ?? `№${id}`}»` : "—");
  switch (f.type) {
    case "bool":
      return yes(v);
    case "choice":
      return f.options?.find((o) => o.value === v)?.label ?? String(v);
    case "number":
    case "integer":
      return known(v) ? `${small(v)}${f.unit ? ` ${f.unit}` : ""}` : null;
    case "vector":
      return Array.isArray(v) ? v.map((x) => (known(x) ? small(x) : "—")).join("; ") : null;
    case "wireframe":
      return wf(v);
    case "wireframes":
      return Array.isArray(v) ? v.map(wf).join(", ") : wf(v);
    case "layers":
      return Array.isArray(v) ? v.map(layer).join(", ") : null;
    case "lines":
      return Array.isArray(v) ? (v as Rec[]).map((c) => `${layer(c.layer_id)}: ${Array.isArray(c.lines) ? `линии ${lineNumbers(c.lines as number[])}` : "все линии"}`).join("; ") : null;
    case "line":
      return isRec(v) ? `${layer(v.layer_id)}, ${known(v.line) ? `линия ${v.line + 1}` : "единственная замкнутая линия"}` : null;
    case "sets":
      return Array.isArray(v) ? `наборов: ${v.length}` : null;
    default:
      return null;
  }
}

/** The parameters as the window names them; the fields not shown at the time (`when`) are left out. */
export function paramFacts(spec: OpSpec | undefined, params: Rec | undefined, names: Names): Fact[] {
  if (!spec || !params) return [];
  const facts: Fact[] = [];
  for (const f of spec.fields) {
    if (f.when && !Object.entries(f.when).every(([k, v]) => params[k] === v)) continue;
    const text = paramText(f, params[f.key], names);
    if (text !== null) facts.push({ label: f.label, value: text });
  }
  return facts;
}

const TOLERANCES: Record<string, string> = { weld: "Допуск сшивки", tolerance: "Допуск отклонения", max_deviation: "Наибольшее отклонение", manifold: "Допуск булевых операций (manifold3d)", volume: "Допуск объёма", section_tolerance: "Допуск разрезов" };

export function toleranceFacts(t: Rec | undefined): Fact[] {
  return Object.entries(t ?? {})
    .filter(([k, v]) => k in TOLERANCES && known(v))
    .map(([k, v]) => ({ label: TOLERANCES[k], value: `${small(v as number)} м` }));
}

/** The head of the block «Операция»: what, when, rebuilt from what. */
export function builtFacts(b: Built, rebuiltFrom: string | null): Fact[] {
  const facts: Fact[] = [{ label: "Операция", value: b.kind === "dtm" ? "Построить ЦТМ" : (b.title ?? b.op ?? "—") }];
  if (b.kind === "dtm" && b.method) facts.push({ label: "Метод", value: b.method });
  if (b.at) facts.push({ label: "Построено", value: checkedText(b.at) });
  if (b.rebuild_of !== null && b.rebuild_of !== undefined) facts.push({ label: "Перестроено из", value: rebuiltFrom ? `«${rebuiltFrom}» (№${b.rebuild_of})` : `№${b.rebuild_of}` });
  return facts;
}

/** Parameters of «Построить ЦТМ» (`built.params` of a DTM). */
export function dtmParamFacts(params: Rec | undefined): Fact[] {
  if (!params) return [];
  const facts: Fact[] = [];
  if (typeof params.breaklines === "boolean") facts.push({ label: "Линии — структурные", value: yes(params.breaklines) });
  facts.push({ label: "Граница", value: isRec(params.boundary) ? "замкнутая линия слоя" : "оболочка точек" });
  facts.push({ label: "Наибольшая длина ребра", value: known(params.max_edge) ? `${small(params.max_edge)} м` : "без ограничения" });
  return facts;
}
