/**
 * The step «Блочная модель» of «Импорт файлов» (ТЗ 7.31, 8.7.2; ADR 0023 решения 5–7; Д-13): what the core tells of a
 * block file (`files[].blocks` of `POST /import/objects/inspect`, docs/progress/e17-api.md §5) and what the user
 * answers (`options[<путь>].blocks`): the role of every column (centre X / Y / Z, size DX / DY / DZ, a field and
 * what it is — grade, density, FRAC, code, class — or «пропустить»), the units, the codes of «пусто», the grid
 * (corner, parent size, counts, the turn about Z, the division into sub-blocks, the axes the centres are written in),
 * the delimiter of a text file, the name of the model. The answers are typed texts (a comma and a point are numbers);
 * `blocksWire` makes the body, `draftErrors` tells what is typed wrong before anything is sent. A missing value is
 * never a zero: what is not typed is not sent, and the core finds it in the file. Pure: no React.
 */
import { parseNumber } from "../../importing/numbers";

export type Geo = "x" | "y" | "z" | "dx" | "dy" | "dz";
export const GEO: readonly Geo[] = ["x", "y", "z", "dx", "dy", "dz"];
export type FieldRole = "grade" | "density" | "frac" | "code" | "class";
/** What the user says a column is. `auto` — what the core found; `field` — a field with the role the core finds. */
export type Choice = "auto" | Geo | "field" | "skip" | FieldRole;

export interface BlocksColumn {
  name: string;
  type: "number" | "text";
  role: Geo | null;
}

export interface BlocksField {
  name: string;
  column: string;
  kind: "number" | "category";
  unit: string | null;
  role: FieldRole | null;
  /** one value in the whole file: it varies nowhere */
  constant: boolean;
  /** the codes of «пусто» met in the column */
  empty: number[];
}

/** `GridV2.to_meta()` of the core. */
export interface GridMeta {
  corner: [number, number, number];
  counts: [number, number, number];
  size?: [number, number, number];
  div: [number, number, number];
  rotation: number;
  order: string;
}

export interface Check3 {
  ok: boolean;
  /** the largest distance of the three centres of the file from the computed ones, m */
  dist: number;
  /** the rows of the file (0-based) checked: the first, the middle, the last */
  rows: number[];
}

export interface BlocksError {
  code: string;
  message: string;
  hint: string;
  /** `blocks_unaligned`: how many blocks and the first rows (0-based) */
  count?: number;
  rows?: number[];
}

export interface BlocksInfo {
  format: "dat" | "csv" | "csv_micromine" | "dm" | "fbm" | string;
  label: string;
  rows: number;
  rows_exact: boolean;
  encoding: string | null;
  columns: BlocksColumn[];
  roles: Record<Geo, string | null>;
  grid: GridMeta | null;
  sub: boolean;
  parents: number;
  empty_candidates: number[];
  units: Record<string, string>;
  fields: BlocksField[];
  check3: Check3 | null;
  warnings: string[];
  error: BlocksError | null;
}

/** What the user typed for one file; the three numbers of a triple are texts, empty — «как в файле». */
export interface BlocksDraft {
  columns: Record<string, Choice>;
  units: Record<string, string>;
  corner: [string, string, string];
  size: [string, string, string];
  counts: [string, string, string];
  div: [string, string, string];
  rotation: string;
  centres: "project" | "model";
  /** the codes of «пусто», as typed: «-99; -999,5» */
  empty: string;
  /** a text file: "" — found by the file */
  delimiter: string;
}

const NONE: [string, string, string] = ["", "", ""];

export function emptyDraft(): BlocksDraft {
  return { columns: {}, units: {}, corner: [...NONE], size: [...NONE], counts: [...NONE], div: [...NONE], rotation: "", centres: "project", empty: "", delimiter: "" };
}

export const patchDraft = (d: BlocksDraft | null | undefined, patch: Partial<BlocksDraft>): BlocksDraft => ({ ...(d ?? emptyDraft()), ...patch });

export const GEO_TITLES: Record<Geo, string> = { x: "Центр X", y: "Центр Y", z: "Центр Z", dx: "Размер DX", dy: "Размер DY", dz: "Размер DZ" };
export const FIELD_ROLE_TITLES: Record<FieldRole, string> = { grade: "Содержание", density: "Плотность", frac: "FRAC (доля)", code: "Код (категория)", class: "Класс" };

/** The options of the select of a column, in the order the user reads them; a text column cannot be a number. */
export function choicesOf(column: BlocksColumn): { id: Choice; title: string }[] {
  const out: { id: Choice; title: string }[] = [{ id: "auto", title: "Как определено" }];
  if (column.type === "number") for (const g of GEO) out.push({ id: g, title: GEO_TITLES[g] });
  out.push({ id: "field", title: "Поле" });
  if (column.type === "number") for (const r of ["grade", "density", "frac"] as const) out.push({ id: r, title: FIELD_ROLE_TITLES[r] });
  for (const r of ["code", "class"] as const) out.push({ id: r, title: FIELD_ROLE_TITLES[r] });
  out.push({ id: "skip", title: "Пропустить" });
  return out;
}

/** What the core made of a column: a centre or a size, a field with its role, or nothing (the column is left out). */
export function foundChoice(info: BlocksInfo, column: string): Choice {
  const col = info.columns.find((c) => c.name === column);
  if (col?.role) return col.role;
  const field = info.fields.find((f) => f.column === column);
  if (!field) return "skip";
  return field.role ?? "field";
}

/** The choice shown for a column: the user's, else what the core found. */
export function shownChoice(info: BlocksInfo, draft: BlocksDraft | null | undefined, column: string): Choice {
  const mine = draft?.columns[column];
  return mine && mine !== "auto" ? mine : foundChoice(info, column);
}

// ---- the numbers of the grid --------------------------------------------------------------------------------

const TRIPLES = ["corner", "size", "counts", "div"] as const;
export type TripleKey = (typeof TRIPLES)[number];
export const TRIPLE_TITLES: Record<TripleKey, string> = { corner: "Начало сетки, м", size: "Размер родителя, м", counts: "Число ячеек", div: "Деление на субблоки" };
const INTEGER: Record<TripleKey, boolean> = { corner: false, size: false, counts: true, div: true };
export const MAX_DIV = 128;

export interface DraftErrors {
  corner?: string;
  size?: string;
  counts?: string;
  div?: string;
  rotation?: string;
  empty?: string;
}

/** «-99; -999,5» → the numbers; a text that is not a number is told. */
export function parseCodes(text: string): { codes: number[]; error: string | null } {
  const parts = text.split(/[;\n]+/).map((p) => p.trim()).filter(Boolean);
  const codes: number[] = [];
  for (const p of parts) {
    const v = parseNumber(p);
    if (v === undefined || v === null) return { codes: [], error: `«${p}» — не число. Коды пустого пишите числами через «;», например −99.` };
    codes.push(v);
  }
  return { codes: [...new Set(codes)], error: null };
}

/** A triple typed: all three or none (a part of a corner is no corner). */
function parseTriple(key: TripleKey, texts: readonly string[]): { value: number[] | null; error: string | null } {
  const parsed = texts.map((t) => parseNumber(t));
  const given = parsed.filter((v) => v !== null).length;
  if (given === 0) return { value: null, error: null };
  const title = TRIPLE_TITLES[key].replace(/, м$/, "");
  const bad = texts.findIndex((_, i) => parsed[i] === undefined);
  if (bad >= 0) return { value: null, error: `«${texts[bad].trim()}» — не число. Введите ${title.toLowerCase()} числами, например 12,5.` };
  if (given < 3) return { value: null, error: `${title}: заполните все три значения (X, Y, Z) или оставьте пустыми — тогда значения берутся из файла.` };
  const v = parsed as number[];
  if (INTEGER[key] && !v.every((x) => Number.isInteger(x) && x >= 1)) return { value: null, error: `${title}: нужны целые числа от 1.` };
  if (key === "size" && !v.every((x) => x > 0)) return { value: null, error: `${title}: размер должен быть больше нуля.` };
  if (key === "div" && !v.every((x) => x <= MAX_DIV)) return { value: null, error: `${title}: не больше ${MAX_DIV} по оси (степени двойки: 1, 2, 4 … 128).` };
  return { value: v, error: null };
}

export function parseRotation(text: string): { value: number | null; error: string | null } {
  const v = parseNumber(text);
  if (v === undefined) return { value: null, error: `«${text.trim()}» — не число. Введите угол в градусах, например 30.` };
  if (v === null) return { value: null, error: null };
  if (!(v > -180 && v <= 180)) return { value: null, error: "Угол поворота — от −180° (не включая) до 180°, против часовой стрелки при виде сверху." };
  return { value: v, error: null };
}

export function draftErrors(d: BlocksDraft | null | undefined): DraftErrors {
  const out: DraftErrors = {};
  if (!d) return out;
  for (const k of TRIPLES) {
    const e = parseTriple(k, d[k]).error;
    if (e) out[k] = e;
  }
  const r = parseRotation(d.rotation).error;
  if (r) out.rotation = r;
  const c = parseCodes(d.empty).error;
  if (c) out.empty = c;
  return out;
}

export const hasErrors = (e: DraftErrors): boolean => Object.keys(e).length > 0;

// ---- the body ------------------------------------------------------------------------------------------------------

export interface BlocksWire {
  roles?: Partial<Record<Geo, string>>;
  grid?: { corner?: number[]; size?: number[]; counts?: number[]; div?: number[]; rotation?: number };
  centres?: "model";
  empty?: number[];
  fields?: Record<string, { unit?: string; role?: FieldRole; skip?: boolean }>;
  delimiter?: string;
}

const isGeo = (c: Choice): c is Geo => (GEO as readonly string[]).includes(c);

/**
 * `options[<путь>].blocks`: only what the user said. A centre or a size set on a column goes to `roles`; a column
 * that was a centre or a size and is said to be a field, skipped or given a role is released from it (`""` — no such
 * column). A field's unit and role go to `fields`; «Пропустить» — `skip`. `{}` — «загрузить как блочную модель» with
 * everything found by the core. A draft with a wrong text gives what is right of it (the window says the rest).
 */
export function blocksWire(draft: BlocksDraft, info: BlocksInfo | null): BlocksWire {
  const out: BlocksWire = {};
  const roles: Partial<Record<Geo, string>> = {};
  const fields: NonNullable<BlocksWire["fields"]> = {};
  for (const [column, choice] of Object.entries(draft.columns)) {
    if (choice === "auto") continue;
    if (isGeo(choice)) {
      roles[choice] = column;
      continue;
    }
    const was = info?.columns.find((c) => c.name === column)?.role;
    if (was) roles[was] = roles[was] ?? "";
    if (choice === "skip") fields[column] = { ...fields[column], skip: true };
    else if (choice !== "field") fields[column] = { ...fields[column], role: choice };
  }
  for (const [column, unit] of Object.entries(draft.units)) {
    if (unit.trim() && draft.columns[column] !== "skip") fields[column] = { ...fields[column], unit: unit.trim() };
  }
  if (Object.keys(roles).length) out.roles = roles;
  if (Object.keys(fields).length) out.fields = fields;
  const grid: NonNullable<BlocksWire["grid"]> = {};
  for (const k of TRIPLES) {
    const v = parseTriple(k, draft[k]).value;
    if (v) grid[k] = v;
  }
  const turn = parseRotation(draft.rotation).value;
  if (turn !== null) grid.rotation = turn;
  if (Object.keys(grid).length) out.grid = grid;
  if (draft.centres === "model") out.centres = "model";
  const codes = parseCodes(draft.empty);
  if (!codes.error && codes.codes.length) out.empty = codes.codes;
  if (draft.delimiter) out.delimiter = draft.delimiter;
  return out;
}

// ---- the texts -----------------------------------------------------------------------------------------------------

export const n = (v: number, digits = 3): string => v.toLocaleString("ru-RU", { maximumFractionDigits: digits }).replace("-", "\u2212");
const SUPER: Record<string, string> = { "-": "\u207b", "0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074", "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079" };

/** «2,4·10⁻¹⁰»: a small distance as a power of ten (a plain number would be a row of zeros). */
function small(v: number): string {
  if (v === 0) return "0";
  if (v >= 1e-3) return n(v, 6);
  const [mantissa, exponent] = v.toExponential(1).split("e");
  return `${mantissa.replace(".", ",")}\u00b710${[...String(Number(exponent))].map((c) => SUPER[c] ?? c).join("")}`;
}
const triple = (v: readonly number[], sep = " × "): string => v.map((x) => n(x)).join(sep);

/** «443 × 558 × 102 родителей по 10 × 10 × 10 м; начало 23 436 265,92; …; поворот 30°; деление 8 × 4 × 4» */
export function gridText(g: GridMeta): string {
  const parts = [`${triple(g.counts)} родителей${g.size ? ` по ${triple(g.size)} м` : ""}`, `начало ${triple(g.corner, "; ")}`];
  if (g.rotation) parts.push(`поворот ${n(g.rotation)}°`);
  if (g.div.some((d) => d > 1)) parts.push(`деление на субблоки ${triple(g.div)}`);
  return parts.join("; ");
}

/** The check of three centres (ТЗ 8.7.2): the first, the middle and the last block of the file against the grid. */
export function check3Text(info: BlocksInfo): { ok: boolean; text: string } | null {
  const c = info.check3;
  if (!c) return null;
  const rows = c.rows.map((r) => n(r + 1, 0)).join(", ");
  const dist = small(c.dist);
  return c.ok
    ? { ok: true, text: `Три центра файла (строки ${rows}) совпали с сеткой: расхождение ${dist} м.` }
    : { ok: false, text: `Три центра файла (строки ${rows}) не совпали с сеткой: расхождение ${dist} м. Проверьте начало, размер родителя и угол поворота.` };
}

/** The turn is read from the file («предварительно» — № 64: no sample of a turned model of Datamine or Micromine). */
export function turnNote(info: BlocksInfo, draft: BlocksDraft | null | undefined): string | null {
  const typed = parseRotation(draft?.rotation ?? "").value;
  const turn = typed ?? info.grid?.rotation ?? 0;
  if (!turn) return null;
  const how = info.format === "dm" || info.format === "fbm" ? "Datamine (ANGLE1 вокруг оси 3)" : "Micromine (угол вокруг Z, против часовой стрелки)";
  return `Поворот ${n(turn)}° по соглашению ${how} — предварительно: образцов повёрнутых моделей пока нет. Решает проверка трёх центров.`;
}

/** The rows of a refusal, from 1: «1–20 и другие». */
export function rowsText(error: BlocksError | null): string {
  const rows = error?.rows ?? [];
  if (!rows.length) return "";
  const shown = rows.slice(0, 20).map((r) => n(r + 1, 0)).join(", ");
  const more = (error?.count ?? rows.length) > rows.length ? " и другие" : "";
  return `Строки данных (с 1): ${shown}${more}`;
}

/** The file that can be asked to be loaded as a block model: a table the core did not take for one. */
export const CAN_FORCE = new Set(["no_coordinates", "no_column", "no_header"]);

/** The findings of the checks of a stored model (`meta.checks`) or of the job of the import (`entry.checks`). */
export interface Findings {
  duplicates: number;
  overlaps: number;
}

export function findingsText(f: Findings): string[] {
  const out: string[] = [];
  if (f.duplicates > 0) out.push(`Повторяющиеся блоки: ${n(f.duplicates, 0)} — оставлена первая строка.`);
  if (f.overlaps > 0) out.push(`Перекрывающиеся блоки: ${n(f.overlaps, 0)} — строки сохранены, смотрите «Свойства» модели.`);
  return out;
}

/** «Блочная модель · Micromine DAT · блоков 103 · родителей 103 · субблоки». */
export function headline(info: BlocksInfo): string {
  const rows = `${info.rows_exact ? "" : "около "}${n(info.rows, 0)}`;
  const parts = [`${info.label}`, `блоков ${rows}`];
  if (info.parents) parts.push(`родителей ${n(info.parents, 0)}`);
  if (info.sub) parts.push("с субблоками");
  return parts.join(" · ");
}

/** The fields that will load: not skipped by the user. */
export function loadedFields(info: BlocksInfo, draft: BlocksDraft | null | undefined): BlocksField[] {
  return info.fields.filter((f) => draft?.columns[f.column] !== "skip");
}

/** The delimiters of a text file the user may choose. */
export const DELIMITERS: { id: string; title: string }[] = [
  { id: "", title: "авто" },
  { id: ";", title: "точка с запятой" },
  { id: ",", title: "запятая" },
  { id: "\t", title: "табуляция" },
  { id: "|", title: "вертикальная черта" },
];

export const isTextFormat = (format: string): boolean => format === "csv" || format === "csv_micromine";
