export interface ColumnInfo {
  name: string;
  dtype: string;
  numeric: boolean;
  sample: unknown[];
}

export interface Component {
  column: string;
  unit: string;
}

export interface Suggestion {
  mapping: Record<string, string>;
  components: Component[];
}

/** Reading parameters for CSV/TXT/Excel; null = detect automatically. */
export interface ReadOptions {
  encoding: string | null;
  delimiter: string | null;
  /** 1-based row with column names (empty rows are not counted); 0 = no header */
  header_row: number;
  sheet: string | null;
}

export interface UploadResponse {
  file_id: string;
  filename: string;
  format: "micromine" | "text" | "excel";
  row_count: number;
  columns: ColumnInfo[];
  detected_kind: string | null;
  suggestions: Record<string, Suggestion>;
  preview: Record<string, unknown>[];
  /** parameters actually used to read the file */
  options: ReadOptions;
  sheets: string[];
  notes: string[];
}

export interface SchemaField {
  key: string;
  label: string;
  required: boolean;
  numeric: boolean;
}

export type Schemas = Record<string, { label: string; fields: SchemaField[] }>;

export interface Issue {
  severity: "error" | "warning";
  code: string;
  message: string;
  field: string | null;
  count: number | null;
}

export interface ValidateRequest {
  file_id: string;
  kind: string;
  mapping: Record<string, string>;
  components: Component[];
  attributes: string[];
}

export interface ValidateResponse {
  ok: boolean;
  row_count: number;
  issues: Issue[];
  columns: string[];
  preview: Record<string, unknown>[];
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.detail?.message === "string") detail = body.detail.message;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const getSchemas = () => request<Schemas>("/api/import/schemas");

export function uploadFile(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadResponse>("/api/import/upload", { method: "POST", body: form });
}

export const rereadFile = (fileId: string, options: ReadOptions) =>
  request<UploadResponse>(`/api/import/${fileId}/options`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });

export const validateMapping = (req: ValidateRequest) =>
  request<ValidateResponse>("/api/import/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

export interface Project {
  id: number;
  name: string;
  created_at: string;
}

export interface Summary {
  counts: Record<string, number>;
  components: { name: string; unit: string }[];
  imports: { id: number; kind: string; filename: string; row_count: number; created_at: string }[];
}

export interface TablePage {
  total: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

export interface CommitResponse {
  job_id: number;
  kind: string;
  inserted: number;
  replaced: number;
  issues: Issue[];
}

export const listProjects = () => request<Project[]>("/api/projects");

export const createProject = (name: string) =>
  request<Project>("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

export const getSummary = (projectId: number) => request<Summary>(`/api/projects/${projectId}/summary`);

export function getTable(projectId: number, kind: string, limit: number, offset: number, holeId?: string | null) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (holeId) params.set("hole_id", holeId);
  return request<TablePage>(`/api/projects/${projectId}/tables/${kind}?${params}`);
}

export const commitImport = (req: ValidateRequest & { project_id: number; filename: string }) =>
  request<CommitResponse>("/api/import/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

export interface AuditRule {
  code: string;
  severity: "error" | "warning" | "info";
  table: string;
  title: string;
  description: string;
  findings: number;
  rows: number;
}

export interface AuditRun {
  run_id: number;
  created_at: string;
  config: Record<string, number>;
  by_severity: Record<string, number>;
  stale: boolean;
  rules: AuditRule[];
}

export interface AuditConfig {
  depth_tolerance: number;
  dogleg_per_30m: number;
  isolated_factor: number;
  z_jump: number;
  assay_outlier_z: number;
}

export interface AuditFinding {
  id: number;
  code: string;
  severity: "error" | "warning" | "info";
  table: string;
  hole_id: string | null;
  count: number;
  message: string;
  details: { examples?: Record<string, unknown>[] };
}

export interface FindingsPage {
  total: number;
  items: AuditFinding[];
}

export const getAudit = (projectId: number) => request<AuditRun | null>(`/api/projects/${projectId}/audit`);

export const runAudit = (projectId: number, config: AuditConfig) =>
  request<AuditRun>(`/api/projects/${projectId}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

export function getFindings(
  projectId: number,
  filters: { severity?: string; table?: string; code?: string; hole_id?: string; hole?: string },
  limit: number,
  offset: number,
) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return request<FindingsPage>(`/api/projects/${projectId}/audit/findings?${params}`);
}

export const auditExportUrl = (projectId: number) => `/api/projects/${projectId}/audit/export.xlsx`;

export interface Hole {
  hole_id: string;
  x: number | null;
  y: number | null;
  z: number | null;
  td: number | null;
  status: string | null;
  survey_count: number;
  assay_count: number;
  error: number;
  warning: number;
  info: number;
}

export interface HoleDetail extends Hole {
  azimuth: number | null;
  dip: number | null;
  date: string | null;
  stage: string | null;
  attributes: Record<string, unknown>;
  findings: { code: string; severity: "error" | "warning" | "info"; count: number; message: string }[];
}

export const listHoles = (projectId: number) => request<Hole[]>(`/api/projects/${projectId}/holes`);

export const getHole = (projectId: number, holeId: string) =>
  request<HoleDetail>(`/api/projects/${projectId}/holes/${encodeURIComponent(holeId)}`);

export const getHealth = () => request<{ status: string; version: string }>("/api/health");

export interface ValueStats {
  count: number;
  min: number | null;
  max: number | null;
  p05: number | null;
  p50: number | null;
  p95: number | null;
}

export interface TrajectoryHole {
  hole_id: string;
  td: number | null;
  source: "survey" | "collar" | "vertical";
  /** Polyline points [x, y, z, measured depth]. */
  path: number[][];
}

export interface Trajectories {
  holes: TrajectoryHole[];
  skipped: string[];
  extent: { min: number[]; max: number[] } | null;
}

export interface IntervalsData {
  component: string;
  unit: string | null;
  stats: ValueStats;
  /** hole_id -> rows [from, to, value, x1, y1, z1, x2, y2, z2] */
  holes: Record<string, number[][]>;
}

export interface HoleLog {
  hole_id: string;
  td: number | null;
  components: { name: string; unit: string }[];
  survey: { depth: number | null; azimuth: number | null; dip: number | null }[];
  intervals: { from_m: number | null; to_m: number | null; sample_id: string | null; values: Record<string, number | null> }[];
  truncated: boolean;
}

export const getTrajectories = (projectId: number) => request<Trajectories>(`/api/projects/${projectId}/trajectories`);

export const getIntervals = (projectId: number, component: string) =>
  request<IntervalsData>(`/api/projects/${projectId}/intervals?component=${encodeURIComponent(component)}`);

export const getHoleLog = (projectId: number, holeId: string) =>
  request<HoleLog>(`/api/projects/${projectId}/holes/${encodeURIComponent(holeId)}/log`);
