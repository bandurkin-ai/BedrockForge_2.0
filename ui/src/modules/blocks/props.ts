/**
 * What «Свойства» of a block model tell (ADR 0017 §3): the grid — counts, the step (regular or variable), the
 * corner, the extent; the fields with their units and statistics (a missing value stays «—», never 0; `missing`
 * are occupied cells without a value); the source of the import; what was not carried over. Pure: no React.
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

export function gridFacts(b: BlockModelInfo): Fact[] {
  const total = b.grid.counts.reduce((a, c) => a * c, 1);
  const share = total > 0 ? ` (${num((100 * b.cells) / total, 1)} %)` : "";
  const facts: Fact[] = [
    { label: "Сетка", value: `${gridCounts(b)} (${counted(total, "ячейка", "ячейки", "ячеек")})` },
    { label: "Занято ячеек", value: `${count(b.cells)}${share}` },
    { label: "Размер ячейки", value: stepText(b) },
    { label: "Угол модели", value: b.grid.corner.map((v, i) => `${AXES[i]} ${num(v, 3)}`).join(", ") },
    ...extentFacts(b.extent),
  ];
  if (b.crs) facts.push({ label: "Система координат", value: b.crs });
  if (typeof b.size === "number") facts.push({ label: "Файл ячеек", value: bytes(b.size) });
  return facts;
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

const FORMATS: Record<string, string> = { omf: "OMF" };

export function sourceFacts(s: BlockSource | null | undefined): Fact[] {
  if (!s) return [];
  const facts: Fact[] = [{ label: "Формат", value: FORMATS[s.format] ?? s.format }];
  if (s.file) facts.push({ label: "Файл", value: s.file });
  if (s.element) facts.push({ label: "Элемент", value: s.element });
  return facts;
}
