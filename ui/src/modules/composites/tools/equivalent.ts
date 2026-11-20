/** «Эквивалент металла» (ТЗ 8.2.2; ADR 0018 §7): Eq = g₁ + Σ g_e·(P_e·R_e·k_e)/(P₁·R₁·k₁) — a field of the source
 * table. P — the price, R — the recovery (0–1), k — the unit of the grade in the unit of the price (г/т and $/унц —
 * 1/31,1034768; % and $/т — 0,01; ppm = г/т; ppb — ×0,001). `others` — «ПОЛЕ цена извлечение» through «;» (the
 * assistant) or rows of the window; the window adds the units of every metal. A missing grade gives a missing Eq
 * unless «считать нулём». Pure. */
import { numberText, parseNumber, type ToolSpec } from "../params";

export interface OtherMetal {
  field: string;
  price: number;
  recovery: number;
}

/** «CU 9000 0,9; AG 25 0,8» → [{field, price, recovery}]; a part that is not «поле цена извлечение» — a reason. */
export function parseOthers(text: string): OtherMetal[] | string {
  const out: OtherMetal[] = [];
  for (const part of text.split(";").map((x) => x.trim()).filter(Boolean)) {
    const [field, p, r, ...rest] = part.split(/\s+/);
    const price = parseNumber(p ?? "");
    const recovery = parseNumber(r ?? "");
    if (!field || price === null || recovery === null || rest.length || price <= 0 || recovery <= 0 || recovery > 1)
      return `«Прочие металлы»: «${part}» — нужно «ПОЛЕ цена извлечение», цена > 0, извлечение 0–1`;
    out.push({ field, price, recovery });
  }
  return out.length ? out : "«Прочие металлы»: укажите хотя бы один";
}

/** [{field, price, recovery}] → «CU 9000 0,9; AG 25 0,8». */
export const othersText = (rows: readonly { field: string; price: string; recovery: string }[]): string =>
  rows
    .filter((r) => r.field || r.price.trim())
    .map((r) => `${r.field || "?"} ${r.price.trim().replace(/\s+/g, "") || "?"} ${r.recovery.trim().replace(/\s+/g, "") || "?"}`)
    .join("; ");

export const equivalent: ToolSpec = {
  id: "equivalent",
  command: "composite.equivalent",
  title: "Эквивалент металла",
  endpoint: "/equivalent",
  preview: () => "equivalent",
  source: { table: "one", fields: "none", domain: false },
  params: [
    { key: "base", label: "Основной металл", kind: "field", numeric: true },
    { key: "base_price", label: "Цена основного", kind: "number", min: 0 },
    { key: "base_recovery", label: "Извлечение основного", kind: "number", min: 0, max: 1, default: 0.9 },
    { key: "others", label: "Прочие металлы", kind: "text", hint: "ПОЛЕ цена извлечение; …, например «CU 9000 0,9»" },
    { key: "nan_zero", label: "Пустое содержание считать нулём", kind: "boolean", default: false },
    { key: "name", label: "Имя поля", kind: "text", optional: true, hint: "пусто — ‹основной›EQ" },
  ],
  required: ["set_id", "table", "base", "others"],
  writes: "fields",
  note: "Единицы и перевод k — по единицам полей (г/т и $/унц — 1/31,1034768; % и $/т — 0,01).",
  finish: (body) => {
    if (typeof body.base_price === "number" && body.base_price <= 0) return "«Цена основного»: нужна цена больше нуля";
    if (typeof body.base_recovery === "number" && body.base_recovery <= 0) return "«Извлечение основного»: нужно больше нуля";
    if (typeof body.others !== "string") return body;
    const others = parseOthers(body.others);
    if (typeof others === "string") return others;
    if (typeof body.base === "string" && others.some((o) => o.field.toLowerCase() === (body.base as string).toLowerCase())) return "«Прочие металлы»: основной металл не может быть среди прочих";
    return { ...body, others };
  },
};

// ---- units and k (ADR 0018 §7) ----

export const GRADE_UNITS = ["г/т", "ppm", "ppb", "%", "кг/т"] as const;
export const PRICE_UNITS = ["$/унц", "$/г", "$/кг", "$/т"] as const;
export type GradeUnit = (typeof GRADE_UNITS)[number];
export type PriceUnit = (typeof PRICE_UNITS)[number];

/** Grams of metal in a tonne of ore for 1 unit of the grade. */
const GRAMS_PER_TONNE: Record<GradeUnit, number> = { "г/т": 1, ppm: 1, ppb: 0.001, "%": 10_000, "кг/т": 1000 };
/** The price of a gram for a price of 1 in the unit. */
export const TROY_OUNCE_G = 31.1034768;
const PER_GRAM: Record<PriceUnit, number> = { "$/унц": 1 / TROY_OUNCE_G, "$/г": 1, "$/кг": 0.001, "$/т": 1e-6 };

/** The unit of a field as the table writes it («g/t», «г/т», «ppm», «%», «pct») → a grade unit; unknown — null. */
export function gradeUnit(text: string | null | undefined): GradeUnit | null {
  const t = (text ?? "").trim().toLowerCase().replace(/\s+/g, "");
  if (!t) return null;
  if (t === "г/т" || t === "g/t" || t === "гр/т" || t === "gpt") return "г/т";
  if (t === "ppm" || t === "мг/кг" || t === "mg/kg") return "ppm";
  if (t === "ppb" || t === "мг/т" || t === "mg/t") return "ppb";
  if (t === "%" || t === "pct" || t === "percent") return "%";
  if (t === "кг/т" || t === "kg/t") return "кг/т";
  return null;
}

/** The price unit of a grade unit by default: % — $/т, the rest — $/унц (ADR 0018 §7). */
export const defaultPriceUnit = (u: GradeUnit): PriceUnit => (u === "%" || u === "кг/т" ? "$/т" : "$/унц");

/** k: 1 unit of the grade in the unit of the price (г/т & $/унц → 1/31,1034768; % & $/т → 0,01). */
export const kFactor = (grade: GradeUnit, price: PriceUnit): number => GRAMS_PER_TONNE[grade] * PER_GRAM[price];

export interface Metal {
  field: string;
  price: number;
  recovery: number;
  unit: GradeUnit;
  priceUnit: PriceUnit;
}

/** How much of the base one unit of the other metal is worth: (P_e·R_e·k_e)/(P₁·R₁·k₁). */
export const metalFactor = (base: Metal, other: Metal): number => (other.price * other.recovery * kFactor(other.unit, other.priceUnit)) / (base.price * base.recovery * kFactor(base.unit, base.priceUnit));

/** Eq of one sample; a missing grade — null unless `nanZero` (then it counts as 0). */
export function equivalentValue(base: Metal, others: readonly Metal[], grades: Record<string, number | null | undefined>, nanZero = false): number | null {
  const g = (f: string): number | null => {
    const v = grades[f];
    return typeof v === "number" && Number.isFinite(v) ? v : nanZero ? 0 : null;
  };
  let eq = g(base.field);
  if (eq === null) return null;
  for (const o of others) {
    const v = g(o.field);
    if (v === null) return null;
    eq += v * metalFactor(base, o);
  }
  return eq;
}

/** «1 % CU = 1,399656 г/т AU-экв». */
export const factorText = (base: Metal, other: Metal): string => `1 ${other.unit} ${other.field} = ${numberText(metalFactor(base, other))} ${base.unit} ${base.field}-экв`;

/** The units of the window in the body of the job (`base_unit`, `base_price_unit`, `others[].unit`, `price_unit`):
 * the core takes them over the units of the fields. */
export function withUnits(body: Record<string, unknown>, base: { unit: GradeUnit; priceUnit: PriceUnit }, units: readonly { field: string; unit: GradeUnit; priceUnit: PriceUnit }[]): Record<string, unknown> {
  if (!Array.isArray(body.others)) return body;
  const others = (body.others as OtherMetal[]).map((o) => {
    const u = units.find((x) => x.field.toLowerCase() === o.field.toLowerCase());
    return u ? { ...o, unit: u.unit, price_unit: u.priceUnit } : o;
  });
  return { ...body, base_unit: base.unit, base_price_unit: base.priceUnit, others };
}
