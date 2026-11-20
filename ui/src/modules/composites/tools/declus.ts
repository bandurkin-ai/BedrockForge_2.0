/** «Декластеризация» (ТЗ 8.2.1; ADR 0018 §7): BF cells and polygons in plan, GSLIB declus in 3D with anisy, anisz;
 * the weights — the field `DECL_W` and a report «размер → среднее» (core `stats/declus3d.py::declus_weights`):
 * the curve of the cell sizes, the best size, the naive and the declustered mean. Pure. */
import { numberText, type ToolSpec } from "../params";

const cell3d = (v: Record<string, unknown>) => v.method === "cell3d";
const cells = (v: Record<string, unknown>) => v.method !== "polygon";

export const declus: ToolSpec = {
  id: "declus",
  command: "composite.declus",
  title: "Декластеризация",
  endpoint: "/declus",
  preview: null,
  source: { table: "one", fields: "one", domain: false },
  params: [
    {
      key: "method",
      label: "Метод",
      kind: "choice",
      default: "cell",
      choices: [
        { value: "cell", label: "ячейки в плане" },
        { value: "polygon", label: "полигоны в плане" },
        { value: "cell3d", label: "ячейки в 3D" },
      ],
    },
    { key: "cmin", label: "Размер ячейки от", kind: "number", unit: "м", min: 0.01, when: cells },
    { key: "cmax", label: "Размер ячейки до", kind: "number", unit: "м", min: 0.01, when: cells },
    { key: "ncell", label: "Число размеров", kind: "integer", min: 1, max: 500, default: 20, when: cells },
    { key: "noff", label: "Сдвигов начала", kind: "integer", min: 1, max: 64, default: 8, when: cells },
    {
      key: "minmax",
      label: "Критерий",
      kind: "choice",
      default: "min",
      when: cells,
      choices: [
        { value: "min", label: "минимум среднего" },
        { value: "max", label: "максимум среднего" },
      ],
    },
    { key: "anisy", label: "Анизотропия Y", kind: "number", min: 0.01, max: 100, default: 1, when: cell3d },
    { key: "anisz", label: "Анизотропия Z", kind: "number", min: 0.01, max: 1e6, default: 1, when: cell3d },
  ],
  required: ["set_id", "table", "field", "method"],
  writes: "fields",
  note: "Веса — поле DECL_W (сумма весов = число проб); нет координат или значения — пусто.",
  finish: (body) => (typeof body.cmin === "number" && typeof body.cmax === "number" && body.cmin > body.cmax ? "«Размер ячейки от» больше «до»" : body),
};

// ---- the report of the job (`dh_results.report`) ----

type Num = number | null;
const num = (v: unknown): Num => (typeof v === "number" && Number.isFinite(v) ? v : null);

export interface DeclusReport {
  method: string;
  n: number;
  n_missing: number;
  naive_mean: Num;
  mean: Num;
  /** no size changed the mean the right way — the weights are 1 */
  uniform: boolean;
  best_size: Num;
  sizes: Num[];
  means: Num[];
  min_weight: Num;
  max_weight: Num;
  n_zero: number;
  /** the grid of the polygons */
  nx: number | null;
  ny: number | null;
  notes: string[];
}

/** The report made safe to draw; a report without `n` (not a declustering) — null. */
export function toDeclusReport(raw: Record<string, unknown> | null | undefined): DeclusReport | null {
  if (!raw || typeof raw.n !== "number") return null;
  const list = (v: unknown): Num[] => (Array.isArray(v) ? v.map(num) : []);
  return {
    method: typeof raw.method === "string" ? raw.method : "",
    n: raw.n,
    n_missing: num(raw.n_missing) ?? 0,
    naive_mean: num(raw.naive_mean),
    mean: num(raw.mean),
    uniform: raw.uniform === true,
    best_size: num(raw.best_size),
    sizes: list(raw.sizes),
    means: list(raw.means),
    min_weight: num(raw.min_weight),
    max_weight: num(raw.max_weight),
    n_zero: num(raw.n_zero) ?? 0,
    nx: num(raw.nx),
    ny: num(raw.ny),
    notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [],
  };
}

const METHOD_TITLES: Record<string, string> = { cell: "ячейки в плане", polygon: "полигоны в плане", cell3d: "ячейки в 3D" };
export const methodTitle = (m: string): string => METHOD_TITLES[m] ?? m;

/** The mean at the best size on the curve (the point to mark); none — null. */
export function bestPoint(r: DeclusReport): { x: number; y: number } | null {
  if (r.best_size === null) return null;
  const i = r.sizes.findIndex((s) => s !== null && Math.abs(s - r.best_size!) <= 1e-9 * Math.max(1, Math.abs(s)));
  const y = i >= 0 ? r.means[i] : r.mean;
  return y === null || y === undefined ? null : { x: r.best_size, y };
}

const t = (v: Num, digits = 4): string => (v === null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits }));

/** The facts of the report as rows «название — значение». */
export function declusFacts(r: DeclusReport): { label: string; value: string }[] {
  const out = [
    { label: "Метод", value: methodTitle(r.method) },
    { label: "Проб с весом", value: t(r.n, 0) },
    { label: "Без координат или значения", value: t(r.n_missing, 0) },
    { label: "Среднее без весов", value: t(r.naive_mean) },
    { label: "Среднее с весами", value: t(r.mean) },
  ];
  if (r.method === "polygon" && r.nx !== null && r.ny !== null) out.push({ label: "Сетка полигонов", value: `${t(r.nx, 0)} × ${t(r.ny, 0)}` });
  if (r.method !== "polygon") out.push({ label: "Лучший размер ячейки, м", value: r.uniform ? "нет (веса равны 1)" : r.best_size === null ? "—" : numberText(r.best_size) });
  out.push({ label: "Вес: от … до", value: `${t(r.min_weight)} … ${t(r.max_weight)}` });
  if (r.n_zero) out.push({ label: "Проб с нулевым весом", value: t(r.n_zero, 0) });
  return out;
}
