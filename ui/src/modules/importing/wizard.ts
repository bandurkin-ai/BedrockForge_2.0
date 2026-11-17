/**
 * State of the wizard «Импорт скважин» (ТЗ 7.7): steps «Устья» → «Инклинометрия» → «Интервалы и события» →
 * «Импорт». Files added at any step go to the step of their detected kind. No React here.
 */
import { api, CoreError } from "../../shell/api";
import { logError, logStep, type OpLog } from "../../shell/opLog";
import { followJob } from "../jobs/follow";
import { Store } from "../../shell/store";

export type TableKind = "collar" | "survey" | "assay" | "interval" | "event";
export type StepId = "collar" | "survey" | "tables" | "import";

export const STEPS: { id: StepId; title: string }[] = [
  { id: "collar", title: "Устья" },
  { id: "survey", title: "Инклинометрия" },
  { id: "tables", title: "Интервалы и события" },
  { id: "import", title: "Импорт" },
];

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
  step: StepId;
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

export function initialState(): WizardState {
  return { step: "collar", files: [], setMode: "new", setId: null, setName: "Скважины", crs: null, crsName: null, sets: [], starting: false, started: false, error: null };
}

let nextId = 1;
const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;
const stem = (p: string) => baseName(p).replace(/\.[^.]+$/, "");

export function filesOf(s: WizardState, step: StepId): FileEntry[] {
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

export function canStart(s: WizardState): boolean {
  if (!s.files.length || !s.files.every(ready) || s.starting || clashOf(s)) return false;
  return s.setMode === "existing" ? s.setId !== null : s.setName.trim().length > 0;
}

export class Wizard {
  readonly store = new Store<WizardState>(initialState());

  constructor(readonly log: OpLog) {}

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
    this.store.set({ starting: true, error: null });
    const body = {
      set_id: s.setMode === "existing" ? s.setId : null,
      set_name: s.setMode === "new" ? s.setName.trim() : null,
      crs: s.crs,
      files: s.files.map((f) => ({
        path: f.path, options: f.options, kind: f.kind, mapping: f.mapping, components: f.components,
        attributes: f.attributes, force: f.force, table_name: f.tableName || null,
      })),
    };
    logStep(this.log, `Запуск импорта: ${JSON.stringify({ ...body, files: body.files.map((f) => ({ ...f, path: baseName(f.path) })) })}`);
    try {
      const r = await api.post<{ source: string; id: number }>("/drillholes/import", body);
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
