/**
 * «Импорт файлов» for lines, points and rasters (ТЗ 7.8, 7.18, 7.19; ADR 0011 §3, §5): the composition of the
 * chosen files (`POST /import/objects/inspect` — nothing is written), «Уровень растров по Z» and the start of
 * the job `import.objects`. The rules are plain functions (vitest); `ObjectsWizard` holds the state. No React.
 */
import { api, CoreError } from "../../shell/api";
import { logError, logStep, type OpLog } from "../../shell/opLog";
import { Store } from "../../shell/store";
import { followJob } from "../jobs/follow";
import { baseName } from "./route";

// ---- the composition as the core gives it --------------------------------------------------------------

export interface LayerEntry {
  name: string;
  lines: number;
  points: number;
  vertices: number;
  closed: number;
  length: number;
  points_only: boolean;
  flat: boolean;
  color: string | null;
  attributes: { name: string; type?: string }[];
}

export interface RasterEntry {
  name: string;
  width: number;
  height: number;
  image: string;
  bits: number;
  georef: { source: string; source_label: string; pixel?: number | null; checks?: unknown[] };
  /** the level of a plan raster from its georeference; null — none (it lies under the lowest object) */
  elevation: number | null;
  vertical: boolean;
  has_elevation: boolean;
  sidecars: string[];
}

export interface FileOk {
  path: string;
  file: string;
  ok: true;
  format: string;
  label: string;
  kind: "lines" | "points" | "raster";
  encoding: string | null;
  crs: { name: string | null; epsg: number | null; wkt: string | null } | null;
  sidecars: string[];
  layers: LayerEntry[];
  rasters: RasterEntry[];
  totals: { layers: number; lines: number; points: number; vertices: number; rasters: number };
  warnings: string[];
  left_out: { what: string; count: number; reason: string }[];
  size: number;
}

export interface FileFailed {
  path: string;
  file: string;
  ok: false;
  error: { code: string; message: string; hint: string };
  size: number;
}

export type FileEntry = FileOk | FileFailed;

export interface RestEntry {
  path: string;
  file: string;
  code: string;
  message: string;
  hint: string;
}

export interface Totals {
  files: number;
  failed: number;
  layers: number;
  lines: number;
  points: number;
  vertices: number;
  rasters: number;
}

export interface Composition {
  files: FileEntry[];
  /** files of the selection that belong to nothing, each with the reason */
  rest: RestEntry[];
  totals: Totals;
  raster_z: { applies: boolean; elevation: number | null; shift: number };
  /** where an image without a georeference stands: the current section line, or null */
  section: Record<string, number> | null;
}

/** How a file is read when it is not to be guessed: the encoding; the columns of a table of points. */
export interface FileOptions {
  encoding?: string;
  x?: string;
  y?: string;
  z?: string;
}

// ---- «Уровень растров по Z» (ТЗ 7.19) -------------------------------------------------------------------

/** The two fields as they are typed: «высота, м» and «сдвиг, м». */
export interface Level {
  elevation: string;
  shift: string;
}

export interface ParsedLevel {
  /** instead of the height of the georeference; null — not set */
  elevation: number | null;
  /** ± for every raster; 0 — not set */
  shift: number;
  errors: { elevation?: string; shift?: string };
}

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;

/**
 * A number typed with a comma or a point: «12,5», «12.5», «−5», «1 250,5». Empty — null (not set, never 0);
 * anything else — undefined (not a number).
 */
export function parseNumber(text: string): number | null | undefined {
  const t = text.replace(/[\s  ]/g, "").replace(/−/g, "-");
  if (!t) return null;
  const plain = t.replace(",", ".");
  if (!NUMBER.test(plain)) return undefined;
  const v = Number(plain);
  return Number.isFinite(v) ? v : undefined;
}

export function parseLevel(level: Level): ParsedLevel {
  const out: ParsedLevel = { elevation: null, shift: 0, errors: {} };
  const elevation = parseNumber(level.elevation);
  if (elevation === undefined) out.errors.elevation = `«${level.elevation.trim()}» — не число. Введите высоту, например 250,5.`;
  else out.elevation = elevation;
  const shift = parseNumber(level.shift);
  if (shift === undefined) out.errors.shift = `«${level.shift.trim()}» — не число. Введите сдвиг, например −5.`;
  else out.shift = shift ?? 0;
  return out;
}

export const levelValid = (l: ParsedLevel): boolean => !l.errors.elevation && !l.errors.shift;

/** «250,5», «−5» */
export function metres(v: number, signed = false): string {
  const text = Math.abs(v).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  return `${v < 0 ? "−" : signed ? "+" : ""}${text}`;
}

/**
 * Where a raster will stand with the level applied — the rule of the import (`rasters.store.apply_level`):
 * «высота» replaces the level of a plan raster, «сдвиг» then moves every raster that has a height, all the
 * corners of a vertical one too. `warn` — the shift has nothing to move.
 */
export function rasterLevel(r: Pick<RasterEntry, "vertical" | "elevation">, level: ParsedLevel): { text: string; warn: boolean } {
  if (r.vertical) return { text: level.shift ? `вертикальный, все углы сдвигаются на ${metres(level.shift, true)} м` : "вертикальный", warn: false };
  const base = level.elevation ?? r.elevation;
  if (base === null) {
    const none = "в плане, под самым низким объектом";
    return level.shift ? { text: `${none} — сдвиг не применён: у растра нет высоты`, warn: true } : { text: none, warn: false };
  }
  const replaced = level.elevation !== null && r.elevation !== null && Math.abs(r.elevation - level.elevation) > 1e-9;
  return { text: `в плане, высота ${metres(base + level.shift)} м${replaced ? ` (вместо ${metres(r.elevation!)} м из привязки)` : ""}`, warn: false };
}

// ---- the state ------------------------------------------------------------------------------------------

export interface ObjectsState {
  /** every chosen file, companions too */
  paths: string[];
  /** by the path of a main file */
  options: Record<string, FileOptions>;
  level: Level;
  composition: Composition | null;
  busy: boolean;
  error: string | null;
  starting: boolean;
  started: boolean;
}

export function initialObjects(): ObjectsState {
  return { paths: [], options: {}, level: { elevation: "", shift: "" }, composition: null, busy: false, error: null, starting: false, started: false };
}

const n = (v: number): string => v.toLocaleString("ru-RU");

/** «Файлов: 2 · слоёв: 5 · линий: 120 · вершин: 3 400 · точек: 25 · растров: 1 · не прочитано: 1» */
export function totalsText(t: Totals): string {
  if (!t.files && !t.failed) return "Нет файлов для загрузки";
  const parts = [`Файлов: ${n(t.files)}`];
  for (const [key, label] of [["layers", "слоёв"], ["lines", "линий"], ["vertices", "вершин"], ["points", "точек"], ["rasters", "растров"]] as const) {
    if (t[key]) parts.push(`${label}: ${n(t[key])}`);
  }
  if (t.failed) parts.push(`не прочитано: ${n(t.failed)}`);
  return parts.join(" · ");
}

/** «3 слоя · 120 линий · 3 400 вершин» of a file of lines or points. */
export function fileCounts(f: FileOk): string {
  const t = f.totals;
  const parts = [`слоёв: ${n(t.layers)}`];
  if (t.lines) parts.push(`линий: ${n(t.lines)}`);
  if (t.vertices) parts.push(`вершин: ${n(t.vertices)}`);
  if (t.points) parts.push(`точек: ${n(t.points)}`);
  return parts.join(" · ");
}

function sumTotals(files: readonly FileEntry[]): Totals {
  const t: Totals = { files: 0, failed: 0, layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0 };
  for (const f of files) {
    if (!f.ok) {
      t.failed++;
      continue;
    }
    t.files++;
    for (const k of ["layers", "lines", "points", "vertices", "rasters"] as const) t[k] += f.totals[k];
  }
  return t;
}

const folderOf = (p: string): string => p.slice(0, p.length - baseName(p).length);
const stem = (p: string): string => baseName(p).replace(/\.[^.]+$/, "").toLowerCase();

/** «Убрать»: the file and its companions (the files of its name next to it, unless they are files of the
 * composition themselves) leave the selection and the composition; nothing is read again. */
export function withoutFile(s: ObjectsState, path: string): ObjectsState {
  const mains = new Set(s.composition?.files.map((f) => f.path));
  const gone = (p: string) => p === path || (!mains.has(p) && folderOf(p) === folderOf(path) && stem(p) === stem(path));
  const paths = s.paths.filter((p) => !gone(p));
  const options = Object.fromEntries(Object.entries(s.options).filter(([p]) => !gone(p)));
  if (!s.composition) return { ...s, paths, options };
  const files = s.composition.files.filter((f) => !gone(f.path));
  const totals = sumTotals(files);
  const composition: Composition = {
    ...s.composition,
    files,
    rest: s.composition.rest.filter((r) => !gone(r.path)),
    totals,
    raster_z: { ...s.composition.raster_z, applies: totals.rasters > 0 },
  };
  return { ...s, paths, options, composition };
}

/** The level counts only while the selection has rasters. */
const levelUsed = (s: ObjectsState): boolean => (s.composition?.totals.rasters ?? 0) > 0;

export function canStartObjects(s: ObjectsState): boolean {
  if (!s.composition || s.busy || s.starting || s.composition.totals.files === 0) return false;
  return !levelUsed(s) || levelValid(parseLevel(s.level));
}

/** The body of `POST /import/objects`: the level goes as numbers (null — not set). */
export function startBody(s: ObjectsState): { files: string[]; options: Record<string, FileOptions>; raster_z: { elevation: number | null; shift: number | null }; section: boolean } {
  const level = parseLevel(s.level);
  const used = levelUsed(s) && levelValid(level);
  const options: Record<string, FileOptions> = {};
  for (const [path, o] of Object.entries(s.options)) {
    const given = Object.fromEntries(Object.entries(o).filter(([, v]) => typeof v === "string" && v.trim() !== ""));
    if (s.paths.includes(path) && Object.keys(given).length) options[path] = given;
  }
  return { files: s.paths, options, raster_z: { elevation: used ? level.elevation : null, shift: used && level.shift ? level.shift : null }, section: true };
}

const errorText = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

export class ObjectsWizard {
  readonly store = new Store<ObjectsState>(initialObjects());
  private seq = 0;

  constructor(readonly log: OpLog) {}

  /** Adds files to the selection and reads the composition of all of it (companions pair across the selection). */
  async add(paths: readonly string[]): Promise<void> {
    const have = new Set(this.store.get().paths);
    const fresh = paths.filter((p) => !have.has(p));
    if (!fresh.length) return;
    for (const p of fresh) logStep(this.log, `Файл: ${p}`);
    this.store.set((s) => ({ paths: [...s.paths, ...fresh] }));
    await this.inspect();
  }

  remove(path: string): void {
    logStep(this.log, `Убран файл: ${baseName(path)}`);
    this.store.set((s) => withoutFile(s, path));
  }

  /** Read options of a file (encoding, columns of a table of points): the composition is read again. */
  async setOptions(path: string, patch: FileOptions): Promise<void> {
    this.store.set((s) => ({ options: { ...s.options, [path]: { ...s.options[path], ...patch } } }));
    logStep(this.log, `${baseName(path)}: параметры чтения ${JSON.stringify(this.store.get().options[path])}`);
    await this.inspect();
  }

  setLevel(patch: Partial<Level>): void {
    this.store.set((s) => ({ level: { ...s.level, ...patch } }));
  }

  /** Reads the composition; an answer overtaken by a newer request is dropped. */
  async inspect(): Promise<void> {
    const s = this.store.get();
    const mine = ++this.seq;
    if (!s.paths.length) {
      this.store.set({ composition: null, busy: false, error: null });
      return;
    }
    this.store.set({ busy: true, error: null });
    try {
      // the level is not sent: the composition shows the heights of the georeference, the level is applied on top
      const { files, options } = startBody(s);
      const r = await api.post<Composition>("/import/objects/inspect", { files, options, section: true });
      if (mine !== this.seq) return;
      logStep(this.log, `Состав: ${totalsText(r.totals)}`);
      for (const f of r.files) {
        if (f.ok) logStep(this.log, `  ${f.file}: ${f.label}${f.rasters.length ? "" : `, ${fileCounts(f)}`}${f.warnings.length ? `; замечаний ${f.warnings.length}` : ""}`);
        else logStep(this.log, `  ${f.file}: не прочитан — ${f.error.message} ${f.error.hint}`.trim());
      }
      for (const x of r.rest) logStep(this.log, `  ${x.file}: не загрузится — ${x.message}`);
      this.store.set({ composition: r, busy: false });
    } catch (e) {
      if (mine !== this.seq) return;
      logError(this.log, errorText(e), e instanceof CoreError ? e : undefined);
      this.store.set({ busy: false, error: errorText(e) });
    }
  }

  /** Starts the job `import.objects`; returns it or null. */
  async start(): Promise<{ source: string; id: number } | null> {
    const s = this.store.get();
    if (!canStartObjects(s)) return null;
    this.store.set({ starting: true, error: null });
    const body = startBody(s);
    logStep(this.log, `Запуск импорта: ${JSON.stringify({ ...body, files: body.files.map(baseName) })}`);
    try {
      const r = await api.post<{ source: string; id: number }>("/import/objects", body);
      logStep(this.log, `Задание №${r.id} поставлено в очередь; журнал продолжится до его окончания`);
      followJob(this.log, r);
      this.store.set({ starting: false, started: true });
      return r;
    } catch (e) {
      logError(this.log, errorText(e), e instanceof CoreError ? e : undefined);
      this.store.set({ starting: false, error: errorText(e) });
      return null;
    }
  }
}
