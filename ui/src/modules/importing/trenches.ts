/**
 * «Канавы по полилиниям» (ТЗ 7.35 Д-16; ADR 0011 §5): the file of polylines of the wizard — a STR, a DXF or a
 * table of vertices «канава, X, Y, Z» — how its trenches are named (by the string or by a field) and directed
 * («Обратить направление»), and what the core makes of it (`POST /import/trenches/inspect`). Pure: the calls
 * are in `wizard.ts`.
 */

export interface TrenchRow {
  name: string;
  vertices: number;
  /** 3D length of the polyline, m */
  length: number;
  /** the first vertex in the chosen direction: the collar */
  x: number;
  y: number;
  z: number;
}

/** The answer of `POST /import/trenches/inspect`. */
export interface TrenchPreview {
  format: string;
  /** layers of lines of the file with the number of their lines */
  layers: { name: string; lines: number }[];
  /** attributes of the lines (columns of a table) a name can be taken from */
  fields: string[];
  trenches: number;
  vertices: number;
  length: number;
  /** lines that give no trench */
  skipped: number;
  warnings: string[];
  preview: TrenchRow[];
}

export interface TrenchState {
  path: string | null;
  name: string;
  /** null — the encoding is detected */
  encoding: string | null;
  nameFrom: "string" | "field";
  nameField: string;
  /** names of the layers to take; null — all */
  layers: string[] | null;
  reverseAll: boolean;
  /** trenches whose direction is reversed one by one */
  reversed: string[];
  /** columns of a table of vertices, when they are not to be guessed */
  columns: { x: string; y: string; z: string };
  preview: TrenchPreview | null;
  busy: boolean;
  error: string | null;
}

export function initialTrench(): TrenchState {
  return { path: null, name: "", encoding: null, nameFrom: "string", nameField: "", layers: null, reverseAll: false, reversed: [], columns: { x: "", y: "", z: "" }, preview: null, busy: false, error: null };
}

/** Trenches listed in the preview of the wizard (each can be reversed there). */
export const PREVIEW_LIMIT = 200;
const TABLES = new Set(["csv", "txt", "tsv", "prn", "dat", "xyz"]);

/** Is the file a table of vertices: by the answer of the core, before it — by the extension. */
export function isVertexTable(t: Pick<TrenchState, "path" | "preview">): boolean {
  if (t.preview) return t.preview.format === "points";
  return TABLES.has((t.path ?? "").toLowerCase().split(".").pop() ?? "");
}

/** «Обратить направление» as the job takes it: every trench, the named ones, or none. */
export function reverseOf(t: Pick<TrenchState, "reverseAll" | "reversed">): boolean | string[] {
  return t.reverseAll ? true : t.reversed.length ? [...t.reversed] : false;
}

export function toggleReverse(reversed: readonly string[], name: string, on: boolean): string[] {
  const rest = reversed.filter((n) => n !== name);
  return on ? [...rest, name] : rest;
}

/** The `lines` parameter of `import.trenches` and of its preview. */
export function linesSpec(t: TrenchState): Record<string, unknown> {
  const spec: Record<string, unknown> = { path: t.path, name_from: t.nameFrom };
  const field = t.nameField.trim();
  if (isVertexTable(t)) {
    // a table always names by a column: the chosen one, else the core finds it by its name
    spec.name_from = "string";
    if (field) spec.name_field = field;
    for (const k of ["x", "y", "z"] as const) if (t.columns[k].trim()) spec[k] = t.columns[k].trim();
  } else {
    if (t.nameFrom === "field") spec.name_field = field;
    if (t.layers !== null) spec.layers = [...t.layers];
  }
  if (t.encoding) spec.encoding = t.encoding;
  spec.reverse = reverseOf(t);
  return spec;
}

const n = (v: number): string => v.toLocaleString("ru-RU");

/** «Канав: 12 · вершин: 340 · общая длина по 3D: 1 520,5 м · линий без канавы: 2» */
export function trenchSummary(p: Pick<TrenchPreview, "trenches" | "vertices" | "length" | "skipped">): string {
  const parts = [`Канав: ${n(p.trenches)}`, `вершин: ${n(p.vertices)}`, `общая длина по 3D: ${p.length.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} м`];
  if (p.skipped) parts.push(`линий без канавы: ${n(p.skipped)}`);
  return parts.join(" · ");
}

/** Why the trenches cannot be imported yet, in words; null — they can. */
export function trenchProblem(t: TrenchState): string | null {
  if (!t.path) return "Выберите файл с линиями канав";
  if (t.busy) return "Файл читается…";
  if (t.error) return t.error;
  if (!isVertexTable(t) && t.nameFrom === "field" && !t.nameField.trim()) return "Выберите поле с именем канавы";
  if (!t.preview) return "Файл ещё не прочитан";
  if (!t.preview.trenches) return "В файле нет ни одной канавы: проверьте слои и источник имени";
  return null;
}
