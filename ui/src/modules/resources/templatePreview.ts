/**
 * The sample of a form for the window «Шаблоны форм отчёта» (ТЗ 8.10 «Формы»): the rows of a template with made-up round numbers, in the units
 * and with the decimals of the template — the order of the rows, the subtotals, the total, the sections, «Балансовые / Забалансовые». It follows
 * `forms.build` of the core (`core/bf_core/resources/forms.py`): the titles of the columns, the subtotals and the total are SUMS of the tonnes,
 * the volume and the metal, ḡ = Σ t·g / Σ t; the metal is by the base unit of the element (g for g/t, ppm, ppb; t for %). The report is not
 * recalculated: a template is the show only (ADR 0027 решение 17). Pure.
 */
import { BASE_OF, METAL_UNITS, TONNES_UNITS, VOLUME_UNITS, roleTitle, type MetalBase, type Metric, type TemplateBody } from "./templates";

export interface SampleElement {
  name: string;
  /** the unit of the grade: г/т, g/t, ppm, ppb, % */
  unit: string;
}

export const SAMPLE_ELEMENTS: SampleElement[] = [
  { name: "Au", unit: "г/т" },
  { name: "Cu", unit: "%" },
];

export interface PreviewColumn {
  key: string;
  title: string;
  group: string | null;
  unit: string;
  decimals: number;
}

export interface PreviewRow {
  label: string;
  type: "group" | "subtotal" | "total";
  cells: string[];
}

export interface PreviewSection {
  title: string | null;
  rows: PreviewRow[];
}

export interface Preview {
  columns: PreviewColumn[];
  sections: PreviewSection[];
}

/** The number as the core shows it: the decimal comma, a no-break space between the thousands, rounded to `decimals`. */
export function formatNumber(v: number | null, decimals: number): string {
  if (v === null || !Number.isFinite(v)) return "";
  const text = v.toLocaleString("ru-RU", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return /^[-−]?0(?:,0+)?$/.test(text) ? text.replace(/^[-−]/, "") : text; // a negative that rounds to zero has no sign
}

interface Agg {
  tonnes: number;
  volume: number;
  /** Σ t·g by the element */
  tg: number[];
}

const add = (a: Agg, b: Agg): Agg => ({ tonnes: a.tonnes + b.tonnes, volume: a.volume + b.volume, tg: a.tg.map((x, i) => x + b.tg[i]) });
const empty = (n: number): Agg => ({ tonnes: 0, volume: 0, tg: Array.from({ length: n }, () => 0) });

/** The metal in the unit of the template from Σ t·g: g/t → grams; % → tonnes (the grade is a share ×100). */
function metalOf(tg: number, base: MetalBase, unit: string): number {
  const inBase = base === "t" ? tg / 100 : tg; // tonnes of metal for %, grams for g/t (t in tonnes: t·g/t = g)
  return inBase * (base === "t" ? 1e6 : 1) * (METAL_UNITS[unit]?.[1] ?? 1);
}

/** The columns of the form in the order of the template (the core: `_columns`): the whole set once per table of the balance. */
export function previewColumns(t: TemplateBody, elements: readonly SampleElement[] = SAMPLE_ELEMENTS): PreviewColumn[] {
  const parts: [string | null, string | null][] = t.balance
    ? [
        ["balance", "Балансовые"],
        ["offbalance", "Забалансовые"],
      ]
    : [[null, null]];
  const cols: PreviewColumn[] = [];
  for (const [part, group] of parts) {
    const pre = part ? `${part}:` : "";
    let per = false;
    for (const m of t.columns) {
      if (m === "tonnes") {
        const u = TONNES_UNITS[t.units.tonnes]?.[0] ?? t.units.tonnes;
        cols.push({ key: `${pre}tonnes`, title: `Руда, ${u}`, group, unit: u, decimals: t.decimals.tonnes });
      } else if (m === "volume") {
        const u = VOLUME_UNITS[t.units.volume]?.[0] ?? t.units.volume;
        cols.push({ key: `${pre}volume`, title: `Объём, ${u}`, group, unit: u, decimals: t.decimals.volume });
      } else if (!per) {
        per = true;
        for (const el of elements) {
          const base = BASE_OF[el.unit.trim().toLocaleLowerCase("ru-RU")] ?? null;
          for (const m2 of t.columns) {
            if (m2 === "grade") cols.push({ key: `${pre}grade:${el.name}`, title: el.unit ? `${el.name}, ${el.unit}` : el.name, group, unit: el.unit, decimals: t.decimals.grade });
            else if (m2 === "metal" && base !== null) {
              const u = METAL_UNITS[t.units.metal[base]]?.[0] ?? t.units.metal[base];
              cols.push({ key: `${pre}metal:${el.name}`, title: `Металл ${el.name}, ${u}`, group, unit: u, decimals: t.decimals.metal });
            }
          }
        }
      }
    }
  }
  return cols;
}

/** The sample rows of a section: the row `i` — round numbers that grow with `i` (t, m³ by the density 2,7, the grades), `scale` — the section's size. */
function sampleRow(i: number, scale: number, elements: readonly SampleElement[]): Agg {
  const tonnes = 1_250_000 * (i + 1) * scale;
  return { tonnes, volume: tonnes / 2.7, tg: elements.map((el, k) => tonnes * (el.unit === "%" ? 0.35 + 0.12 * i + 0.05 * k : 0.8 + 0.45 * i + 0.1 * k)) };
}

function cellsOf(a: Agg, t: TemplateBody, elements: readonly SampleElement[], part: string | null): string[] {
  const k = part === "offbalance" ? 0.3 : 1; // the sample of the off-balance: less ore, poorer
  const g = part === "offbalance" ? 0.55 : 1;
  const out: string[] = [];
  let per = false;
  for (const m of t.columns) {
    if (m === "tonnes") out.push(formatNumber(a.tonnes * k * (TONNES_UNITS[t.units.tonnes]?.[1] ?? 1), t.decimals.tonnes));
    else if (m === "volume") out.push(formatNumber(a.volume * k * (VOLUME_UNITS[t.units.volume]?.[1] ?? 1), t.decimals.volume));
    else if (!per) {
      per = true;
      elements.forEach((el, e) => {
        const base = BASE_OF[el.unit.trim().toLocaleLowerCase("ru-RU")] ?? null;
        for (const m2 of t.columns) {
          if (m2 === "grade") out.push(a.tonnes > 0 ? formatNumber((a.tg[e] / a.tonnes) * g, t.decimals.grade) : "");
          else if (m2 === "metal" && base !== null) out.push(formatNumber(metalOf(a.tg[e] * k * g, base, t.units.metal[base]), t.decimals.metal));
        }
      });
    }
  }
  return out;
}

/** The sample of the form: the rows of the template with a subtotal after its last member, then the total; sections when the template has them. */
export function previewForm(t: TemplateBody, elements: readonly SampleElement[] = SAMPLE_ELEMENTS): Preview {
  const columns = previewColumns(t, elements);
  const parts: (string | null)[] = t.balance ? ["balance", "offbalance"] : [null];
  const sections = t.section ? [1, 0.6] : [1];
  const taken = new Set<string>();
  const present = (s: { members: string[] }) => s.members.filter((m) => t.labels.includes(m));
  const out: PreviewSection[] = sections.map((scale, si) => {
    const base = new Map(t.labels.map((l, i) => [l, sampleRow(i, scale, elements)] as const));
    const rows: { label: string; type: PreviewRow["type"]; agg: Agg }[] = [];
    let total = empty(elements.length);
    for (const l of t.labels) {
      const agg = base.get(l) as Agg;
      total = add(total, agg);
      rows.push({ label: l, type: "group", agg });
      for (const s of t.subtotals) {
        const m = present(s);
        if (m.length >= 2 && m[m.length - 1] === l && !taken.has(`${si}:${s.label}`)) {
          taken.add(`${si}:${s.label}`);
          rows.push({ label: s.label, type: "subtotal", agg: m.reduce((a, x) => add(a, base.get(x) as Agg), empty(elements.length)) });
        }
      }
    }
    if (t.total !== null) rows.push({ label: t.total, type: "total", agg: total });
    return {
      title: t.section ? `${roleTitle(t.section)[0].toLocaleUpperCase("ru-RU")}${roleTitle(t.section).slice(1)} ${si + 1}` : null,
      rows: rows.map((r) => ({ label: r.label, type: r.type, cells: parts.flatMap((p) => cellsOf(r.agg, t, elements, p)) })),
    };
  });
  return { columns, sections: out };
}

/** The metrics of a template as one line: «тоннаж, содержание, металл». */
export const columnsText = (cols: readonly Metric[]): string => cols.map((c) => ({ tonnes: "тоннаж", volume: "объём", grade: "содержание", metal: "металл" })[c]).join(", ");
