/**
 * The client of the QAQC setup (ADR 0020 §2–3, §10; errors `{code, message, hint, param?}` in Russian): the data
 * objects (`/qaqc/data`), the reading of a source for the mapping form (`POST /data/inspect`), the import job
 * (`POST /data` → a job of the «Задания» tab), the project setup (`/qaqc/config`) and the CRM reference
 * (`/qaqc/crm`, `POST /crm/read`). Shapes follow ADR 0020 §10; fields the core may leave out are optional here.
 */
import { api, CoreError } from "../../shell/api";
import { jobsStore, refresh as refreshJobs, type Job } from "../jobs/state";

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

/** The fixed fields of a record (ADR 0020 §2) — keys of `mapping.fields`. */
export type FieldKey = "sample_id" | "hole" | "type" | "parent_id" | "crm_id" | "lab" | "batch" | "date" | "seq";

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

export interface ImportCounts {
  no_parent?: number;
  dup_ids?: number;
  bad_dates?: number;
  below_dl?: number;
  above_dl?: number;
  bad_numbers?: number;
}

export type DataSource = { kind: "file"; name?: string; path?: string; sha256?: string; sheet?: string | null } | { kind: "table"; set_id: number; table?: string; name_key?: string; rev?: number; name?: string };

export interface TypeCodeCount {
  code: string;
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
  /** the source table has grown since the import (a snapshot of a set's table) */
  stale?: boolean;
  sha256?: string;
  counts?: ImportCounts;
  /** the mapping of the import (`meta`), for «Обновить» */
  mapping?: Mapping | null;
}

export async function loadData(): Promise<QaqcData[]> {
  const r = await api.get<{ data?: QaqcData[]; items?: QaqcData[] } | QaqcData[]>("/qaqc/data");
  return Array.isArray(r) ? r : (r.data ?? r.items ?? []);
}

export const getData = (id: number): Promise<QaqcData> => api.get<QaqcData>(`/qaqc/data/${id}`);
export const renameData = (id: number, name: string, version: number): Promise<QaqcData> => api.put<QaqcData>(`/qaqc/data/${id}`, { name, expected_version: version });
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
  sample?: unknown[];
}

export interface InspectAnswer {
  rows: number;
  columns: InspectColumn[];
  /** the core's choice of the mapping; the form falls back to `autoMapping` */
  mapping?: Mapping | null;
  /** up to 50 rows, by column name or in column order */
  preview: (Record<string, unknown> | unknown[])[];
  sheets?: string[];
  sheet?: string | null;
  warnings?: string[];
}

const inspectBody = (s: InspectSource): Record<string, unknown> => {
  if (s.kind === "table") return { set_id: s.set_id, table: s.table };
  const { kind: _kind, ...rest } = s;
  return Object.fromEntries(Object.entries(rest).filter(([, v]) => v !== undefined && v !== null && v !== ""));
};

export const inspectSource = (s: InspectSource): Promise<InspectAnswer> => api.post<InspectAnswer>("/qaqc/data/inspect", inspectBody(s));

/** The body of `POST /data`: the source as it was read. */
export function sourceBody(s: InspectSource): Record<string, unknown> {
  return { kind: s.kind, ...inspectBody(s) };
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
  const clean = { ...body, ...(body.replace_id ? {} : { replace_id: undefined }) };
  return startedOf(await api.post<Record<string, unknown>>("/qaqc/data", clean));
}

export interface ImportResult {
  data_id: number;
  rows: number;
  counts: ImportCounts;
}

export const FINISHED = ["done", "failed", "cancelled"];

/** Resolves when the job is finished (the list of the «Задания» tab follows the events of the core). */
export function waitJob(job: Started): Promise<Job> {
  return new Promise((resolve) => {
    const find = () => jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id);
    const check = (): boolean => {
      const j = find();
      if (j && FINISHED.includes(j.status)) {
        off();
        resolve(j);
        return true;
      }
      return false;
    };
    const off = jobsStore.subscribe(() => void check());
    if (!check()) void refreshJobs();
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
/** Reads a CSV / XLSX into rows of the form; nothing is written. */
export const readCrmFile = async (path: string): Promise<Partial<CrmRow>[]> => (await api.post<{ rows?: Partial<CrmRow>[] }>("/qaqc/crm/read", { path })).rows ?? [];
