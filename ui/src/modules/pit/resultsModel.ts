/**
 * The words and the numbers of the results of the pit optimisation (ТЗ 8.11.4–8.11.5; ADR 0028 решения 25–27): the shells of a scenario to choose for the
 * surfaces, the numbers of the command `pit.surfaces`, the texts of the three commands for the log and for the assistant, the rows of the comparison and
 * the curves of its chart. A value that is not known is «—» — never 0 (a shell that only one scenario has has no numbers on the other side). Pure, no React.
 */
import { fmt, fmtCount } from "../blocks/report/format";
import type { ScenarioDetail } from "./answers";
import { list, num, obj } from "./answers";
import { warningText } from "./format";
import type { CompareAnswer, CompareShell, ShellKey, ShellNumbers, SolidResult, SurfacesResult } from "./resultsAnswers";

export const DASH = "—";

/** A number for a table of the results: not known — «—». */
export const val = (v: number | null | undefined): string => (v === null || v === undefined || !Number.isFinite(v) ? DASH : fmt(v));

/** RF as the people write it: 0,8; 1,05. */
export const rfText = (rf: number | null | undefined): string => (rf === null || rf === undefined || !Number.isFinite(rf) ? DASH : rf.toLocaleString("ru-RU", { maximumFractionDigits: 6 }));

// ---- the shells of a scenario ----

export interface ShellInfo {
  /** the number of the shell 1…N (the value of the field PIT) */
  k: number;
  rf: number | null;
  blocks: number | null;
  ore_t: number | null;
  waste_t: number | null;
}

/** The shells of the stored result of a scenario (`meta.shells`); none when it has no result. */
export function shellsOf(s: ScenarioDetail | null): ShellInfo[] {
  if (!s || !s.has_result || !s.meta) return [];
  return list(s.meta.shells).flatMap((x): ShellInfo[] => {
    const r = obj(x);
    const k = num(r.k);
    return k === null ? [] : [{ k, rf: num(r.rf), blocks: num(r.blocks), ore_t: num(r.ore_t), waste_t: num(r.waste_t) }];
  });
}

/** The shells of the choice of the window: the numbers of the picked (none picked yet — all of them), in their order. */
export const pickedOf = (all: readonly ShellInfo[], picked: readonly number[] | null): number[] => (picked === null ? all.map((s) => s.k) : all.filter((s) => picked.includes(s.k)).map((s) => s.k));

/** The shell the solid is proposed for: the one at the base price (RF = 1), else the last (the largest pit). */
export const defaultShell = (all: readonly ShellInfo[]): number | null => (all.find((s) => s.rf === 1) ?? all[all.length - 1])?.k ?? null;

// ---- the numbers of the command ----

export type ShellsGot = { ok: true; shells: number[] } | { ok: false; message: string; hint: string };

/**
 * `shells` of `pit.surfaces`: the numbers 1…N through «;» or «,» or a space; a range «3-6» is fine (the shells of the field PIT, not RF). `n` — the
 * number of the shells of the result (null — not known, the core checks). Every number once, in ascending order.
 */
export function parseShells(raw: unknown, n: number | null): ShellsGot {
  const hint = n === null ? "Номера оболочек — целые от 1, например 1; 3; 5-7." : `Номера оболочек — целые от 1 до ${n}, например 1; 3; 5-7.`;
  const parts = (typeof raw === "number" ? [String(raw)] : typeof raw === "string" ? raw.split(/[;,\s]+/) : []).map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return { ok: false, message: "Не заданы номера оболочек.", hint };
  const out = new Set<number>();
  for (const p of parts) {
    const m = /^(\d+)(?:-(\d+))?$/.exec(p);
    if (!m) return { ok: false, message: `«${p}» — не номер оболочки.`, hint };
    const from = Number(m[1]);
    const to = m[2] === undefined ? from : Number(m[2]);
    if (from < 1 || to < from || (n !== null && to > n)) return { ok: false, message: `Оболочки «${p}» нет${n === null ? "" : `: в результате их ${n}`}.`, hint };
    for (let k = from; k <= to; k++) out.add(k);
  }
  return { ok: true, shells: [...out].sort((a, b) => a - b) };
}

/** The numbers of the shells as the text of the argument: «1; 2; 3». */
export const shellsText = (shells: readonly number[]): string => shells.join("; ");

// ---- the texts of the commands ----

export function surfacesText(r: SurfacesResult, scenario: string): string {
  const w = r.wireframes;
  const place = r.folder ? `, папка «${r.folder}» в разделе «Каркасы»` : r.folder_id === null ? "" : `, папка сценария №${r.folder_id} в разделе «Каркасы»`;
  const lines = [`Поверхности оболочек сценария «${scenario}» построены: ЦТМ ${fmtCount(w.length)}${place}. Повторное построение заменяет их на месте.`];
  for (const x of w.slice(0, 12)) lines.push(`Оболочка ${x.shell} (RF ${rfText(x.rf)}): «${x.name}», узлов ${x.nodes === null ? DASH : fmtCount(x.nodes)}, треугольников ${x.triangles === null ? DASH : fmtCount(x.triangles)}.`);
  if (w.length > 12) lines.push(`…и ещё оболочек: ${w.length - 12}.`);
  for (const x of r.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(x)}`);
  return lines.join("\n");
}

const METHODS: Record<string, string> = { between: "тело между рельефом и поверхностью оболочки", difference: "разность тел (запасной путь)" };
export const methodText = (m: string): string => METHODS[m] ?? (m || DASH);

export function solidText(r: SolidResult): string {
  const lines = [`Каркас карьера «${r.name}» построен по оболочке ${r.shell ?? DASH} (RF ${rfText(r.rf)}): объём ${val(r.volume)} м³, замкнут${r.parts !== null && r.parts > 1 ? `, частей ${fmtCount(r.parts)}` : ""}.`];
  lines.push(`Сверка: объём блоков оболочки ${val(r.volume_blocks)} м³, расхождение ${val(r.volume_diff_pct)} %. Метод: ${methodText(r.method)}.`);
  for (const x of r.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(x)}`);
  return lines.join("\n");
}

const warn = (c: CompareAnswer): string[] => c.warnings.slice(0, 5).map((w) => `Предупреждение: ${warningText(w)}`);

export function compareText(c: CompareAnswer): string {
  const both = c.shells.filter((s) => s.status === "both").length;
  const onlyA = c.shells.filter((s) => s.status === "only_a").length;
  const onlyB = c.shells.filter((s) => s.status === "only_b").length;
  const lines = [`Сравнение сценариев «${c.a.name}» (A, №${c.a.id}) и «${c.b.name}» (B, №${c.b.id}): различий параметров ${c.params_diff.length}, оболочек по RF ${c.shells.length} (в обоих ${both}${onlyA + onlyB ? `, только в A ${onlyA}, только в B ${onlyB}: у них нет разности` : ""}).`];
  const t = c.totals;
  if (t) lines.push(`Итог по последней оболочке: NPV лучшего случая A ${val(t.a?.npv_best)}, B ${val(t.b?.npv_best)}, A − B ${val(t.diff?.npv_best)} $; худшего — A ${val(t.a?.npv_worst)}, B ${val(t.b?.npv_worst)}, A − B ${val(t.diff?.npv_worst)} $.`);
  if (c.blocks) lines.push(`Блоки конечного карьера: только в A ${fmtCount(c.blocks.only_a.blocks ?? 0)} (${val(c.blocks.only_a.tonnes)} т), только в B ${fmtCount(c.blocks.only_b.blocks ?? 0)} (${val(c.blocks.only_b.tonnes)} т).`);
  else if (!c.same_grid) lines.push("Сетки различаются: блоки по сценариям не сравниваются, только оболочки и NPV.");
  return [...lines, ...warn(c)].join("\n");
}

// ---- the parameters that differ ----

const LEAF: Record<string, string> = { price: "цена", sales: "сбыт", royalty: "роялти", unit: "единица", field: "поле", c_p: "затраты на переработку c_p", c_mr: "доплата c_mр", c_m0: "затраты на добычу c_m0", z_ref: "отметка Z_ref", h_gl: "глубина H_гл", c_gl: "прирост c_гл", angle: "угол", az: "азимут", tolerance: "допуск угла", q_ore: "производительность по руде Q_р", q_mass: "производительность по горной массе Q_гм", rate: "ставка" };
const TOP: Record<string, string> = { model_id: "Блочная модель", density: "Плотность", default_density: "Плотность по умолчанию", prefix: "Префикс полей PIT и ORE", k: "Число уровней шаблона K", terrain_id: "Рельеф", filter_id: "Фильтр", restrict: "Ограничитель", solver: "Решатель" };

/** A path of the keys of the params in words: `elements.0.price` → «Элемент 1: цена», `rf.3` → «Коэффициент цены RF №4». */
export function paramLabel(path: string): string {
  const p = path.split(".");
  if (p.length === 1 && TOP[p[0]]) return TOP[p[0]];
  if (p[0] === "rf" && p.length === 2 && /^\d+$/.test(p[1])) return `Коэффициент цены RF №${Number(p[1]) + 1}`;
  if (p[0] === "rf") return `Коэффициенты цены RF (${p.slice(1).join(".") || "все"})`;
  if (p[0] === "elements" && p.length >= 2 && /^\d+$/.test(p[1])) return `Элемент ${Number(p[1]) + 1}${p[2] ? `: ${LEAF[p[2]] ?? p[2]}` : ""}`;
  if (p[0] === "mining" && p[1]) return `Добыча: ${LEAF[p[1]] ?? p[1]}`;
  if (p[0] === "slopes" && p[1] === "tolerance") return "Допуск угла борта";
  if (p[0] === "slopes") return `Углы бортов (${p.slice(1).join(".")})`;
  if (p[0] === "rocks") return `Породы (${p.slice(1).join(".")})`;
  if (p[0] === "calendar" && p[1]) return `Календарь: ${LEAF[p[1]] ?? p[1]}`;
  return path;
}

/** The value of a parameter in words; not set — «—». */
export function paramValue(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === "number") return val(v);
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "string") return v === "" ? "(пусто)" : v;
  return JSON.stringify(v);
}

// ---- the rows and the curves of the comparison ----

export interface MetricInfo {
  key: ShellKey;
  label: string;
  unit: string;
}

export const METRICS: readonly MetricInfo[] = [
  { key: "npv_best", label: "NPV лучшего случая", unit: "$" },
  { key: "npv_worst", label: "NPV худшего случая", unit: "$" },
  { key: "cf", label: "CF", unit: "$" },
  { key: "ore_t", label: "Руда", unit: "т" },
  { key: "waste_t", label: "Пустая порода", unit: "т" },
  { key: "blocks", label: "Блоки", unit: "шт." },
];

export const metricInfo = (key: ShellKey): MetricInfo => METRICS.find((m) => m.key === key) ?? METRICS[0];

export interface CompareRow {
  rf: number;
  status: CompareShell["status"];
  a: number | null;
  b: number | null;
  /** A − B; null when a side has no such shell or no value (a gap, never 0) */
  diff: number | null;
  k_a: number | null;
  k_b: number | null;
}

const pick = (n: ShellNumbers | null, key: ShellKey): number | null => (n ? n[key] : null);

/** The shells by RF for one quantity: both sides' numbers and the difference of the core. */
export const compareRows = (c: CompareAnswer, key: ShellKey): CompareRow[] =>
  c.shells.map((s) => ({ rf: s.rf, status: s.status, a: pick(s.a, key), b: pick(s.b, key), diff: pick(s.diff, key), k_a: s.k_a, k_b: s.k_b }));

export const STATUS_TEXT: Record<CompareShell["status"], string> = { both: "в обоих", only_a: "только в A", only_b: "только в B" };

export interface Curve {
  id: "a" | "b";
  label: string;
  xs: number[];
  ys: (number | null)[];
}

/** The curve of one side: RF on the axis, the quantity above it; a shell the side does not have is not a point (it breaks nothing and is not 0). */
export function curveOf(c: CompareAnswer, side: "a" | "b", key: ShellKey): Curve {
  const xs: number[] = [];
  const ys: (number | null)[] = [];
  for (const s of [...c.shells].sort((p, q) => p.rf - q.rf)) {
    const n = side === "a" ? s.a : s.b;
    if (!n) continue;
    xs.push(s.rf);
    ys.push(n[key]);
  }
  const head = side === "a" ? c.a : c.b;
  return { id: side, label: `${side.toUpperCase()} — ${head.name || `сценарий №${head.id}`}`, xs, ys };
}

/** The CSV of the chart: «Сценарий; RF; value», a gap is an empty cell. */
export const curveRows = (curves: readonly Curve[]): (string | number | null)[][] => curves.flatMap((c) => c.xs.map((x, i) => [c.label, x, c.ys[i]]));
