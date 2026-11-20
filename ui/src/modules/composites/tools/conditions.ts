/**
 * «Анализ кондиций» (Д-26 «Анализ кондиций», Т8.2-20; ADR 0018 §12, решение 23): Сб × Мрт × Мпп (до 200 вариантов)
 * тем же ядром, что «Рудные интервалы (ГКЗ)» — a result without a table, the variants in its report. Here: the
 * parameters and the body of the job (the common last step `oreFinish`, the lists without repeats in ascending
 * order, `exclude {field, values}` — samples left out by a field, e.g. inside the pit after coding by wireframes),
 * the number of variants and why more than 200 is not started, the variants of a report made safe, the table of
 * the form И1, сл. 24 with the grade of the прирезка between neighbouring cutoffs (сл. 26) and the mark of
 * neighbours whose Σ M differ not by 10–15 % (сл. 25), the CSV of the table. Pure.
 */
import { parseList, parseNames, type FormValues, type ToolSpec } from "../params";
import { num, ORE_COMMON, oreFinish } from "./ore";

export const MAX_VARIANTS = 200;
/** Neighbouring cutoffs should change Σ M by 10–15 % (И1, сл. 25). */
export const STEP_MIN_PCT = 10;
export const STEP_MAX_PCT = 15;

const LISTS = ["cutoffs", "m_min", "m_waste_max"] as const;

export const conditions: ToolSpec = {
  id: "conditions",
  command: "composite.conditions_analysis",
  title: "Анализ кондиций",
  endpoint: "/conditions",
  preview: null,
  source: { table: "one", fields: "one", domain: true },
  params: [
    { key: "cutoffs", label: "Бортовые содержания Сб", kind: "list", min: 0, hint: "через «;», например «0,5; 1; 1,5»" },
    { key: "m_min", label: "Мин. мощности Мрт", kind: "list", unit: "м", min: 0, hint: "через «;», например «1; 3»" },
    { key: "m_waste_max", label: "Макс. мощности прослоя Мпп", kind: "list", unit: "м", min: 0, hint: "через «;», например «3; 5»" },
    ...ORE_COMMON,
    { key: "exclude_field", label: "Исключить пробы по полю", kind: "field", optional: true, hint: "например CODE после «Кодировать каркасами» — пробы внутри карьера" },
    { key: "exclude_values", label: "Значения для исключения", kind: "text", optional: true, hint: "через «,», например «Карьер»", when: (v) => typeof v.exclude_field === "string" && v.exclude_field.trim() !== "" },
  ],
  required: ["set_id", "table", "field", "cutoffs", "m_min", "m_waste_max"],
  writes: "report",
  note: `Каждый вариант Сб × Мрт × Мпп считается как отдельный запуск «Рудные интервалы (ГКЗ)» (МС = Сб·Мрт, Смин = Сб); не больше ${MAX_VARIANTS} вариантов.`,
  finish: conditionsFinish,
};

/** «Вариантов 300: не больше 200» — the reason of the footer; the window adds what to do. */
export const tooManyText = (n: number): string => `Вариантов ${n}: не больше ${MAX_VARIANTS}`;

const uniqSorted = (v: unknown): number[] | null => (Array.isArray(v) ? [...new Set(v.filter((x): x is number => typeof x === "number" && Number.isFinite(x)))].sort((a, b) => a - b) : null);

/**
 * The last step of the body: the lists without repeats in ascending order, more than 200 variants — why not;
 * `exclude_field` + «Значения» → `exclude {field, values}` (values as typed, the core compares them as text —
 * a number of a numeric field by its value); then the common step of Д-26 (`thickness`, `contacts_values`).
 */
export function conditionsFinish(body: Record<string, unknown>): Record<string, unknown> | string {
  const out: Record<string, unknown> = { ...body };
  let n = 1;
  for (const k of LISTS) {
    const list = uniqSorted(out[k]);
    if (!list || !list.length) return `Задайте ${k === "cutoffs" ? "Сб" : k === "m_min" ? "Мрт" : "Мпп"} — хотя бы одно значение`;
    out[k] = list;
    n *= list.length;
  }
  if (n > MAX_VARIANTS) return tooManyText(n);
  const field = typeof out.exclude_field === "string" ? out.exclude_field.trim() : "";
  const values = typeof out.exclude_values === "string" ? parseNames(out.exclude_values) : [];
  delete out.exclude_field;
  delete out.exclude_values;
  if (field) {
    if (!values.length) return "«Значения для исключения»: перечислите значения поля через «,»";
    out.exclude = { field, values };
  }
  return oreFinish(out);
}

export interface VariantCount {
  /** lengths of Сб, Мрт, Мпп without repeats; null — the list is not given or not numbers */
  sizes: [number | null, number | null, number | null];
  /** the product; null — a list is missing */
  total: number | null;
}

/** The number of variants as typed (before the check of the whole form): «3 × 2 × 2 = 12». */
export function variantCount(values: FormValues): VariantCount {
  const size = (k: string): number | null => {
    const t = values[k];
    if (typeof t !== "string" || !t.trim()) return null;
    const l = parseList(t);
    return l ? new Set(l).size : null;
  };
  const sizes: VariantCount["sizes"] = [size("cutoffs"), size("m_min"), size("m_waste_max")];
  return { sizes, total: sizes.every((x) => x !== null) ? sizes.reduce<number>((a, b) => a * (b as number), 1) : null };
}

export function variantCountText(c: VariantCount): string {
  const s = c.sizes.map((x) => (x === null ? "?" : String(x))).join(" × ");
  return c.total === null ? `Вариантов: ${s}` : `Вариантов: ${s} = ${c.total}`;
}

// ---- the variants of a report ----

/** One variant of the report (only `ore`, NESTED = 0); a value the core leaves out is null, never zero. */
export interface Variant {
  cutoff: number;
  m_min: number;
  m_waste_max: number;
  count: number | null;
  /** Σ M, м */
  m_sum: number | null;
  /** Σ M / число */
  m_mean: number | null;
  /** Σ MC / Σ M */
  c_mean: number | null;
  mc_sum: number | null;
}

const fin = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function variant(raw: unknown): Variant | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const cutoff = fin(r.cutoff);
  const mMin = fin(r.m_min);
  const mWaste = fin(r.m_waste_max);
  if (cutoff === null || mMin === null || mWaste === null) return null;
  const count = fin(r.count);
  const mSum = fin(r.m_sum ?? r.m);
  const mcSum = fin(r.mc_sum ?? r.mc);
  const mMean = fin(r.m_mean) ?? (mSum !== null && count ? mSum / count : null);
  const cMean = fin(r.c_mean ?? r.c) ?? (mSum !== null && mcSum !== null && mSum > 0 ? mcSum / mSum : null);
  return { cutoff, m_min: mMin, m_waste_max: mWaste, count, m_sum: mSum, m_mean: mMean, c_mean: cMean, mc_sum: mcSum };
}

/** The variants of the answer of the job or of `report` of the result: `variants` or `rows` (also under `report`). */
export function toVariants(raw: unknown): Variant[] {
  if (!raw || typeof raw !== "object") return [];
  const r = raw as Record<string, unknown>;
  const list = Array.isArray(r.variants) ? r.variants : Array.isArray(r.rows) ? r.rows : r.report && typeof r.report === "object" ? null : [];
  if (list === null) return toVariants(r.report);
  return list.map(variant).filter((v): v is Variant => v !== null);
}

/** The unit of the grade field the report names («г/т»), null — not known. */
export function variantsUnit(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.unit === "string" && r.unit.trim()) return r.unit.trim();
  return r.report ? variantsUnit(r.report) : null;
}

// ---- the table of the form И1, сл. 24 ----

export interface VariantRow extends Variant {
  key: string;
  /** the first row of its group (Мрт, Мпп) */
  first: boolean;
  /** (Σ M₁ − Σ M₂) / Σ M₁, % — to the previous (lower) cutoff of the group; null — first or no Σ M */
  step_pct: number | null;
  /** (Σ MC₁ − Σ MC₂) / (Σ M₁ − Σ M₂) — the grade of the прирезка between neighbouring cutoffs (сл. 26) */
  c_trim: number | null;
  /** why the row is marked; null — not marked */
  mark: string | null;
}

/** Rows grouped by (Мрт, Мпп), within a group by Сб ascending; the neighbour is the previous (lower) cutoff. */
export function variantRows(variants: readonly Variant[]): VariantRow[] {
  const sorted = [...variants].sort((a, b) => a.m_min - b.m_min || a.m_waste_max - b.m_waste_max || a.cutoff - b.cutoff);
  const out: VariantRow[] = [];
  sorted.forEach((v, i) => {
    const prev = i > 0 && sorted[i - 1].m_min === v.m_min && sorted[i - 1].m_waste_max === v.m_waste_max ? sorted[i - 1] : null;
    let step: number | null = null;
    let trim: number | null = null;
    let mark: string | null = null;
    if (prev && prev.m_sum !== null && v.m_sum !== null) {
      if (prev.m_sum > 0) step = ((prev.m_sum - v.m_sum) / prev.m_sum) * 100;
      const dm = prev.m_sum - v.m_sum;
      if (dm > 0 && prev.mc_sum !== null && v.mc_sum !== null) trim = (prev.mc_sum - v.mc_sum) / dm;
      if (dm < 0) mark = `Σ M больше, чем при Сб ${num(prev.cutoff, 6)}: при росте борта мощность не должна расти — проверьте данные`;
      else if (step !== null && (step < STEP_MIN_PCT || step > STEP_MAX_PCT))
        mark = `Σ M отличается от варианта Сб ${num(prev.cutoff, 6)} на ${num(step, 1)} % — рекомендуется ${STEP_MIN_PCT}–${STEP_MAX_PCT} % (И1, сл. 25)`;
    }
    out.push({ ...v, key: `${v.m_min}|${v.m_waste_max}|${v.cutoff}`, first: prev === null, step_pct: step, c_trim: trim, mark });
  });
  return out;
}

/** The columns of the table and of its CSV (И1, сл. 24 + сл. 25–26). */
export function variantColumns(unit: string | null): { key: string; title: string }[] {
  const u = unit ? `, ${unit}` : "";
  return [
    { key: "cutoff", title: `Сб${u}` },
    { key: "m_min", title: "Мрт, м" },
    { key: "m_waste_max", title: "Мпп, м" },
    { key: "count", title: "Число интервалов" },
    { key: "c_mean", title: `Ср. содержание (ΣMC/ΣM)${u}` },
    { key: "m_sum", title: "Σ M, м" },
    { key: "m_mean", title: "Ср. M, м" },
    { key: "mc_sum", title: "Σ MC" },
    { key: "step_pct", title: "Изменение Σ M, %" },
    { key: "c_trim", title: `Содержание в прирезке${u}` },
  ];
}

/** The text of a cell: «—» for a missing value (never «0»). */
export function cellText(row: VariantRow, key: string): string {
  const v = (row as unknown as Record<string, unknown>)[key];
  if (key === "count") return typeof v === "number" ? String(v) : "—";
  const digits = key === "c_mean" || key === "c_trim" ? 3 : key === "step_pct" ? 1 : key === "cutoff" || key === "m_min" || key === "m_waste_max" ? 6 : 2;
  return num(v, digits);
}

/** The CSV of the table: «;», decimal comma, the mark as the last column (the bridge writes the BOM). */
export function variantsCsv(rows: readonly VariantRow[], unit: string | null): string {
  const cols = variantColumns(unit);
  const esc = (s: string) => (/[;"\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const lines = [[...cols.map((c) => c.title), "Пометка"].map(esc).join(";")];
  for (const r of rows) lines.push([...cols.map((c) => { const t = cellText(r, c.key); return t === "—" ? "" : t; }), r.mark ?? ""].map(esc).join(";"));
  return `${lines.join("\r\n")}\r\n`;
}

/** «Анализ кондиций · AU.csv». */
export const variantsFileName = (field: string | null): string => `${`Анализ кондиций${field ? ` · ${field}` : ""}`.replace(/[\\/:*?"<>|]+/g, "_")}.csv`;
