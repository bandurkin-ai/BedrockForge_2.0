/**
 * What «Свойства» of a block model tell (ADR 0017 §3; ADR 0023 §2, §5; ТЗ 7.31 «Свойства»): the grid — counts, the step
 * (regular or variable), the corner, the turn about Z, the sub-blocks (the division of a parent, the levels, the blocks
 * and the parents) and the extent; the checks of the import (duplicates, overlaps, empty and constant fields, the volume,
 * the rows of the examples from 1); the fields with their units and statistics (a missing value stays «—», never 0;
 * `missing` are occupied cells without a value); the source of the import; what was not carried over. Pure: no React.
 */
import { extentFacts, num, type Fact } from "../../shell/props";
import { count, counted } from "../../shell/treeActions";
import type { BlockField, BlockModelInfo, BlockSource, CategoryStats, NumberStats } from "./api";
import { gridCounts } from "./tree";

const AXES = ["X", "Y", "Z"] as const;

const value = (v: number | null | undefined, digits = 3): string => (typeof v === "number" && Number.isFinite(v) ? num(v, digits) : "—");

/** «1,5 МБ», «820 КБ», «12 Б» */
export function bytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 ** 2) return `${num(n / 1024, 1)} КБ`;
  if (n < 1024 ** 3) return `${num(n / 1024 ** 2, 1)} МБ`;
  return `${num(n / 1024 ** 3, 1)} ГБ`;
}

/** «5 × 5 × 2,5 м»; a variable step — «переменный» with the range of each axis when the steps are known. */
export function stepText(b: Pick<BlockModelInfo, "grid">): string {
  const g = b.grid;
  if (g.size) return `${g.size.map((v) => num(v, 3)).join(" × ")} м`;
  if (!g.tensors) return "переменный";
  const t = g.tensors;
  const axis = (steps: number[]) => {
    const lo = Math.min(...steps);
    const hi = Math.max(...steps);
    return lo === hi ? num(lo, 3) : `${num(lo, 3)}…${num(hi, 3)}`;
  };
  return `переменный: X ${axis(t.x)}, Y ${axis(t.y)}, Z ${axis(t.z)} м`;
}

/** What the core of the model v2 adds to the answer of `GET /blocks[/{id}]` (e17-api.md §2, §4): the turn and the division of
 * the grid, the sub-blocks, the checks of the import. Models of the earlier version have none of it. */
export interface GridV2 {
  /** degrees about Z, counter-clockwise seen from above; 0 — none */
  rotation?: number;
  /** the division of a parent into sub-blocks along each axis (1 — none) */
  div?: [number, number, number];
  /** the levels of the division along each axis (2^level); null — the division is not a power of two (5, 3, 6 …; ADR 0030) */
  levels?: [number, number, number] | null;
}

export interface CheckExample {
  /** "duplicate" | "overlap" | "outside" | "crossing" … */
  check: string;
  /** the row of the file the model was made of, from 0 (the window shows it from 1) */
  row: number;
  other_row?: number;
}

/** `meta.checks` (e17-api.md §2): the findings of the import. */
export interface Checks {
  at?: string;
  duplicates: number;
  overlaps: { n: number; method: "exact" | "volume" | "boxes" | string };
  /** the names of the fields without a single value */
  empty: string[];
  /** the names of the fields with one value in all the blocks */
  constant: string[];
  /** m³ of all the blocks */
  volume: number | null;
  examples: CheckExample[];
}

export interface BlockV2 {
  /** `list` — a list of blocks (`meta v3`) */
  layout?: "grid" | "list";
  sub?: boolean;
  parents?: number;
  rows?: number;
  checks?: Checks | null;
}

export type BlockInfoV2 = Omit<BlockModelInfo, "grid"> & BlockV2 & { grid: BlockModelInfo["grid"] & GridV2 };

/** The model as the v2 core tells it; a model of the earlier version has the same shape without the extra keys. */
export const v2 = (b: BlockModelInfo): BlockInfoV2 => b as BlockInfoV2;

const LEVELS = (l: readonly number[]): string => l.map((x) => String(x)).join(" × ");

/** «, уровней 2 × 2 × 1» for a division by 2^L; «, произвольное деление» when an axis is divided by 3, 5, 6 … (no levels). */
export const divTail = (levels: readonly number[] | null | undefined, div: readonly number[]): string => {
  if (levels) return `, уровней ${LEVELS(levels)}`;
  return isFreeDiv(div) ? ", произвольное деление (не по уровням 2^L)" : "";
};

/** A division with an axis that is not a power of two (3, 5, 6 …): it has no levels. */
export const isFreeDiv = (div: readonly number[]): boolean => div.some((d) => d > 1 && (d & (d - 1)) !== 0);

/** A list of blocks (ADR 0030): the structure, the blocks and the enclosing grid — no sub-blocks, no parents. */
export function listFacts(b: BlockModelInfo): Fact[] {
  const total = b.grid.counts.reduce((a, c) => a * c, 1);
  const facts: Fact[] = [
    { label: "Структура", value: "список блоков (без сетки субблоков)" },
    { label: "Блоков", value: count(b.cells) },
    { label: "Охватывающая сетка", value: `${gridCounts(b)} (${counted(total, "ячейка", "ячейки", "ячеек")}), ячейка ${stepText(b)} — только для поиска блока под точкой` },
    { label: "Угол сетки", value: b.grid.corner.map((v, i) => `${AXES[i]} ${num(v, 3)}`).join(", ") },
    ...extentFacts(b.extent),
  ];
  const turn = v2(b).grid.rotation;
  if (typeof turn === "number" && turn !== 0) facts.splice(4, 0, { label: "Поворот", value: `${num(turn, 4)}° вокруг Z, против часовой стрелки при виде сверху` });
  if (b.crs) facts.push({ label: "Система координат", value: b.crs });
  if (typeof b.size === "number") facts.push({ label: "Файл ячеек", value: bytes(b.size) });
  return facts;
}

export function gridFacts(b: BlockModelInfo): Fact[] {
  if (b.layout === "list") return listFacts(b);
  const total = b.grid.counts.reduce((a, c) => a * c, 1);
  const share = total > 0 ? ` (${num((100 * b.cells) / total, 1)} %)` : "";
  const m = v2(b);
  const facts: Fact[] = [
    { label: "Сетка", value: `${gridCounts(b)} (${counted(total, "ячейка", "ячейки", "ячеек")})` },
    { label: "Занято ячеек", value: `${count(b.cells)}${share}` },
    { label: "Размер ячейки", value: stepText(b) },
    { label: "Угол модели", value: b.grid.corner.map((v, i) => `${AXES[i]} ${num(v, 3)}`).join(", ") },
    ...extentFacts(b.extent),
  ];
  const turn = m.grid.rotation;
  if (typeof turn === "number" && turn !== 0) facts.splice(4, 0, { label: "Поворот", value: `${num(turn, 4)}° вокруг Z, против часовой стрелки при виде сверху` });
  const sub = subFacts(b);
  if (sub.length) facts.splice(turn ? 5 : 4, 0, ...sub);
  if (b.crs) facts.push({ label: "Система координат", value: b.crs });
  if (typeof b.size === "number") facts.push({ label: "Файл ячеек", value: bytes(b.size) });
  return facts;
}

/** The sub-blocks of a model: the division of a parent, the levels, the blocks against the parents; none — nothing. */
export function subFacts(b: BlockModelInfo): Fact[] {
  const m = v2(b);
  const div = m.grid.div;
  if (!m.sub && !(div && div.some((d) => d > 1))) return [];
  const out: Fact[] = [];
  if (div) out.push({ label: "Субблоки", value: `родитель делится на ${div.map((d) => num(d, 0)).join(" × ")}${divTail(m.grid.levels, div)}` });
  else out.push({ label: "Субблоки", value: "есть" });
  if (typeof m.rows === "number" && typeof m.parents === "number") out.push({ label: "Блоков и родителей", value: `${count(m.rows)} блоков в ${count(m.parents)} родителях` });
  return out;
}

const CHECK_TITLES: Record<string, string> = { duplicate: "повтор", overlap: "перекрытие", outside: "за сеткой", crossing: "через границу родителя" };
export const MAX_EXAMPLES = 10;

/** «строка 104 — повтор строки 3» (rows from 1). */
export function exampleText(e: CheckExample): string {
  const title = CHECK_TITLES[e.check] ?? e.check;
  const other = typeof e.other_row === "number" ? ` строки ${count(e.other_row + 1)}` : "";
  return `строка ${count(e.row + 1)} — ${title}${other}`;
}

export interface CheckRow {
  label: string;
  value: string;
  /** a finding (not «0»): shown as a warning */
  found: boolean;
}

/** The checks of the import as rows of «Проверки»; null — the model was not checked (made before the checks). */
export function checkRows(checks: Checks | null | undefined): CheckRow[] | null {
  if (!checks) return null;
  const method = checks.overlaps.method === "exact" ? "точно, на сетке наименьшего блока" : "по объёму в родительской ячейке";
  const rows: CheckRow[] = [
    { label: "Дубликаты", value: checks.duplicates ? `${count(checks.duplicates)} — остаётся первая строка` : "нет", found: checks.duplicates > 0 },
    { label: "Перекрытия", value: checks.overlaps.n ? `${count(checks.overlaps.n)} (${method}) — строки сохранены` : `нет (${method})`, found: checks.overlaps.n > 0 },
    { label: "Пустые поля", value: checks.empty.length ? checks.empty.join(", ") : "нет", found: checks.empty.length > 0 },
    { label: "Постоянные поля", value: checks.constant.length ? `${checks.constant.join(", ")} — одно значение во всех блоках` : "нет", found: checks.constant.length > 0 },
  ];
  if (typeof checks.volume === "number") rows.push({ label: "Объём блоков", value: `${num(checks.volume, 1)} м³`, found: false });
  return rows;
}

export interface FieldRow {
  name: string;
  kind: string;
  unit: string;
  count: string;
  missing: string;
  min: string;
  max: string;
  mean: string;
  std: string;
  /** a category: its labels with the cells of each, the largest first (at most `TOP_LABELS`) */
  labels: string;
}

export const TOP_LABELS = 5;

const isNumberStats = (s: BlockField["stats"]): s is NumberStats => !!s && "mean" in s;
const isCategoryStats = (s: BlockField["stats"]): s is CategoryStats => !!s && "counts" in s;

export function fieldRow(f: BlockField): FieldRow {
  const s = f.stats;
  const row: FieldRow = {
    name: f.name,
    kind: f.kind === "category" ? "категория" : "число",
    unit: f.unit || "—",
    count: s ? count(s.count) : "—",
    missing: s ? count(s.missing) : "—",
    min: "—",
    max: "—",
    mean: "—",
    std: "—",
    labels: "",
  };
  if (isNumberStats(s)) Object.assign(row, { min: value(s.min), max: value(s.max), mean: value(s.mean), std: value(s.std) });
  const labels = f.labels ?? [];
  if (f.kind === "category" && labels.length) {
    const counts = isCategoryStats(s) ? s.counts : null;
    const order = labels.map((name, i) => ({ name, n: counts?.[i] ?? null })).sort((a, b) => (b.n ?? -1) - (a.n ?? -1));
    const shown = order.slice(0, TOP_LABELS).map((l) => (l.n === null ? l.name : `${l.name} — ${count(l.n)}`));
    const rest = labels.length - shown.length;
    row.labels = `${counted(labels.length, "значение", "значения", "значений")}: ${shown.join(", ")}${rest > 0 ? ` и ещё ${count(rest)}` : ""}`;
  }
  return row;
}

/** The names of the file formats the core reports (`format` of an import). */
const FORMATS: Record<string, string> = { omf: "OMF", dat: "Micromine DAT", csv: "CSV / TXT", csv_micromine: "CSV Micromine", dm: "Datamine DM", fbm: "Datamine FBM" };
/** What made a model that was not read from a file (`op` of the source: no `format`). */
const OPS: Record<string, string> = { fill: "Заполнение каркасами", code: "Кодирование каркасами", prototype: "Прототип", regularize: "Регуляризация", calc: "Калькулятор", import: "Импорт из файла" };

export function sourceFacts(s: BlockSource | null | undefined): Fact[] {
  if (!s) return [];
  const facts: Fact[] = [];
  if (s.format) facts.push({ label: "Формат", value: FORMATS[s.format] ?? s.format });
  else if (s.op) facts.push({ label: "Создана", value: OPS[s.op] ?? s.op });
  if (s.file) facts.push({ label: "Файл", value: s.file });
  if (s.element) facts.push({ label: "Элемент", value: s.element });
  return facts;
}
