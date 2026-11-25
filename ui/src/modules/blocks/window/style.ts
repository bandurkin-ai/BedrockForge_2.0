/**
 * The form of the colouring and the look of the window «Блочная модель · …» (ТЗ 6.8, 8.7.6; ADR 0023 §11, decisions
 * 16–19): the patches of the style (`StylePatch`, written with `styleOp` into `block_styles`) every control makes, in
 * the form the core's request wants (`colourBody`, `shellRequest` read the style back); «Взять из данных» (the
 * resolved range of the last answer); the rows of the breaks and of the categories with their colours and «скрыть»;
 * a palette filled into the colours; the limit of faces in millions; the words of a wrong value in Russian bound to
 * the field of the form (the same words as `colourIssue` and the command `blocks.style`). Pure: no React, no core.
 */
import { fieldNumber, num } from "../../../shell/props";
import { defaultColor, type Rgb } from "../../../viz/blocks/legend";
import type { ColourKind } from "../../../viz/blocks/decode";
import type { BlockField, BlockModelInfo, CategoryStats } from "../api";
import { colourIssue, kindFor, MAX_LIMIT, MIN_LIMIT, numberOf, type BlockStyle, type ColourStyle, type ShowInfo, type StylePatch } from "../display";
import { paletteById, paletteColours, PROJECT_PALETTE } from "./palettes";

/** The place of a wrong value in the form. */
export type FormField = "field" | "from" | "to" | "breaks" | "limit" | "edge_width" | "general";

export interface FormIssue {
  field: FormField;
  /** the message of the core's kind with its hint, in Russian */
  text: string;
}

const issue = (field: FormField, text: string): FormIssue => ({ field, text });

/** A number as it is typed in the form: a decimal comma. */
export const plain = (v: number): string => String(v).replace(".", ",");

export const hexOf = (rgb: Rgb): string => `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;

// ---------------------------------------------------------------------------------------------------- the words

/** What is wrong in the colouring of the style, with its place in the form; the words are those of `colourIssue`. */
export function colourIssueAt(c: ColourStyle, model: Pick<BlockModelInfo, "fields"> | undefined): FormIssue | null {
  const text = colourIssue(c, model);
  if (!text) return null;
  if (!c.field || text.startsWith("Поля «")) return issue("field", text);
  if (text.startsWith("Границы градаций")) return issue("breaks", text);
  if (text.startsWith("Граница шкалы")) return issue("to", text);
  if (text.startsWith("Логарифмическая")) return issue("from", text);
  return issue("general", text);
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; issue: FormIssue };

/** A bound of the scale as typed; «» — the core takes the minimum / maximum of the field (null). */
export function parseBound(text: string, which: "from" | "to"): Parsed<number | null> {
  if (!text.trim()) return { ok: true, value: null };
  const n = numberOf(text);
  if (n === null) return { ok: false, issue: issue(which, `Граница шкалы «${which === "from" ? "от" : "до"}»: нужно число. Например 0,5 — или оставьте пустым: возьмутся минимум и максимум поля.`) };
  return { ok: true, value: n };
}

/** The breaks as typed: numbers separated by «;» (or lines). */
export function parseBreaks(text: string): Parsed<number[]> {
  const parts = text.split(/[;\n]/).map((s) => s.trim()).filter(Boolean);
  const nums = parts.map((p) => numberOf(p));
  if (nums.some((n) => n === null)) return { ok: false, issue: issue("breaks", "Границы градаций: нужны числа через «;». Например 0,5; 1; 2.") };
  return { ok: true, value: nums as number[] };
}

export const breaksText = (b: readonly number[]): string => b.map(plain).join("; ");

/** The limit of faces in millions as typed («5», «0,5») → faces; the core takes 500 000 – 10 000 000. */
export function parseLimit(text: string): Parsed<number> {
  const n = numberOf(text);
  const faces = n === null ? NaN : Math.round(n * 1_000_000);
  if (!Number.isFinite(faces) || faces < MIN_LIMIT || faces > MAX_LIMIT) return { ok: false, issue: issue("limit", "Лимит граней — от 0,5 до 10 млн. Задайте число граней в этих пределах, например 5.") };
  return { ok: true, value: faces };
}

export const limitText = (limit: number): string => plain(limit / 1_000_000);

export function parseWidth(text: string): Parsed<number | null> {
  if (!text.trim()) return { ok: true, value: null };
  const n = numberOf(text);
  if (n === null || n <= 0 || n > 8) return { ok: false, issue: issue("edge_width", "Толщина рёбер — число от 0 до 8 пикселей. Например 1 — или оставьте пустым: по режиму.") };
  return { ok: true, value: n };
}

// ----------------------------------------------------------------------------------------- the colours and the classes

/** The colouring as the core will get it: a field of a number can be a scale or breaks, of a category — its codes. */
export const effectiveKind = (c: ColourStyle, field: BlockField | undefined): ColourKind => (c.field ? kindFor(c.kind, field) : "none");

/** The number of classes the colours are for: 256 buckets, one per class of the breaks, one per code. */
export function classCount(c: ColourStyle, field: BlockField | undefined): number {
  switch (effectiveKind(c, field)) {
    case "scale":
      return 256;
    case "classes":
      return c.breaks.length ? c.breaks.length + 1 : 0;
    case "categories":
      return Math.max(field?.labels?.length ?? 0, ...Object.keys(c.categories).map((k) => Number(k) + 1));
    default:
      return 0;
  }
}

/** The palette the window shows: the one chosen (its id is stored), else «Шкала проекта» (the name of a style nothing was chosen for is not an id). */
export function paletteShown(c: ColourStyle): string {
  return paletteById(c.palette) ? c.palette : PROJECT_PALETTE;
}

/** The colours of a palette written into the style: a scale — 256, breaks — one each, categories — one per code (their «показать» stays). */
export function paletteFill(c: ColourStyle, field: BlockField | undefined, id: string): Partial<ColourStyle> {
  const kind = effectiveKind(c, field);
  const n = classCount(c, field);
  const list = paletteColours(id, kind, n);
  if (kind === "categories") {
    const categories: ColourStyle["categories"] = {};
    for (let code = 0; code < n; code++) categories[String(code)] = { colour: list?.[code] ?? null, on: c.categories[String(code)]?.on ?? true };
    for (const [code, v] of Object.entries(c.categories)) if (!(code in categories)) categories[code] = v;
    return { palette: id, colours: [], categories };
  }
  return { palette: id, colours: list ?? [] };
}

/**
 * The colours after the structure of the colouring changed (a new field, a new kind, other breaks): a palette that was
 * chosen is filled again for the new classes, else the colours of the old structure go (they would colour other classes).
 */
export function recolour(next: ColourStyle, field: BlockField | undefined, before: ColourStyle): Partial<ColourStyle> {
  const shown = paletteShown(before);
  if (shown !== PROJECT_PALETTE) return paletteFill({ ...next, colours: [], categories: Object.fromEntries(Object.entries(next.categories).map(([k, v]) => [k, { ...v, colour: null }])) }, field, shown);
  return effectiveKind(next, field) === "categories" ? { colours: [] } : { colours: next.colours.length === classCount(next, field) ? next.colours : [] };
}

/** The field chosen for the colouring: a number is a scale (or the breaks it had), a category — its codes; the range, the breaks and the categories of the old field go. */
export function fieldPatch(name: string | null, field: BlockField | undefined, cur: BlockStyle): StylePatch {
  if (name === null) return { colour: { field: null, kind: "none", from: null, to: null, breaks: [], colours: [], categories: {} } };
  const kind = kindFor(cur.colour.kind === "none" ? "scale" : cur.colour.kind, field);
  const base: ColourStyle = { ...cur.colour, field: name, kind, from: null, to: null, breaks: [], categories: {} };
  return { colour: { field: name, kind, from: null, to: null, breaks: [], categories: {}, ...recolour(base, field, cur.colour) } };
}

/** The kind chosen («шкала» / «градации») for the field of a number. */
export function kindPatch(kind: ColourKind, field: BlockField | undefined, cur: BlockStyle): StylePatch {
  const base: ColourStyle = { ...cur.colour, kind };
  return { colour: { kind, ...recolour(base, field, cur.colour) } };
}

/** New breaks: the classes change, so a chosen palette is filled again. */
export function breaksPatch(breaks: number[], field: BlockField | undefined, cur: BlockStyle): StylePatch {
  const base: ColourStyle = { ...cur.colour, breaks };
  return { colour: { breaks, ...recolour(base, field, cur.colour) } };
}

// ---------------------------------------------------------------------------------------------- «Взять из данных»

/** Why the range cannot be taken from the data now, or null. The resolved range is that of the last answer of the core. */
export function takeReason(c: ColourStyle, field: BlockField | undefined, info: ShowInfo | undefined): string | null {
  if (!c.field || effectiveKind(c, field) !== "scale") return "Границы берутся для раскраски шкалой по числовому полю.";
  const h = info?.header;
  if (!h) return "Границы придут от ядра после показа: включите «Показать в 3D».";
  if (h.field !== c.field || h.colour.kind !== "scale") return "Показ ещё считается для другого поля: подождите обновления.";
  if (typeof h.colour.from !== "number" || typeof h.colour.to !== "number") return "Ядро не назвало границы шкалы: повторите показ.";
  return null;
}

/** The range the core resolved for the field (what the scale shows now), as a patch; null — see `takeReason`. */
export function takeFromData(c: ColourStyle, field: BlockField | undefined, info: ShowInfo | undefined): StylePatch | null {
  if (takeReason(c, field, info)) return null;
  const h = info!.header!;
  return { colour: { from: h.colour.from as number, to: h.colour.to as number } };
}

/** «от 0,07 до 4» — the resolved range as the person reads it. */
export function rangeText(info: ShowInfo | undefined): string | null {
  const c = info?.header?.colour;
  if (!c || c.kind !== "scale" || typeof c.from !== "number" || typeof c.to !== "number") return null;
  return `от ${fieldNumber(c.from, 4)} до ${fieldNumber(c.to, 4)}${c.log ? " (логарифмическая шкала)" : ""}`;
}

// ------------------------------------------------------------------------------------------------------------- rows

export interface ClassRow {
  k: number;
  from: number | null;
  to: number | null;
  colour: string;
  custom: boolean;
}

/** The classes of the breaks: «до b0», «от b0 до b1», …, «от bn», with their colours. */
export function classRows(c: ColourStyle): ClassRow[] {
  const n = c.breaks.length + 1;
  if (c.breaks.length < 1) return [];
  return Array.from({ length: n }, (_, k) => ({
    k,
    from: k === 0 ? null : c.breaks[k - 1],
    to: k === n - 1 ? null : c.breaks[k],
    colour: c.colours[k] ?? hexOf(defaultColor("classes", k, n)),
    custom: !!c.colours[k],
  }));
}

export const classText = (r: Pick<ClassRow, "from" | "to">): string => (r.from === null ? `до ${num(r.to ?? 0, 6)}` : r.to === null ? `от ${num(r.from, 6)}` : `от ${num(r.from, 6)} до ${num(r.to, 6)}`);

export interface CategoryRow {
  code: number;
  label: string;
  colour: string;
  custom: boolean;
  on: boolean;
  /** blocks with the category (statistics of the field); null — not read */
  blocks: number | null;
}

const isCategoryStats = (s: BlockField["stats"]): s is CategoryStats => !!s && "counts" in s;

/** The categories of the field with the colour and «показать» of each (the colours of the brand where none is chosen). */
export function categoryRows(field: BlockField | undefined, labels: readonly string[] | null | undefined, c: ColourStyle): CategoryRow[] {
  const names = labels && labels.length ? labels : (field?.labels ?? []);
  const n = Math.max(names.length, ...Object.keys(c.categories).map((k) => Number(k) + 1), 0);
  const counts = field && isCategoryStats(field.stats) ? field.stats.counts : null;
  return Array.from({ length: n }, (_, code) => {
    const own = c.categories[String(code)];
    return {
      code,
      label: names[code] ?? `код ${code}`,
      colour: own?.colour ?? hexOf(defaultColor("categories", code, n)),
      custom: !!own?.colour,
      on: own?.on !== false,
      blocks: counts && code < names.length && names[code] !== undefined ? (counts[code] ?? null) : null,
    };
  });
}

/** One category's colour or «показать» changed; the other categories stay. */
export function categoryPatch(c: ColourStyle, code: number, part: { colour?: string | null; on?: boolean }): StylePatch {
  const cur = c.categories[String(code)] ?? { colour: null, on: true };
  return { colour: { categories: { ...c.categories, [String(code)]: { colour: part.colour !== undefined ? part.colour : cur.colour, on: part.on !== undefined ? part.on : cur.on } } } };
}

/** All the categories shown (the core gets no `hidden`) or only those listed. */
export function categoriesShown(c: ColourStyle, codes: readonly number[], on: boolean): StylePatch {
  const categories = { ...c.categories };
  for (const code of codes) categories[String(code)] = { colour: categories[String(code)]?.colour ?? null, on };
  return { colour: { categories } };
}

/** One colour of a class of the breaks (or of the scale) changed. */
export function classColourPatch(c: ColourStyle, k: number, colour: string | null): StylePatch {
  const colours = c.colours.slice();
  while (colours.length <= k) colours.push(null);
  colours[k] = colour;
  return { colour: { colours } };
}

// ------------------------------------------------------------------------------------------------------------ status

/** «Упрощено: родители 4 × 4 × 4 (LOD 2), граней до упрощения 120 000 000» — or null when the model is drawn as it is. */
export function lodText(info: ShowInfo | undefined): string | null {
  const h = info?.header;
  if (!h || !h.simplified || info?.status === "error") return null;
  const side = 2 ** h.lod;
  return `Упрощено (LOD ${h.lod}): родители ${side} × ${side} × ${side}, граней до упрощения ${num(h.faces_raw, 0)}, показано ${num(h.faces, 0)}.`;
}

/** «Запрос 120 мс, запросов 3» */
export function timeText(info: ShowInfo | undefined): string | null {
  if (!info || info.ms === null) return null;
  return `Последний запрос ${num(info.ms, 0)} мс; запросов к ядру: ${info.requests}.`;
}
