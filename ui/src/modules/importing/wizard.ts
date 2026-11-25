/**
 * State of the wizard «Импорт скважин» (ТЗ 7.7): steps «Устья» → «Инклинометрия» → «Интервалы и события» →
 * «Импорт». Files added at any step go to the step of their detected kind. In the mode «Канавы по полилиниям»
 * (ТЗ 7.35 Д-16) step 1 is the file of polylines (`trenches.ts`), step 2 — an optional table of samples,
 * mapped as any table of the wizard; the job is `import.trenches`. No React here.
 */
import { api, CoreError } from "../../shell/api";
import { logError, logStep, type OpLog } from "../../shell/opLog";
import { followJob } from "../jobs/follow";
import { askImpact } from "../lineage/impactAsk";
import { Store } from "../../shell/store";
import { initialTrench, linesSpec, PREVIEW_LIMIT, trenchProblem, trenchSummary, type TrenchPreview, type TrenchState } from "./trenches";

export type TableKind = "collar" | "survey" | "assay" | "interval" | "event";
export type StepId = "collar" | "survey" | "trenches" | "tables" | "import";
/** What the wizard loads: drillholes from tables, or trenches along polylines. */
export type WizardMode = "holes" | "trenches";

export const STEPS: { id: StepId; title: string }[] = [
  { id: "collar", title: "Устья" },
  { id: "survey", title: "Инклинометрия" },
  { id: "tables", title: "Интервалы и события" },
  { id: "import", title: "Импорт" },
];

const TRENCH_STEPS: { id: StepId; title: string }[] = [
  { id: "trenches", title: "Канавы по полилиниям" },
  { id: "tables", title: "Пробы канав" },
  { id: "import", title: "Импорт" },
];

export const stepsOf = (mode: WizardMode): { id: StepId; title: string }[] => (mode === "trenches" ? TRENCH_STEPS : STEPS);
export const MODE_TITLES: Record<WizardMode, string> = { holes: "Импорт скважин", trenches: "Импорт канав" };
const SET_NAMES: Record<WizardMode, string> = { holes: "Скважины", trenches: "Канавы" };

export const KIND_TITLES: Record<TableKind, string> = {
  collar: "Устья (Collar)",
  survey: "Инклинометрия (Survey)",
  assay: "Пробы (Assay)",
  interval: "Интервальная таблица",
  event: "События по скважинам",
};

export function stepOf(kind: TableKind | null): StepId {
  return kind === "collar" ? "collar" : kind === "survey" ? "survey" : "tables";
}

export interface ReadOptions {
  encoding?: string | null;
  delimiter?: string | null;
  header_row: number;
  sheet?: string | null;
}

export interface Issue {
  severity: "error" | "warning";
  code: string;
  message: string;
  field?: string;
  count?: number;
}

export interface CheckResult {
  rows: number;
  columns: { name: string; kind: "number" | "text"; sample: unknown[] }[];
  kind: TableKind | null;
  mapping: Record<string, string>;
  components: { column: string; unit: string }[];
  attributes: string[];
  warnings: string[];
  issues: Issue[];
  preview: Record<string, unknown>[];
  counts?: Record<string, number>;
  can_force?: boolean;
  fields: { key: string; label: string; required: boolean }[];
}

export interface InspectResult {
  format: string;
  summary: string;
  info: { encoding: string | null; delimiter: string | null; header_row: number | null; sheet: string | null; sheets: string[] };
  rows: string[][];
  numbers: number[];
  warnings: string[];
  size: number;
}

export interface FileEntry {
  id: number;
  path: string;
  name: string;
  options: ReadOptions;
  inspect: InspectResult | null;
  check: CheckResult | null;
  kind: TableKind | null;
  mapping: Record<string, string>;
  components: { column: string; unit: string }[];
  attributes: string[];
  force: boolean;
  tableName: string;
  busy: boolean;
  error: string | null;
}

export interface WizardState {
  mode: WizardMode;
  step: StepId;
  /** the file of polylines of the mode «Канавы по полилиниям» */
  trench: TrenchState;
  /** the chosen set has a table of samples already: the samples of the trenches replace it (confirmed) */
  replaceSamples: boolean;
  files: FileEntry[];
  setMode: "new" | "existing";
  setId: number | null;
  setName: string;
  crs: string | null;
  crsName: string | null;
  sets: SetInfo[];
  starting: boolean;
  /** A job was started: the core already dropped its copy of the files. */
  started: boolean;
  error: string | null;
}

export interface SetInfo {
  id: number;
  name: string;
  holes: number;
  tables: { name: string; kind: string; rows: number }[];
}

export function initialState(mode: WizardMode = "holes"): WizardState {
  return {
    mode, step: stepsOf(mode)[0].id, trench: initialTrench(), replaceSamples: false, files: [], setMode: "new", setId: null, setName: SET_NAMES[mode],
    crs: null, crsName: null, sets: [], starting: false, started: false, error: null,
  };
}

let nextId = 1;
const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;
const stem = (p: string) => baseName(p).replace(/\.[^.]+$/, "");

export function filesOf(s: WizardState, step: StepId): FileEntry[] {
  if (s.mode === "trenches") return step === "tables" ? s.files : []; // one step of tables: the samples
  return s.files.filter((f) => stepOf(f.kind) === step || (f.kind === null && step === s.step && f.check === null));
}

/** Blocking problems of a file: errors that «Загрузить с ошибками» does not cover. */
export function blockers(f: FileEntry): Issue[] {
  if (!f.check) return [];
  return f.check.issues.filter((i) => i.severity === "error" && !(f.force && f.check?.can_force));
}

export function ready(f: FileEntry): boolean {
  return f.kind !== null && !!f.check && !f.busy && !f.error && blockers(f).length === 0;
}

const nameKey = (t: string) => t.trim().normalize("NFC").toLowerCase();
const KIND_NAMES: Partial<Record<TableKind, string>> = { assay: "Пробы (Assay)", interval: "Интервальная таблица", event: "События по скважинам" };

/** The table name a file gets in the set (the core uses the same rule). */
export function tableNameOf(f: FileEntry): string {
  return f.tableName.trim() || (f.kind ? KIND_NAMES[f.kind] ?? f.kind : "");
}

/** Two files that would land in one table: the second would replace the first (one assay table per set). */
export function clashOf(s: WizardState): string | null {
  const seen = new Map<string, string>();
  for (const f of s.files) {
    if (!f.kind || f.kind === "collar" || f.kind === "survey") continue;
    const key = f.kind === "assay" ? "assay" : `${f.kind}:${nameKey(tableNameOf(f))}`;
    const other = seen.get(key);
    if (other) return f.kind === "assay" ? `Файлы «${other}» и «${f.name}» — оба пробы: в наборе одна таблица проб.` : `Файлы «${other}» и «${f.name}» загружаются в одну таблицу «${tableNameOf(f)}».`;
    seen.set(key, f.name);
  }
  return null;
}

/** What a file replaces in the chosen set («Заменить», ТЗ 7.7): null — nothing, it is new. */
export function replaces(s: WizardState, f: FileEntry): string | null {
  if (s.setMode !== "existing" || !f.kind) return null;
  const set = s.sets.find((x) => x.id === s.setId);
  if (!set) return null;
  if (f.kind === "collar") return set.holes ? "устья набора" : null;
  if (f.kind === "survey") return set.holes ? "инклинометрию набора" : null;
  const old = set.tables.find((t) => t.kind === f.kind && (f.kind === "assay" || nameKey(t.name) === nameKey(tableNameOf(f))));
  return old ? `таблицу «${old.name}» (${old.rows.toLocaleString("ru-RU")} строк)` : null;
}

/** What the import would replace in the chosen set: the collars or the surveys (the set is the node) or a table by its kind and name. */
export interface Replaced {
  what: string;
  table: { kind: string; name: string } | null;
}

/** The files of the import that replace something of the chosen set (the trenches: the samples, when the replacement is confirmed). */
export function replacedBy(s: WizardState): Replaced[] {
  if (s.setMode !== "existing" || s.setId === null) return [];
  const files = s.mode === "trenches" ? (s.replaceSamples && sampleFile(s) ? [sampleFile(s)!] : []) : s.files;
  const out: Replaced[] = [];
  for (const f of files) {
    const what = replaces(s, f);
    if (what && f.kind) out.push({ what, table: f.kind === "collar" || f.kind === "survey" ? null : { kind: f.kind, name: tableNameOf(f) } });
  }
  return out;
}

/**
 * The window «Влияние изменения» before the import replaces what is built from (Д-35; ADR 0025 §7): per replaced table or, for the
 * collars and the surveys, the set; it opens only when there are dependents. `false` — the person refused: the import does not start.
 * The tables of the set are read for their numbers; no answer is no reason to stop the import.
 */
export async function askReplaced(s: WizardState): Promise<boolean> {
  const list = replacedBy(s);
  if (!list.length || s.setId === null) return true;
  const set = s.sets.find((x) => x.id === s.setId);
  let tables: { table: string; kind: string; name: string }[] = [];
  if (list.some((r) => r.table)) {
    try {
      tables = (await api.get<{ tables?: typeof tables }>(`/drillholes/sets/${s.setId}/tables`)).tables ?? [];
    } catch {
      tables = [];
    }
  }
  const asked = new Set<string>();
  for (const r of list) {
    const old = r.table ? tables.find((t) => t.kind === r.table!.kind && (r.table!.kind === "assay" || nameKey(t.name) === nameKey(r.table!.name))) : undefined;
    const node = old && Number.isInteger(Number(old.table)) ? { kind: "dh_table", id: Number(old.table) } : { kind: "dh_set", id: s.setId };
    const key = `${node.kind}:${node.id}`;
    if (asked.has(key)) continue;
    asked.add(key);
    if (!(await askImpact(node, `заменить ${r.what}${r.table && set ? ` набора «${set.name}»` : ""}`))) return false;
  }
  return true;
}

/** The table of samples of the trenches: the only file of the wizard, when it is samples or intervals. */
export function sampleFile(s: WizardState): FileEntry | null {
  const f = s.files[0];
  return s.files.length === 1 && (f.kind === "assay" || f.kind === "interval") ? f : null;
}

/** The samples of the trenches would replace the table of samples of the chosen set, for all its holes. */
export function needsReplace(s: WizardState): boolean {
  const f = sampleFile(s);
  return f !== null && f.kind === "assay" && replaces(s, f) !== null;
}

/** Why the trenches cannot be imported yet, in words; null — they can (ТЗ 7.35 Д-16). */
export function trenchBlocker(s: WizardState): string | null {
  const problem = trenchProblem(s.trench);
  if (problem) return problem;
  if (s.files.length > 1) return "Пробы канав — одна таблица: уберите лишние файлы";
  if (s.files.length === 1) {
    const f = s.files[0];
    if (!sampleFile(s)) return f.kind === null && !f.check ? "Таблица проб читается…" : "Пробы канав — таблица проб или интервальная таблица: выберите её тип";
    if (!ready(f)) return f.busy ? "Таблица проб читается…" : "Исправьте таблицу проб на шаге «Пробы канав»";
    if (needsReplace(s) && !s.replaceSamples) return "Подтвердите замену таблицы проб набора";
  }
  if (s.setMode === "existing" ? s.setId === null : !s.setName.trim()) return "Выберите набор или введите название нового";
  return null;
}

export function canStart(s: WizardState): boolean {
  if (s.starting) return false;
  if (s.mode === "trenches") return trenchBlocker(s) === null;
  if (!s.files.length || !s.files.every(ready) || clashOf(s)) return false;
  return s.setMode === "existing" ? s.setId !== null : s.setName.trim().length > 0;
}

/** The body of `POST /import/trenches`. */
export function trenchBody(s: WizardState): Record<string, unknown> {
  const f = sampleFile(s);
  return {
    set_id: s.setMode === "existing" ? s.setId : null,
    set_name: s.setMode === "new" ? s.setName.trim() : null,
    crs: s.crs,
    lines: linesSpec(s.trench),
    samples: f && {
      path: f.path, options: f.options, kind: f.kind, mapping: f.mapping, components: f.components, attributes: f.attributes,
      force: f.force, table_name: f.tableName || null, replace: s.setMode === "existing" && s.replaceSamples,
    },
  };
}

export class Wizard {
  readonly store: Store<WizardState>;
  private trenchSeq = 0;

  constructor(readonly log: OpLog, mode: WizardMode = "holes") {
    this.store = new Store<WizardState>(initialState(mode));
  }

  /** Drillholes from tables, or trenches along polylines: the steps change, the chosen files stay. */
  setMode(mode: WizardMode): void {
    const s = this.store.get();
    if (s.mode === mode) return;
    logStep(this.log, `Режим мастера: ${MODE_TITLES[mode]}`);
    this.store.set({ mode, step: stepsOf(mode)[0].id, setName: s.setName === SET_NAMES[s.mode] ? SET_NAMES[mode] : s.setName, error: null });
  }

  // ---- «Канавы по полилиниям» -----------------------------------------------------------------------

  /** The file of polylines: a new one starts from the defaults (names by the string, every layer). */
  async setTrenchFile(path: string): Promise<void> {
    logStep(this.log, `Файл линий канав: ${path}`);
    this.store.set({ trench: { ...initialTrench(), path, name: baseName(path) } });
    await this.inspectTrench();
  }

  /** A change of how the trenches are read, named or directed; the preview follows unless `inspect` is false. */
  async patchTrench(patch: Partial<TrenchState>, inspect = true): Promise<void> {
    // other names: trenches ticked «Обратить» under the old names are not there any more
    const renamed = "nameFrom" in patch || "nameField" in patch || "layers" in patch || "columns" in patch;
    this.store.set((s) => ({ trench: { ...s.trench, ...(renamed ? { reversed: [] } : {}), ...patch } }));
    if (inspect) await this.inspectTrench();
  }

  /** Reads what the file gives; an answer overtaken by a newer request is dropped. */
  async inspectTrench(): Promise<void> {
    const t = this.store.get().trench;
    const mine = ++this.trenchSeq;
    const set = (p: Partial<TrenchState>) => mine === this.trenchSeq && this.store.set((s) => ({ trench: { ...s.trench, ...p } }));
    if (!t.path) return;
    if (t.nameFrom === "field" && !t.nameField.trim() && t.preview && t.preview.format !== "points") return; // the field is being chosen
    set({ busy: true, error: null });
    try {
      const r = await api.post<TrenchPreview>("/import/trenches/inspect", { lines: linesSpec(t), limit: PREVIEW_LIMIT });
      if (mine !== this.trenchSeq) return;
      logStep(this.log, `${t.name}: ${trenchSummary(r)}; параметры ${JSON.stringify({ ...linesSpec(t), path: t.name })}`);
      for (const w of r.warnings) logStep(this.log, `  замечание: ${w}`);
      set({ preview: r, busy: false });
    } catch (e) {
      if (mine !== this.trenchSeq) return;
      const msg = e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e);
      logError(this.log, msg, e instanceof CoreError ? e : undefined);
      set({ preview: null, busy: false, error: msg });
    }
  }

  private patch(id: number, p: Partial<FileEntry>): void {
    this.store.set((s) => ({ files: s.files.map((f) => (f.id === id ? { ...f, ...p } : f)) }));
  }

  private file(id: number): FileEntry | undefined {
    return this.store.get().files.find((f) => f.id === id);
  }

  async loadSets(): Promise<void> {
    try {
      const r = await api.get<{ sets: SetInfo[] }>("/drillholes/sets");
      this.store.set({ sets: r.sets });
    } catch {
      /* no project: the wizard shows it at the start */
    }
  }

  /** Adds files; each is read and placed on the step of its kind. */
  async add(paths: string[]): Promise<void> {
    for (const path of paths) {
      const entry: FileEntry = {
        id: nextId++, path, name: baseName(path), options: { header_row: 1 }, inspect: null, check: null, kind: null,
        mapping: {}, components: [], attributes: [], force: false, tableName: stem(path), busy: true, error: null,
      };
      this.store.set((s) => ({ files: [...s.files, entry] }));
      logStep(this.log, `Файл: ${path}`);
      await this.reread(entry.id);
    }
  }

  remove(id: number): void {
    this.store.set((s) => ({ files: s.files.filter((f) => f.id !== id) }));
  }

  /** Re-reads the file with its options (encoding, delimiter, header row, sheet), keeping the kind if chosen. */
  async reread(id: number, options?: ReadOptions): Promise<void> {
    const f = this.file(id);
    if (!f) return;
    const opts = options ?? f.options;
    this.patch(id, { options: opts, busy: true, error: null });
    try {
      const inspect = await api.post<InspectResult>("/drillholes/import/inspect", { path: f.path, options: opts });
      logStep(this.log, `${f.name}: ${inspect.summary}`);
      this.patch(id, { inspect });
      await this.check(id, { kind: f.check ? f.kind : null, mapping: null });
    } catch (e) {
      this.fail(id, e);
    }
  }

  /** Reads and checks with the given kind/mapping; null — let the core suggest them. */
  async check(id: number, change: { kind?: TableKind | null; mapping?: Record<string, string> | null; components?: FileEntry["components"]; attributes?: string[]; force?: boolean } = {}): Promise<void> {
    const f = this.file(id);
    if (!f) return;
    this.patch(id, { busy: true, error: null });
    const kind = change.kind === undefined ? f.kind : change.kind;
    const body: Record<string, unknown> = {
      path: f.path,
      options: f.options,
      kind,
      mapping: change.mapping === undefined ? f.mapping : change.mapping,
      components: change.components ?? (change.kind !== undefined || change.mapping === null ? null : f.components),
      attributes: change.attributes ?? f.attributes,
      force: change.force ?? f.force,
      table_name: f.tableName,
    };
    if (body.kind === null) body.mapping = null;
    try {
      const r = await api.post<CheckResult>("/drillholes/import/check", body);
      logStep(this.log, `${f.name}: строк ${r.rows}, тип ${r.kind ?? "не распознан"}, колонки ${JSON.stringify(r.mapping)}; замечаний ${r.issues.length}`);
      for (const i of r.issues) logStep(this.log, `  ${i.severity === "error" ? "ошибка" : "замечание"}: ${i.message}${i.count ? ` (${i.count})` : ""}`);
      this.patch(id, {
        check: r, kind: r.kind, mapping: r.mapping, components: r.components, attributes: r.attributes,
        force: (change.force ?? f.force) && !!r.can_force, busy: false,
      });
    } catch (e) {
      this.fail(id, e);
    }
  }

  setTableName(id: number, name: string): void {
    this.patch(id, { tableName: name });
  }

  private fail(id: number, e: unknown): void {
    const msg = e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e);
    logError(this.log, msg, e instanceof CoreError ? e : undefined);
    this.patch(id, { busy: false, error: msg });
  }

  /** The wizard closed: the core drops the table it kept read for «Проверить». */
  close(): void {
    void api.post("/drillholes/import/forget", {}).catch(() => undefined);
  }

  go(step: StepId): void {
    this.store.set({ step });
    if (step === "import") void this.loadSets();
  }

  /** Starts the job; returns its id or null. */
  async start(): Promise<{ source: string; id: number } | null> {
    const s = this.store.get();
    if (!canStart(s)) return null;
    if (!(await askReplaced(s))) return null; // the person saw what is built from what the import replaces and refused
    this.store.set({ starting: true, error: null });
    if (s.mode === "trenches") return this.submit("/import/trenches", trenchBody(s), (b) => ({ ...b, lines: { ...(b.lines as object), path: s.trench.name } }));
    const body = {
      set_id: s.setMode === "existing" ? s.setId : null,
      set_name: s.setMode === "new" ? s.setName.trim() : null,
      crs: s.crs,
      files: s.files.map((f) => ({
        path: f.path, options: f.options, kind: f.kind, mapping: f.mapping, components: f.components,
        attributes: f.attributes, force: f.force, table_name: f.tableName || null,
      })),
    };
    return this.submit("/drillholes/import", body, (b) => ({ ...b, files: body.files.map((f) => ({ ...f, path: baseName(f.path) })) }));
  }

  private async submit(url: string, body: Record<string, unknown>, shown: (body: Record<string, unknown>) => unknown): Promise<{ source: string; id: number } | null> {
    logStep(this.log, `Запуск импорта: ${JSON.stringify(shown(body))}`);
    try {
      const r = await api.post<{ source: string; id: number }>(url, body);
      logStep(this.log, `Задание №${r.id} поставлено в очередь; журнал продолжится до его окончания`);
      followJob(this.log, r);
      this.store.set({ starting: false, started: true });
      return r;
    } catch (e) {
      const msg = e instanceof CoreError ? e.full : String(e);
      logError(this.log, msg, e instanceof CoreError ? e : undefined);
      this.store.set({ starting: false, error: msg });
      return null;
    }
  }
}
