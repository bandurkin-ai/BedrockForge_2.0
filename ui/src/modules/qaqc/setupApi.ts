/**
 * The client of the QAQC setup (ADR 0020 §2–3, §10; errors `{code, message, hint, param?}` in Russian): the data
 * objects (`/qaqc/data`), the reading of a source for the mapping form (`POST /data/inspect`), the import job
 * (`POST /data` → a job of the «Задания» tab), the project setup (`/qaqc/config`) and the CRM reference
 * (`/qaqc/crm`, `POST /crm/read`). Shapes follow the core (`qaqc/router.py`, `jobs.py`, `normalize.py`;
 * docs/progress/e14-api.md); fields the core may leave out are optional here. The mapping of the form (`Mapping`:
 * the fixed fields apart, the DL column of an element as `dl`) goes to the core and comes back in its own flat form
 * (`CoreMapping`: roles at the top, `elements[].dl_column`) — `mappingToCore` / `mappingFromCore`.
 */
import { api, CoreError } from "../../shell/api";
import { jobsStore, refresh as refreshJobs, type Job } from "../jobs/state";
import { projectStore } from "../projects/state";

/** The types of control samples (ТЗ 8.4.1). */
export const QAQC_TYPES = ["ORIG", "CRM", "BLANK", "DUP_F", "DUP_C", "DUP_P", "UMP"] as const;
export type QaqcType = (typeof QAQC_TYPES)[number];

export const TYPE_TITLES: Record<QaqcType, string> = {
  ORIG: "ORIG — исходная проба",
  CRM: "CRM — стандарт",
  BLANK: "BLANK — бланк",
  DUP_F: "DUP_F — полевой дубликат",
  DUP_C: "DUP_C — дроблёный дубликат",
  DUP_P: "DUP_P — пульповый дубликат",
  UMP: "UMP — внешний контроль",
};

// ---- data ----

/** The fixed fields of a record (ADR 0020 §2) — keys of `mapping.fields`; the roles of the core (`normalize.ROLES`). */
export const FIELD_KEYS = ["sample_id", "hole", "type", "parent_id", "crm_id", "lab", "batch", "date", "seq"] as const;
export type FieldKey = (typeof FIELD_KEYS)[number];

export interface MappedElement {
  /** the column of the source */
  column: string;
  /** the name of the element in the data (e.g. «Au») */
  name: string;
  unit: string;
  /** the column of the detection limit of this element (optional) */
  dl?: string | null;
}

/** The mapping of the columns sent with `POST /data` and kept in the data's `meta`. */
export interface Mapping {
  fields: Partial<Record<FieldKey, string>>;
  elements: MappedElement[];
  /** other columns kept for the filter «любое поле» (≤ 40) */
  extra: string[];
}

/** An element as the core keeps it: no unit — null; the column of its DL — `dl_column`. */
export interface CoreElement {
  column: string;
  name: string;
  unit: string | null;
  dl_column: string | null;
}

/** The mapping as the core reads (`normalize.parse_mapping`) and answers it (`inspect`, `GET /data/{id}`). */
export type CoreMapping = { v: 1 } & Record<FieldKey, string | null> & { elements: CoreElement[]; extra: string[] };

/** The mapping of the form → the body of the core: every role (none — null), units "" → null, `dl` → `dl_column`. */
export function mappingToCore(m: Mapping): CoreMapping {
  const roles = Object.fromEntries(FIELD_KEYS.map((k) => [k, m.fields[k] || null])) as Record<FieldKey, string | null>;
  return {
    v: 1,
    ...roles,
    elements: m.elements.map((e) => ({ column: e.column, name: e.name.trim(), unit: e.unit.trim() || null, dl_column: e.dl || null })),
    extra: [...m.extra],
  };
}

const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** The flat mapping of the core → the form; null — none (or not an object). An element without a name takes its column. */
export function mappingFromCore(raw: unknown): Mapping | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const fields: Mapping["fields"] = {};
  for (const k of FIELD_KEYS) {
    const col = text(r[k]);
    if (col) fields[k] = col;
  }
  const elements: MappedElement[] = [];
  for (const e of Array.isArray(r.elements) ? r.elements : []) {
    if (!e || typeof e !== "object") continue;
    const x = e as Record<string, unknown>;
    const column = text(x.column);
    if (!column) continue;
    elements.push({ column, name: text(x.name) ?? column, unit: text(x.unit) ?? "", dl: text(x.dl_column) });
  }
  const extra = Array.isArray(r.extra) ? r.extra.filter((c): c is string => typeof c === "string" && !!c) : [];
  return { fields, elements, extra };
}

export interface ImportCounts {
  no_parent?: number;
  dup_ids?: number;
  bad_dates?: number;
  below_dl?: number;
  above_dl?: number;
  bad_numbers?: number;
}

/** The source the core keeps: a file (its path — «Обновить» reads it again) or a table of a set. */
export type DataSource = { kind: "file"; name?: string; path?: string; sha256?: string; sheet?: string | null; header_row?: number } | { kind: "table"; set_id: number; table_id?: number; set_name?: string; table_name?: string; rev?: number };

export interface TypeCodeCount {
  /** null — records with an empty TYPE */
  code: string | null;
  type: QaqcType | null;
  n: number;
}

export interface QaqcData {
  id: number;
  name: string;
  version: number;
  rows: number;
  elements: { name: string; unit: string | null }[];
  fields?: { name: string; kind: string }[];
  date_from?: string | null;
  date_to?: string | null;
  type_codes?: TypeCodeCount[];
  source?: DataSource | null;
  /** the source table has grown since the import (a snapshot of a set's table) or is gone */
  stale?: boolean;
  /** the table of the source is gone */
  source_missing?: boolean;
  sha256?: string;
  counts?: ImportCounts;
  /** the mapping of the import (`meta`), for «Обновить» */
  mapping?: Mapping | null;
}

/** Data as the core answers them: the mapping in the form of the form. */
const dataOf = (d: QaqcData): QaqcData => ("mapping" in d ? { ...d, mapping: mappingFromCore(d.mapping) } : d);

export async function loadData(): Promise<QaqcData[]> {
  const r = await api.get<{ data?: QaqcData[]; items?: QaqcData[] } | QaqcData[]>("/qaqc/data");
  return (Array.isArray(r) ? r : (r.data ?? r.items ?? [])).map(dataOf);
}

export const getData = async (id: number): Promise<QaqcData> => dataOf(await api.get<QaqcData>(`/qaqc/data/${id}`));
export const renameData = async (id: number, name: string, version: number): Promise<QaqcData> => dataOf(await api.put<QaqcData>(`/qaqc/data/${id}`, { name, expected_version: version }));
export const deleteData = (id: number): Promise<unknown> => api.del(`/qaqc/data/${id}`);

/** How a file is read (CSV and the like; a sheet of a book). */
export interface ReadOptions {
  sheet?: string | null;
  encoding?: string | null;
  delimiter?: string | null;
  header_row?: number | null;
}

export type InspectSource = ({ kind: "file"; path: string } & ReadOptions) | { kind: "table"; set_id: number; table: string };

export interface InspectColumn {
  name: string;
  kind: "number" | "text";
  /** up to five values met in the first rows */
  samples?: unknown[];
}

/** How the core read the source: a file (format, encoding, the sheet read and every sheet of a book) or a table. */
export interface InspectInfo {
  format?: string;
  encoding?: string | null;
  delimiter?: string | null;
  header_row?: number | null;
  sheet?: string | null;
  sheets?: string[] | null;
  set_id?: number;
  table_id?: number;
  set_name?: string;
  table_name?: string;
  rev?: number;
}

export interface InspectAnswer {
  kind?: "file" | "table";
  rows: number;
  rows_exact?: boolean;
  columns: InspectColumn[];
  /** the core's choice of the mapping (in the form of the form); the form falls back to `autoMapping` */
  mapping?: Mapping | null;
  /** up to 50 rows, by column name or in column order */
  preview: (Record<string, unknown> | unknown[])[];
  info?: InspectInfo;
  /** the sheets of a book and the one read (from `info`) */
  sheets?: string[];
  sheet?: string | null;
  summary?: string;
  warnings?: string[];
}

const inspectBody = (s: InspectSource): Record<string, unknown> => {
  if (s.kind === "table") return { kind: "table", set_id: s.set_id, table: s.table };
  return Object.fromEntries(Object.entries(s).filter(([, v]) => v !== undefined && v !== null && v !== ""));
};

/** The answer of `POST /data/inspect` for the form: the mapping turned into the form's, the sheets from `info`. */
export function inspectOf(a: Omit<InspectAnswer, "mapping"> & { mapping?: unknown }): InspectAnswer {
  const sheets = a.info?.sheets ?? a.sheets;
  return { ...a, mapping: mappingFromCore(a.mapping), sheets: Array.isArray(sheets) ? sheets : undefined, sheet: a.info?.sheet ?? a.sheet ?? null };
}

export const inspectSource = async (s: InspectSource): Promise<InspectAnswer> => inspectOf(await api.post<InspectAnswer>("/qaqc/data/inspect", inspectBody(s)));

/** The body of `POST /data`: the source as it was read. */
export function sourceBody(s: InspectSource): Record<string, unknown> {
  return inspectBody(s);
}

/** A started job (the shape of every job route: `{source, id}`; ADR 0020 §10 writes `{job_id}` — both are taken). */
export interface Started {
  source: string;
  id: number;
}

export function startedOf(r: Record<string, unknown>): Started {
  const id = typeof r.id === "number" ? r.id : typeof r.job_id === "number" ? r.job_id : NaN;
  if (!Number.isInteger(id)) throw new CoreError(0, "bad_answer", "Ядро не вернуло номер задания.", "Обновите программу.");
  return { source: typeof r.source === "string" ? r.source : "project", id };
}

export interface ImportBody {
  name: string;
  source: Record<string, unknown>;
  mapping: Mapping;
  replace_id?: number | null;
}

export async function startImport(body: ImportBody): Promise<Started> {
  const clean = { ...body, mapping: mappingToCore(body.mapping), ...(body.replace_id ? {} : { replace_id: undefined }) };
  return startedOf(await api.post<Record<string, unknown>>("/qaqc/data", clean));
}

/** «Обновить»: the core imports the data again from its source with its mapping (202, the job `qaqc.import`). */
export async function refreshData(id: number): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/qaqc/data/${id}/refresh`, {}));
}

export interface ImportResult {
  data_id: number;
  rows: number;
  counts: ImportCounts;
}

export const FINISHED = ["done", "failed", "cancelled"];

/** The code of a wait stopped by its `signal`. */
export const WAIT_ABORTED = "aborted";

/**
 * Resolves when the job is finished (the list of the «Задания» tab follows the events of the core). Rejects —
 * and lets go of the lists — when `signal` aborts (`aborted`), when another project opens or the project closes
 * (`project_changed`), or when the job is gone from a list read without an error (`job_lost`: the core restarted).
 */
export function waitJob(job: Started, signal?: AbortSignal): Promise<Job> {
  return new Promise((resolve, reject) => {
    const uid = projectStore.get().current?.uid ?? null;
    let over = false;
    let listed = false;
    const find = () => jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id);
    const end = (fn: () => void) => {
      if (over) return;
      over = true;
      offJobs();
      offProject();
      signal?.removeEventListener("abort", onAbort);
      fn();
    };
    const fail = (code: string, message: string, hint: string) => end(() => reject(new CoreError(0, code, message, hint)));
    function onAbort() {
      fail(WAIT_ABORTED, "Ожидание задания прервано.", "Ход задания — во вкладке «Задания».");
    }
    const check = () => {
      const j = find();
      if (j && FINISHED.includes(j.status)) end(() => resolve(j));
      else if (!j && listed && jobsStore.get().error === null) fail("job_lost", `Задание №${job.id} больше не числится в ядре.`, "Ядро могло перезапуститься: проверьте вкладку «Задания» и данные.");
    };
    const offJobs = jobsStore.subscribe(check);
    const offProject = projectStore.subscribe(() => {
      if ((projectStore.get().current?.uid ?? null) !== uid) fail("project_changed", "Проект закрыт: задание больше не отслеживается.", "Откройте проект снова и посмотрите вкладку «Задания».");
    });
    signal?.addEventListener("abort", onAbort);
    if (signal?.aborted) return onAbort();
    check();
    if (over) return;
    void refreshJobs().then(() => {
      listed = true;
      check();
    });
  });
}

// ---- setup of the project ----

export interface DlRow {
  element: string;
  /** null — any laboratory */
  lab: string | null;
  value: number;
  unit: string;
}

export interface Limits {
  sigma_warn: number;
  sigma_fail: number;
  run: number;
  k_blank: number;
  k_dup: number;
  th_group: number;
  hard_p: number;
  hard_c: number;
  hard_f: number;
  hard_ump: number;
  hard_share: number;
  n_boot: number;
  seed: number;
}

export interface QaqcConfig {
  version: number;
  types: Record<string, QaqcType>;
  dl: DlRow[];
  limits: Limits;
  empty_is_orig: boolean;
}

export interface CrmRow {
  crm_id: string;
  element: string;
  mean: number;
  sd: number;
  unit: string;
  source: string;
}

export interface CrmBook {
  version: number;
  rows: CrmRow[];
}

export const getConfig = (): Promise<QaqcConfig> => api.get<QaqcConfig>("/qaqc/config");
export const putConfig = (c: Omit<QaqcConfig, "version">, expected: number): Promise<QaqcConfig> => api.put<QaqcConfig>("/qaqc/config", { ...c, expected_version: expected });
export const getCrm = (): Promise<CrmBook> => api.get<CrmBook>("/qaqc/crm");
export const putCrm = (rows: CrmRow[], expected: number): Promise<CrmBook> => api.put<CrmBook>("/qaqc/crm", { rows, expected_version: expected });
/** A row of a file of the reference that the check of the core refuses (`row` — from 0) and why. */
export interface CrmProblem {
  row: number;
  message: string;
}

/** `POST /crm/read`: the rows (refused ones kept), their problems, the columns found (field → column of the file). */
export interface CrmRead {
  rows: Partial<CrmRow>[];
  problems: CrmProblem[];
  columns: Record<string, string>;
}

/** Reads a CSV / XLSX into rows of the form; nothing is written. */
export async function readCrmFile(path: string): Promise<CrmRead> {
  const r = await api.post<Partial<CrmRead>>("/qaqc/crm/read", { path });
  return { rows: r.rows ?? [], problems: Array.isArray(r.problems) ? r.problems : [], columns: r.columns && typeof r.columns === "object" ? r.columns : {} };
}
