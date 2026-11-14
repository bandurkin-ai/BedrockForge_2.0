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
