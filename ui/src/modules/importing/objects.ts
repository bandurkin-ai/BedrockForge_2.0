/**
 * «Импорт файлов» for lines, points, rasters and wireframes (ТЗ 7.8, 7.18, 7.19, 7.21; ADR 0011 §3, §5; ADR 0013
 * §5–§6): the composition of the chosen files (`POST /import/objects/inspect` — nothing is written), «Уровень
 * растров по Z» and the start of the job `import.objects`. A DXF or a DWG gives its lines and its wireframes at
 * once; Datamine — the field that divides it and «предварительно». The rules are plain functions (vitest);
 * `ObjectsWizard` holds the state. No React.
 */
import { api, CoreError } from "../../shell/api";
import { logError, logStep, type OpLog } from "../../shell/opLog";
import { Store } from "../../shell/store";
import { followJob } from "../jobs/follow";
import { parseEvery, type CloudCard, type CloudMode } from "./cloud";
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

/** A wireframe of a file as the composition tells it; `points` / `triangles` — null when the file does not
 * tell without reading the geometry; `error` — a wireframe that will not load (the others of the file will). */
export interface WireframeEntry {
  key: number | string;
  name: string;
  points: number | null;
  triangles: number | null;
  color: string | null;
  attrs: { name: string; value?: unknown }[];
  extent: number[] | null;
  warnings: string[];
  error: { code: string; message: string; hint: string } | null;
}

export interface FileOk {
  path: string;
  file: string;
  ok: true;
  format: string;
  label: string;
  /** "lines" — also a drawing with wireframes (DXF, DWG): its `wireframes` are loaded with its layers;
   * "cloud" — a point cloud (a LAS, or a table loaded as one; ADR 0014 §6): its card is `cloud` */
  kind: "lines" | "points" | "raster" | "wireframes" | "cloud";
  encoding: string | null;
  crs: { name: string | null; epsg: number | null; wkt: string | null } | null;
  sidecars: string[];
  layers: LayerEntry[];
  rasters: RasterEntry[];
  /** the first of them (the core cuts a long list); every one that has no `error` is loaded */
  wireframes: WireframeEntry[];
  totals: { layers: number; lines: number; points: number; vertices: number; rasters: number; wireframes: number; triangles: number };
  /** wireframes of the file that will not load */
  unread: number;
  /** the reader was not checked on real files (Datamine) */
  preliminary: boolean;
  /** option of the file → the fields to choose it from (`split_by`, `layer_field`) */
  choices: Record<string, string[]>;
  warnings: string[];
  left_out: { what: string; count: number | null; reason: string }[];
  size: number;
  /** the header of a LAS or the head of a table loaded as a cloud (`kind: "cloud"`) */
  cloud?: CloudCard;
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
  wireframes: number;
  triangles: number;
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

/** How a file is read when it is not to be guessed: the encoding; the columns of a table of points; Datamine —
 * the field that divides wireframes, the field whose values are layers of strings ("" — none); a point cloud
 * (ADR 0014 §6) — a table loaded as one (`cloud`), «каждая k-я запись» as typed (`every`), the column of the
 * class of a table (`class_column`: "" — none; absent — found by its name). */
export interface FileOptions {
  encoding?: string;
  x?: string;
  y?: string;
  z?: string;
  split_by?: string;
  layer_field?: string;
  cloud?: boolean;
  every?: string;
  class_column?: string;
}

/** What is sent for a file: the typed text as the core takes it. */
export type SentOptions = Omit<FileOptions, "every"> & { every?: number };

/** The choices of the wizard for a file: option → its Russian label. */
export const CHOICE_TITLES: Record<string, { label: string; none: string }> = {
  split_by: { label: "Делить на каркасы по полю", none: "не делить: один каркас" },
  layer_field: { label: "Слои по полю", none: "один слой" },
};

/** The formats whose text the encoding changes (the others are binary, or tell their encoding themselves). */
const NO_ENCODING = new Set(["tridb", "obj", "stl", "surpac_dtm", "dwg", "raster", "las"]);
export const takesEncoding = (f: FileEntry): boolean => !f.ok || !NO_ENCODING.has(f.format);

/** A DWG without ODA File Converter: «Настройка › Внешние программы» (ADR 0009, ADR 0013 §6). */
export const needsConverter = (f: FileEntry): boolean => !f.ok && f.error.code === "oda_not_found";

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
  /** «Облако точек» of the ribbon: every table of the selection is loaded as a cloud; null — «Импорт файлов» */
  cloud?: CloudMode | null;
}

export function initialObjects(cloud: CloudMode | null = null): ObjectsState {
  return { paths: [], options: {}, level: { elevation: "", shift: "" }, composition: null, busy: false, error: null, starting: false, started: false, cloud };
}

const n = (v: number): string => v.toLocaleString("ru-RU");

const COUNTED = [["layers", "слоёв"], ["lines", "линий"], ["vertices", "вершин"], ["points", "точек"], ["rasters", "растров"], ["wireframes", "каркасов"], ["triangles", "треугольников"]] as const;

/** «Файлов: 2 · слоёв: 5 · линий: 120 · вершин: 3 400 · точек: 25 · растров: 1 · каркасов: 66 · треугольников:
 * 3 590 · не прочитано: 1» */
export function totalsText(t: Totals): string {
  if (!t.files && !t.failed) return "Нет файлов для загрузки";
  const parts = [`Файлов: ${n(t.files)}`];
  for (const [key, label] of COUNTED) if (t[key]) parts.push(`${label}: ${n(t[key])}`);
  if (t.failed) parts.push(`не прочитано: ${n(t.failed)}`);
  return parts.join(" · ");
}

/** «слоёв: 3 · линий: 120 · вершин: 3 400» of a file of lines or points; «каркасов: 66 · треугольников: 3 590 ·
 * не загрузятся: 1» of its wireframes (a drawing has both). */
export function fileCounts(f: FileOk): string {
  const t = f.totals;
  const parts: string[] = [];
  if (t.layers || !f.wireframes.length) parts.push(`слоёв: ${n(t.layers)}`);
  if (t.lines) parts.push(`линий: ${n(t.lines)}`);
  if (t.vertices) parts.push(`вершин: ${n(t.vertices)}`);
  if (t.points) parts.push(`точек: ${n(t.points)}`);
  if (t.wireframes || f.unread) parts.push(`каркасов: ${n(t.wireframes)}`);
  if (t.triangles) parts.push(`треугольников: ${n(t.triangles)}`);
  if (f.unread) parts.push(`не загрузятся: ${n(f.unread)}`);
  return parts.join(" · ");
}

function sumTotals(files: readonly FileEntry[]): Totals {
  const t: Totals = { files: 0, failed: 0, layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 0, triangles: 0 };
  for (const f of files) {
    if (!f.ok) {
      t.failed++;
      continue;
    }
    t.files++;
    for (const [k] of COUNTED) t[k] += f.totals[k] ?? 0;
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

/** «Каждая k-я запись» typed wrong, by the path of its file. */
export function everyErrors(s: Pick<ObjectsState, "options" | "paths">): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [path, o] of Object.entries(s.options)) {
    const e = s.paths.includes(path) ? parseEvery(o.every).error : null;
    if (e) out[path] = e;
  }
  return out;
}

export function canStartObjects(s: ObjectsState): boolean {
  if (!s.composition || s.busy || s.starting || s.composition.totals.files === 0) return false;
  if (Object.keys(everyErrors(s)).length) return false;
  return !levelUsed(s) || levelValid(parseLevel(s.level));
}

/** What is sent of the options of a file: texts that are given; `class_column` — also "" (none); `cloud` —
 * when set, and for every file in the mode «Облако точек»; `every` — as a number (a wrong one is not sent). */
function sentOptions(o: FileOptions, cloudMode: boolean): SentOptions {
  const out: SentOptions = {};
  for (const [k, v] of Object.entries(o)) {
    if (k === "every" || k === "cloud" || k === "class_column") continue;
    if (typeof v === "string" && v.trim() !== "") (out as Record<string, string>)[k] = v;
  }
  if (typeof o.class_column === "string") out.class_column = o.class_column;
  if (o.cloud || cloudMode) out.cloud = true;
  const every = parseEvery(o.every);
  if (!every.error && every.every > 1) out.every = every.every;
  return out;
}

/** The body of `POST /import/objects`: the level goes as numbers (null — not set). */
export function startBody(s: ObjectsState): { files: string[]; options: Record<string, SentOptions>; raster_z: { elevation: number | null; shift: number | null }; section: boolean } {
  const level = parseLevel(s.level);
  const used = levelUsed(s) && levelValid(level);
  const options: Record<string, SentOptions> = {};
  const cloudMode = !!s.cloud;
  for (const path of s.paths) {
    const given = sentOptions(s.options[path] ?? {}, cloudMode);
    if (Object.keys(given).length) options[path] = given;
  }
  return { files: s.paths, options, raster_z: { elevation: used ? level.elevation : null, shift: used && level.shift ? level.shift : null }, section: true };
}

const errorText = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** A DWG is read through ODA File Converter: the core is to hold the path of «Настройка» before it reads one. */
export const hasDrawing = (paths: readonly string[]): boolean => paths.some((p) => /\.dwg$/i.test(p));

export class ObjectsWizard {
  readonly store: Store<ObjectsState>;
  private seq = 0;

  /** `syncConverter` — gives the core the ODA File Converter of «Настройка» (a restarted core has forgotten it);
   * `cloud` — the mode «Облако точек» (every table is a cloud). */
  constructor(
    readonly log: OpLog,
    private readonly syncConverter?: () => Promise<unknown>,
    cloud: CloudMode | null = null,
  ) {
    this.store = new Store<ObjectsState>(initialObjects(cloud));
  }

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
      if (this.syncConverter && hasDrawing(s.paths)) await this.syncConverter().catch(() => undefined); // the file tells what is wrong
      if (mine !== this.seq) return;
      // the level is not sent: the composition shows the heights of the georeference, the level is applied on top
      const { files, options } = startBody(s);
      const r = await api.post<Composition>("/import/objects/inspect", { files, options, section: true });
      if (mine !== this.seq) return;
      logStep(this.log, `Состав: ${totalsText(r.totals)}`);
      for (const f of r.files) {
        if (f.ok) {
          logStep(this.log, `  ${f.file}: ${f.label}${f.rasters.length ? "" : `, ${fileCounts(f)}`}${f.preliminary ? " (предварительно)" : ""}${f.warnings.length ? `; замечаний ${f.warnings.length}` : ""}`);
          if (f.cloud) logStep(this.log, `    облако: ${f.cloud.summary}${f.cloud.every_chosen > 1 ? `; каждая ${f.cloud.every_chosen}-я запись` : ""}`);
          for (const w of f.wireframes) if (w.error) logStep(this.log, `    каркас «${w.name}» не загрузится: ${w.error.message} ${w.error.hint}`.trimEnd());
        } else logStep(this.log, `  ${f.file}: не прочитан — ${f.error.message} ${f.error.hint}`.trim());
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
