/**
 * The report pit-by-pit of a scenario (ТЗ 8.11.3–8.11.5; ADR 0028 решения 18, 19; docs/progress/e21-api.md §7): the answer of `GET /pit/scenarios/{id}/report?calendar=`
 * (`core/tests/data/pit/contract_answers.json` → `report_get`) and what the panel and the chart make of it. A gap is `null`, never 0 (8.0.1): a cell with no value is
 * «—» in the table and a break of the line on the chart. The table of the core (`table`) is shown as the text of the core; the two further views — «Выручка и затраты» and
 * «Накопленные» — are made from the numbers of `shells` with the same rules of the text (the decimal comma, a no-break space between the thousands, `columns[].decimals`).
 * The readers never throw: an answer that is not a report is `null`. No React.
 */
import { list, num, obj, readWarnings, str, type Warning } from "./answers";

export const DASH = "—";
const NBSP = " ";

/** A number as the core writes it (`resources.forms.format_number`): half away from zero, the decimal comma, no-break spaces between the thousands; a gap — «—». */
export function formatNumber(value: number | null | undefined, decimals: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const f = 10 ** decimals;
  const rounded = Math.round(Number((Math.abs(value) * f).toPrecision(15))) / f;
  const [int, frac] = rounded.toFixed(decimals).split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const sign = value < 0 && rounded !== 0 ? "-" : "";
  return `${sign}${grouped}${frac ? `,${frac}` : ""}`;
}

// ---- the answer ----

export interface ColumnInfo {
  key: string;
  title: string;
  unit: string;
  decimals: number;
}

export interface CoreCell {
  value: number | null;
  text: string | null;
}

export interface ElementInfo {
  field: string;
  unit: string | null;
  unit_label: string;
  metal_unit: string;
  price_label: string;
}

export interface ElementPart {
  grade: number | null;
  metal: number | null;
  rec_metal: number | null;
}

/** The sums of a shell or of the accumulation: the money and the tonnes; `strip` is W/O (null when there is no ore). */
export interface Sums {
  blocks: number | null;
  ore_t: number | null;
  waste_t: number | null;
  strip: number | null;
  revenue: number | null;
  cost_mine: number | null;
  cost_process: number | null;
  cost_sales: number | null;
  royalty: number | null;
  cf: number | null;
  elements: Record<string, ElementPart>;
}

export interface ShellRow extends Sums {
  k: number;
  rf: number | null;
  cum: Sums;
  /** NPV of the best and of the worst case for n = k; null — no calendar */
  best: number | null;
  worst: number | null;
}

export interface Calendar {
  q_ore: number | null;
  q_mass: number | null;
  /** a share per year (0,1 is 10 %) */
  rate: number | null;
}

export interface PitReport {
  scenario_id: number;
  name: string;
  version: number;
  state: string;
  algo: string;
  params_hash: string | null;
  base_rf: number | null;
  model: { id: number | null; name: string };
  calendar: Calendar;
  npv_ready: boolean;
  elements: ElementInfo[];
  shells: ShellRow[];
  totals: Sums & { best: number | null; worst: number | null };
  series: { k: number[]; rf: (number | null)[]; ore_t: (number | null)[]; waste_t: (number | null)[]; cf: (number | null)[]; npv_best: (number | null)[]; npv_worst: (number | null)[] };
  table: { columns: ColumnInfo[]; rows: { k: number; cells: Record<string, CoreCell> }[] };
  warnings: Warning[];
  seconds: number | null;
}

const nums = (v: unknown): (number | null)[] => list(v).map(num);

function readElements(raw: unknown): Record<string, ElementPart> {
  const out: Record<string, ElementPart> = {};
  for (const [name, v] of Object.entries(obj(raw))) {
    const e = obj(v);
    out[name] = { grade: num(e.grade), metal: num(e.metal), rec_metal: num(e.rec_metal) };
  }
  return out;
}

function readSums(raw: unknown): Sums {
  const s = obj(raw);
  return {
    blocks: num(s.blocks),
    ore_t: num(s.ore_t),
    waste_t: num(s.waste_t),
    strip: num(s.strip),
    revenue: num(s.revenue),
    cost_mine: num(s.cost_mine),
    cost_process: num(s.cost_process),
    cost_sales: num(s.cost_sales),
    royalty: num(s.royalty),
    cf: num(s.cf),
    elements: readElements(s.elements),
  };
}

function readShell(raw: unknown): ShellRow | null {
  const s = obj(raw);
  const k = num(s.k);
  if (k === null) return null;
  return { ...readSums(s), k, rf: num(s.rf), cum: readSums(s.cum), best: num(obj(s.best).npv), worst: num(obj(s.worst).npv) };
}

function readTable(raw: unknown): PitReport["table"] {
  const t = obj(raw);
  const columns = list(t.columns).flatMap((x): ColumnInfo[] => {
    const c = obj(x);
    return typeof c.key === "string" ? [{ key: c.key, title: str(c.title) ?? c.key, unit: str(c.unit) ?? "", decimals: num(c.decimals) ?? 0 }] : [];
  });
  const rows = list(t.rows).flatMap((x) => {
    const r = obj(x);
    const k = num(r.k);
    if (k === null) return [];
    const cells: Record<string, CoreCell> = {};
    for (const [key, v] of Object.entries(obj(r.cells))) cells[key] = { value: num(obj(v).value), text: str(obj(v).text) };
    return [{ k, cells }];
  });
  return { columns, rows };
}

/** The answer of the report; null when it is not one (no scenario, no shells key). */
export function readReport(raw: unknown): PitReport | null {
  const r = obj(raw);
  const id = num(r.scenario_id);
  if (id === null || !Array.isArray(r.shells)) return null;
  const model = obj(r.model);
  const cal = obj(r.calendar);
  const ser = obj(r.series);
  const tot = obj(r.totals);
  return {
    scenario_id: id,
    name: str(r.name) ?? "",
    version: num(r.version) ?? 1,
    state: str(r.state) ?? "ready",
    algo: str(r.algo) ?? "",
    params_hash: str(r.params_hash),
    base_rf: num(r.base_rf),
    model: { id: num(model.id), name: str(model.name) ?? "" },
    calendar: { q_ore: num(cal.q_ore), q_mass: num(cal.q_mass), rate: num(cal.rate) },
    npv_ready: r.npv_ready === true,
    elements: list(r.elements).flatMap((x): ElementInfo[] => {
      const e = obj(x);
      return typeof e.field === "string" ? [{ field: e.field, unit: str(e.unit), unit_label: str(e.unit_label) ?? str(e.unit) ?? "", metal_unit: str(e.metal_unit) ?? "", price_label: str(e.price_label) ?? "" }] : [];
    }),
    shells: r.shells.map(readShell).filter((s): s is ShellRow => s !== null),
    totals: { ...readSums(tot), best: num(obj(tot.best).npv), worst: num(obj(tot.worst).npv) },
    series: { k: nums(ser.k).filter((v): v is number => v !== null), rf: nums(ser.rf), ore_t: nums(ser.ore_t), waste_t: nums(ser.waste_t), cf: nums(ser.cf), npv_best: nums(ser.npv_best), npv_worst: nums(ser.npv_worst) },
    table: readTable(r.table),
    warnings: readWarnings(r.warnings),
    seconds: num(r.seconds),
  };
}

// ---- the tables of the panel ----

export type TableMode = "shells" | "money" | "cum";

export const TABLE_MODES: readonly { mode: TableMode; label: string; hint: string }[] = [
  { mode: "shells", label: "По оболочкам", hint: "Таблица ядра: приращение каждой оболочки при базовой цене (RF = 1), NPV лучшего и худшего случаев" },
  { mode: "money", label: "Выручка и затраты", hint: "Выручка, затраты на добычу, переработку, сбыт, роялти и CF приращения каждой оболочки" },
  { mode: "cum", label: "Накопленные", hint: "Накопленные значения по оболочкам 1…k: блоки, руда, пустая, вскрыша, содержания, металл, выручка, затраты, CF" },
];

/** A cell of the table on the screen: the text, whether it is a gap (drawn apart). */
export interface ViewCell {
  text: string;
  missing: boolean;
}

export interface ViewRow {
  key: string;
  /** the number of the shell; null — the row of the totals */
  k: number | null;
  total: boolean;
  cells: Record<string, ViewCell>;
}

export interface ViewTable {
  columns: ColumnInfo[];
  rows: ViewRow[];
}

const cell = (text: string): ViewCell => ({ text, missing: text === DASH });
const made = (value: number | null, decimals: number): ViewCell => cell(formatNumber(value, decimals));

/** A cell of the core as the text of the core; a gap (no value, no cell) is «—». */
export function coreText(c: CoreCell | undefined, decimals: number): ViewCell {
  if (!c || c.value === null) return cell(DASH);
  return cell(c.text !== null && c.text !== "" ? c.text : formatNumber(c.value, decimals));
}

const col = (key: string, title: string, unit: string, decimals: number): ColumnInfo => ({ key, title, unit, decimals });

const elementColumns = (r: PitReport): ColumnInfo[] =>
  r.elements.flatMap((e) => [col(`${e.field}:grade`, `${e.field}, содержание`, e.unit_label, 2), col(`${e.field}:rec_metal`, `${e.field}, извлекаемый металл`, e.metal_unit, 0)]);

function sumCells(s: Sums, elements: ElementInfo[]): Record<string, ViewCell> {
  const out: Record<string, ViewCell> = {
    blocks: made(s.blocks, 0),
    ore_t: made(s.ore_t, 0),
    waste_t: made(s.waste_t, 0),
    strip: made(s.strip, 2),
    revenue: made(s.revenue, 0),
    cost_mine: made(s.cost_mine, 0),
    cost_process: made(s.cost_process, 0),
    cost_sales: made(s.cost_sales, 0),
    royalty: made(s.royalty, 0),
    cf: made(s.cf, 0),
  };
  for (const e of elements) {
    const part = s.elements[e.field];
    out[`${e.field}:grade`] = made(part?.grade ?? null, 2);
    out[`${e.field}:rec_metal`] = made(part?.rec_metal ?? null, 0);
  }
  return out;
}

const moneyColumns = [col("revenue", "Выручка", "$", 0), col("cost_mine", "Добыча", "$", 0), col("cost_process", "Переработка", "$", 0), col("cost_sales", "Сбыт", "$", 0), col("royalty", "Роялти", "$", 0), col("cf", "CF", "$", 0)];

/** The core's table as it is, and the row of the totals (the numbers of `totals`: the table of the core has no row of totals). */
function shellsTable(r: PitReport): ViewTable {
  const columns = r.table.columns.length ? r.table.columns : [col("k", "№", "", 0), col("rf", "RF", "", 2), col("ore_t", "Руда", "т", 0), col("waste_t", "Пустая", "т", 0), col("cf", "CF", "$", 0)];
  const byK = new Map(r.table.rows.map((x) => [x.k, x]));
  const rows: ViewRow[] = r.shells.map((s) => {
    const core = byK.get(s.k);
    const cells: Record<string, ViewCell> = {};
    for (const c of columns) cells[c.key] = c.key === "k" ? cell(String(s.k)) : coreText(core?.cells[c.key], c.decimals);
    return { key: `k${s.k}`, k: s.k, total: false, cells };
  });
  if (r.shells.length) {
    const sums: Record<string, ViewCell> = { ...sumCells(r.totals, r.elements), best_npv: made(r.totals.best, 0), worst_npv: made(r.totals.worst, 0), k: cell("Итого"), rf: cell("") };
    const cells: Record<string, ViewCell> = {};
    for (const c of columns) cells[c.key] = sums[c.key] ?? cell(DASH);
    rows.push({ key: "total", k: null, total: true, cells });
  }
  return { columns, rows };
}

function moneyTable(r: PitReport): ViewTable {
  const columns = [col("k", "№", "", 0), col("rf", "RF", "", 2), ...moneyColumns];
  const rows: ViewRow[] = r.shells.map((s) => ({ key: `k${s.k}`, k: s.k, total: false, cells: { k: cell(String(s.k)), rf: made(s.rf, 2), ...sumCells(s, r.elements) } }));
  if (r.shells.length) rows.push({ key: "total", k: null, total: true, cells: { k: cell("Итого"), rf: cell(""), ...sumCells(r.totals, r.elements) } });
  return { columns, rows };
}

function cumTable(r: PitReport): ViewTable {
  const columns = [col("k", "№", "", 0), col("rf", "RF", "", 2), col("blocks", "Блоки", "шт", 0), col("ore_t", "Руда", "т", 0), col("waste_t", "Пустая", "т", 0), col("strip", "Вскрыша", "т/т", 2), ...elementColumns(r), ...moneyColumns];
  const rows: ViewRow[] = r.shells.map((s) => ({ key: `k${s.k}`, k: s.k, total: false, cells: { k: cell(String(s.k)), rf: made(s.rf, 2), ...sumCells(s.cum, r.elements) } }));
  return { columns, rows };
}

/** The table of a mode: «По оболочкам» is the text of the core, the others are made from `shells` with the same rules of the text. */
export function viewTable(r: PitReport, mode: TableMode): ViewTable {
  return mode === "money" ? moneyTable(r) : mode === "cum" ? cumTable(r) : shellsTable(r);
}

/** The head of a column: «Руда, т». */
export const columnTitle = (c: ColumnInfo): string => (c.unit ? `${c.title}, ${c.unit}` : c.title);

// ---- the chart ----

export interface ChartData {
  k: number[];
  rf: (number | null)[];
  ore: (number | null)[];
  waste: (number | null)[];
  cf: (number | null)[];
  best: (number | null)[];
  worst: (number | null)[];
}

/** What the chart draws: `series` of the answer; when it is not complete (a shorter array), the numbers of `shells`. A gap stays `null`. */
export function chartData(r: PitReport): ChartData {
  const s = r.series;
  const n = s.k.length;
  const whole = n > 0 && [s.rf, s.ore_t, s.waste_t, s.cf, s.npv_best, s.npv_worst].every((a) => a.length === n);
  if (whole) return { k: s.k, rf: s.rf, ore: s.ore_t, waste: s.waste_t, cf: s.cf, best: s.npv_best, worst: s.npv_worst };
  return { k: r.shells.map((x) => x.k), rf: r.shells.map((x) => x.rf), ore: r.shells.map((x) => x.ore_t), waste: r.shells.map((x) => x.waste_t), cf: r.shells.map((x) => x.cf), best: r.shells.map((x) => x.best), worst: r.shells.map((x) => x.worst) };
}

// ---- the calendar of the panel ----

/** The text of the calendar: «Q_р 1 000 000 т/год, Q_гм без ограничения, r = 10 %». */
export function calendarText(c: Calendar): string {
  const q = (v: number | null, none: string) => (v === null ? none : `${formatNumber(v, 0)} т/год`);
  const rate = c.rate === null ? DASH : `${String(Math.round(c.rate * 10000) / 100).replace(".", ",")} %`;
  return `Q_р ${q(c.q_ore, "не задана")}, Q_гм ${q(c.q_mass, "без ограничения")}, r = ${rate}`;
}

/** The text of the report for the log and for the assistant. */
export function reportText(r: PitReport): string {
  const first = r.shells[0];
  const last = r.shells[r.shells.length - 1];
  if (!last || !first) return `Pit-by-pit сценария «${r.name}»: оболочек нет.`;
  const lines = [`Pit-by-pit сценария «${r.name}»: оболочек ${r.shells.length} (RF ${formatNumber(first.rf, 2)} … ${formatNumber(last.rf, 2)}).`];
  const t = r.totals;
  lines.push(`Итог по последней оболочке: руда ${formatNumber(t.ore_t, 0)} т, пустая ${formatNumber(t.waste_t, 0)} т, вскрыша ${t.strip === null ? "не определена (руды нет)" : formatNumber(t.strip, 2)}, CF ${formatNumber(t.cf, 0)} $.`);
  lines.push(r.npv_ready ? `NPV (${calendarText(r.calendar)}): лучший случай ${formatNumber(t.best, 0)} $, худший ${formatNumber(t.worst, 0)} $.` : "NPV не считается: не задана производительность по руде Q_р (команда «Календарь» или поле «Q_р» панели).");
  for (const w of r.warnings.slice(0, 3)) lines.push(`Предупреждение: ${w.hint ? `${w.message} ${w.hint}` : w.message}`);
  lines.push("Таблица и график — во вкладке «Pit-by-pit» нижней панели.");
  return lines.join("\n");
}
