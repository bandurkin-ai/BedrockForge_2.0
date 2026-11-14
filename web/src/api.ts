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

export interface UploadResponse {
  file_id: string;
  filename: string;
  format: string;
  row_count: number;
  columns: ColumnInfo[];
  detected_kind: string | null;
  suggestions: Record<string, Suggestion>;
  preview: Record<string, unknown>[];
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

export const getTable = (projectId: number, kind: string, limit: number, offset: number) =>
  request<TablePage>(`/api/projects/${projectId}/tables/${kind}?limit=${limit}&offset=${offset}`);

export const commitImport = (req: ValidateRequest & { project_id: number; filename: string }) =>
  request<CommitResponse>("/api/import/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
