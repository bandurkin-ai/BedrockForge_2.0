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
  set_id: number;
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

export const createDemoProject = () => request<Project>("/api/projects/demo", { method: "POST" });

export const getSummary = (setId: number) => request<Summary>(`/api/sets/${setId}/summary`);

export function getTable(setId: number, kind: string, limit: number, offset: number, holeId?: string | null) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (holeId) params.set("hole_id", holeId);
  return request<TablePage>(`/api/sets/${setId}/tables/${kind}?${params}`);
}

/** Into an existing set (set_id) or a new one in the project (project_id + set_name). */
export type ImportTarget = { set_id: number } | { project_id: number; set_name: string };

export const commitImport = (req: ValidateRequest & ImportTarget & { filename: string }) =>
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

export const getAudit = (setId: number) => request<AuditRun | null>(`/api/sets/${setId}/audit`);

export const runAudit = (setId: number, config: AuditConfig) =>
  request<AuditRun>(`/api/sets/${setId}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

export function getFindings(
  setId: number,
  filters: { severity?: string; table?: string; code?: string; hole_id?: string; hole?: string },
  limit: number,
  offset: number,
) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return request<FindingsPage>(`/api/sets/${setId}/audit/findings?${params}`);
}

export const auditExportUrl = (setId: number) => `/api/sets/${setId}/audit/export.xlsx`;

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

export const listHoles = (setId: number) => request<Hole[]>(`/api/sets/${setId}/holes`);

export const getHole = (setId: number, holeId: string) =>
  request<HoleDetail>(`/api/sets/${setId}/holes/${encodeURIComponent(holeId)}`);

export const getHealth = () => request<{ status: string; version: string }>("/api/health");

// ---- Claude assistant ----

export interface AssistantStatus {
  /** false: the server has no Claude API credentials */
  configured: boolean;
  model: string;
}

export type AssistantBlock =
  | { type: "text"; text: string }
  /** A data tool the server already ran for Claude. */
  | { type: "tool"; name: string; title: string; input: Record<string, unknown>; ok: boolean };

export interface AssistantToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface AssistantToolResult {
  tool_use_id: string;
  content: string;
  is_error: boolean;
}

export interface AssistantTurn {
  conversation_id: string;
  blocks: AssistantBlock[];
  /** Interface commands Claude wants the browser to run; answer with `tool_results`. */
  pending: AssistantToolCall[];
  done: boolean;
  notice: string | null;
}

export interface AssistantRequest {
  conversation_id: string | null;
  text?: string;
  context?: Record<string, unknown>;
  tool_results?: AssistantToolResult[];
  ui_tools: unknown[];
}

export const getAssistantStatus = () => request<AssistantStatus>("/api/assistant/status");

export const assistantChat = (projectId: number, body: AssistantRequest, signal?: AbortSignal) =>
  request<AssistantTurn>(`/api/projects/${projectId}/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

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


export const getTrajectories = (setId: number) => request<Trajectories>(`/api/sets/${setId}/trajectories`);

export const getIntervals = (setId: number, component: string) =>
  request<IntervalsData>(`/api/sets/${setId}/intervals?component=${encodeURIComponent(component)}`);


// ---- Project tree ----

export type DatasetKind = "drillholes" | "lines" | "wireframes" | "blocks" | "rasters";

export interface WireframeInfo {
  id: number;
  name: string;
  color: string;
  point_count: number;
  triangle_count: number;
  extent: { min: number[]; max: number[] };
  stats: { area: number; volume: number | null; closed: boolean; degenerate: number; open_edges: number; attributes?: Record<string, unknown> };
}

export interface LineLayerInfo {
  id: number;
  name: string;
  color: string;
  line_count: number;
  point_count: number;
  extent: { min: number[]; max: number[] };
  stats: { length: number; closed: number; flat: boolean };
}

export interface RasterCheck {
  source: string;
  max: number;
  points: { label: string; col: number; row: number; dx: number; dy: number; dz?: number; d: number }[];
}

export interface RasterInfo {
  id: number;
  name: string;
  width: number;
  height: number;
  georef: {
    affine: number[];
    source: string;
    crs: string | null;
    pixel_size: number;
    rotation: number;
    /** top-left, top-right, bottom-right, bottom-left [x, y] */
    corners: number[][];
    /** "vertical": the image stands in a vertical plane (a section scan) */
    placement?: "plan" | "vertical";
    /** vertical rasters: corners [x, y, z] in the same order */
    corners3d?: number[][];
    /** vertical rasters: azimuth of the image's left-to-right direction */
    azimuth?: number;
    checks: RasterCheck[];
    notes: string[];
  };
  extent: { min: number[]; max: number[] };
  /** null: under the lowest object of the scene */
  elevation: number | null;
  opacity: number;
}

export interface Dataset {
  id: number;
  kind: DatasetKind;
  name: string;
  created_at: string;
  meta: { format?: string; filename?: string; sidecars?: string[] };
  counts: { collar: number; survey: number; assay: number } | null;
  components: { name: string; unit: string }[] | null;
  audit: { error: number; warning: number; info: number } | null;
  wireframes: WireframeInfo[] | null;
  lines: LineLayerInfo[] | null;
  rasters: RasterInfo[] | null;
}

export interface WireframeGeometry {
  id: number;
  origin: number[];
  /** base64 float32 x, y, z relative to origin */
  vertices: string;
  /** base64 uint32 triangle indices */
  triangles: string;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listDatasets = (projectId: number) => request<Dataset[]>(`/api/projects/${projectId}/datasets`);

export const createSet = (projectId: number, name: string) =>
  request<Dataset>(`/api/projects/${projectId}/sets`, { method: "POST", ...json({ name }) });

export const renameDataset = (id: number, name: string) =>
  request<Dataset>(`/api/datasets/${id}`, { method: "PATCH", ...json({ name }) });

export async function deleteDataset(id: number): Promise<void> {
  const response = await fetch(`/api/datasets/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Ошибка ${response.status}`);
}

export function uploadWireframes(projectId: number, file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return request<Dataset>(`/api/projects/${projectId}/wireframes`, { method: "POST", body: form });
}

export const getWireframeGeometry = (id: number) => request<WireframeGeometry>(`/api/wireframes/${id}/geometry`);

export const patchWireframe = (id: number, patch: { name?: string; color?: string }) =>
  request<WireframeInfo>(`/api/wireframes/${id}`, { method: "PATCH", ...json(patch) });

export async function deleteWireframe(id: number): Promise<void> {
  const response = await fetch(`/api/wireframes/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Ошибка ${response.status}`);
}

// ---- Lines and rasters ----

export interface LineGeometry {
  id: number;
  origin: number[];
  /** base64 float32 x, y, z relative to origin */
  vertices: string;
  /** base64 uint32 start of every line, plus the end */
  offsets: string;
  /** base64 uint8 per line */
  closed: string;
}

export function uploadLines(projectId: number, file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return request<Dataset>(`/api/projects/${projectId}/lines`, { method: "POST", body: form });
}

export const getLineGeometry = (id: number) => request<LineGeometry>(`/api/lines/${id}/geometry`);

export const patchLines = (id: number, patch: { name?: string; color?: string }) =>
  request<LineLayerInfo>(`/api/lines/${id}`, { method: "PATCH", ...json(patch) });

export async function deleteLines(id: number): Promise<void> {
  const response = await fetch(`/api/lines/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Ошибка ${response.status}`);
}

/** A raster standing in a vertical plane: left edge (x1, y1), right edge (x2, y2), from z_top down to z_bottom. */
export interface VerticalPlacement {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  z_top: number;
  z_bottom: number;
}

/** Images and their georeference files in one request; every image becomes a dataset.
 * Images without a georeference stand on `section`, if given. */
export function uploadRasters(projectId: number, files: File[], section?: VerticalPlacement | null): Promise<Dataset[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  if (section) form.append("section", JSON.stringify(section));
  return request<Dataset[]>(`/api/projects/${projectId}/rasters`, { method: "POST", body: form });
}

export const rasterImageUrl = (id: number) => `/api/rasters/${id}/image`;

export interface RasterPatch {
  name?: string;
  elevation?: number;
  clear_elevation?: boolean;
  opacity?: number;
  vertical?: VerticalPlacement;
}

export const patchRaster = (id: number, patch: RasterPatch) =>
  request<RasterInfo>(`/api/rasters/${id}`, { method: "PATCH", ...json(patch) });
