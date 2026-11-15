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
      else if (Array.isArray(body.detail) && typeof body.detail[0]?.msg === "string") detail = body.detail[0].msg.replace(/^Value error, /, "");
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

export const commitImport = (req: ValidateRequest & ImportTarget & { filename: string; table_name?: string }) =>
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
  stats: {
    area: number;
    volume: number | null;
    closed: boolean;
    degenerate: number;
    open_edges: number;
    /** "outward" / "inward" for closed solids */
    orientation?: string | null;
    nonmanifold_edges?: number;
    /** set by the checks job after the import */
    checked?: boolean;
    orientation_conflicts?: number;
    self_intersections?: { pairs: number; samples: number[][]; big_triangles: number };
    attributes?: Record<string, unknown>;
  };
  /** simplified copies for distant views */
  lod_levels: number;
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
  /** "/"-separated folder inside the kind's group of the tree; "" at the top */
  folder: string;
  created_at: string;
  meta: { format?: string; filename?: string; sidecars?: string[] };
  counts: { collar: number; survey: number; assay: number } | null;
  components: { name: string; unit: string }[] | null;
  audit: { error: number; warning: number; info: number } | null;
  /** drillholes: saved charts */
  charts?: { id: number; kind: SavedChartKind; name: string }[] | null;
  /** drillholes: interval tables besides the assays (lithology, alteration, ...) */
  interval_tables?: { id: number; name: string; row_count: number; kind?: "interval" | "event" }[] | null;
  wireframes: WireframeInfo[] | null;
  lines: LineLayerInfo[] | null;
  rasters: RasterInfo[] | null;
}

/** Where a wireframe's render buffers are (raw little-endian arrays, /api/blobs/{sha256}). */
export interface WireframeGeometry {
  id: number;
  /** integer metres near the centre of the extent */
  origin: number[];
  vertex_count: number;
  triangle_count: number;
  /** URL of float32 x, y, z relative to origin */
  vertices: string;
  /** URL of uint32 triangle indices */
  triangles: string;
  /** parts of the triangle buffer in spatial order: [first triangle, count, min xyz, max xyz] relative to origin */
  chunks: number[][];
  /** simplified copies, coarser and coarser */
  lods: { level: number; triangles: number; vertex_count: number; vertices: string; triangles_url: string }[];
}

export interface SectionResult {
  id: number;
  origin: number[];
  polylines: { closed: boolean; points: number[][] }[];
}

/** Exact sections of wireframes by a plane (float64 on the server), relative to each wireframe's origin. */
export const getSections = (wireframeIds: number[], point: number[], normal: number[], signal?: AbortSignal) =>
  request<{ sections: SectionResult[]; ms: number }>("/api/sections", { method: "POST", ...json({ wireframe_ids: wireframeIds, point, normal }), signal });

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listDatasets = (projectId: number) => request<Dataset[]>(`/api/projects/${projectId}/datasets`);

export const createSet = (projectId: number, name: string) =>
  request<Dataset>(`/api/projects/${projectId}/sets`, { method: "POST", ...json({ name }) });

export const renameDataset = (id: number, name: string) =>
  request<Dataset>(`/api/datasets/${id}`, { method: "PATCH", ...json({ name }) });

export const moveDataset = (id: number, folder: string) =>
  request<Dataset>(`/api/datasets/${id}`, { method: "PATCH", ...json({ folder }) });

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
  vertex_count: number;
  line_count: number;
  /** URL of float32 x, y, z relative to origin */
  vertices: string;
  /** URL of uint32 start of every line, plus the end */
  offsets: string;
  /** URL of uint8 per line */
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

// ---- Statistics ----

export interface DeclusParams {
  method: "cell" | "polygonal";
  /** cell sizes; null = from the data extent */
  cmin: number | null;
  cmax: number | null;
  ncell: number;
  noff: number;
  minmax: "min" | "max";
  nx: number;
  ny: number;
}

export interface DeclusResult {
  component: string;
  method: "cell" | "polygonal";
  count: number;
  naive_mean: number;
  declustered_mean: number;
  weight_min: number;
  weight_max: number;
  /** cell method: element 0 is size 0 (the naive mean) */
  cell_sizes: (number | null)[] | null;
  means: (number | null)[] | null;
  cmin: number | null;
  cmax: number | null;
  best_cell_size: number | null;
}

export interface NscoreResult {
  component: string;
  count: number;
  values: number[];
  scores: number[];
  zmin: number;
  zmax: number;
  sample_scores: number[];
}

export interface DirectionParams {
  azimuth: number;
  azimuth_tol: number;
  bandwidth: number | null;
}

export interface VariogramStructure {
  it: "spherical" | "exponential" | "gaussian";
  cc: number;
  hmaj: number;
  hmin: number | null;
  azi: number;
}

export interface VariogramModelParams {
  nug: number;
  structures: VariogramStructure[];
}

export interface VariogramRequest {
  lag: number;
  nlag: number;
  directions: DirectionParams[];
  standardize: boolean;
  model: VariogramModelParams | null;
}

export interface VariogramDirection extends DirectionParams {
  dist: (number | null)[];
  gamma: (number | null)[];
  npairs: number[];
  model_gamma: number[] | null;
}

export interface VariogramResult {
  component: string;
  count: number;
  variance: number;
  lags: number[];
  directions: VariogramDirection[];
}

export interface VariogramMapRequest {
  nxlag: number;
  nylag: number;
  dxlag: number;
  dylag: number;
  minnp: number;
  standardize: boolean;
}

export interface VariogramMapResult {
  component: string;
  count: number;
  x: number[];
  y: number[];
  gamma: (number | null)[][];
  npairs: number[][];
}

export interface BootstrapRequest {
  statistic: "mean" | "median" | "std" | "cv" | "p10" | "p90";
  n_boot: number;
  confidence: number;
  declus: DeclusParams | null;
  seed: number | null;
}

export interface BootstrapResult {
  component: string;
  statistic: string;
  count: number;
  n_boot: number;
  observed: number;
  mean: number;
  std: number;
  confidence: number;
  ci_low: number;
  ci_high: number;
  edges: number[];
  counts: number[];
}

export interface LocalRequest {
  nx: number;
  ny: number;
  nreal: number;
  k: number;
  power: number;
  /** null = 25 % of the larger plan extent */
  radius: number | null;
  threshold: number | null;
  seed: number | null;
}

export type Grid = (number | null)[][];

export interface LocalResult {
  component: string;
  count: number;
  holes: number;
  nreal: number;
  radius: number;
  x: number[];
  y: number[];
  mean: Grid;
  std: Grid;
  p10: Grid;
  p90: Grid;
  prob_exceed: Grid | null;
}

const postJson = <T>(url: string, body: unknown) =>
  request<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const runDeclustering = (setId: number, component: string, params: DeclusParams) =>
  postJson<DeclusResult>(`/api/sets/${setId}/stats/declustering`, { component, ...params });

/** `declus` = null: equal weights. */
export const runNscore = (setId: number, component: string, declus: DeclusParams | null) =>
  postJson<NscoreResult>(`/api/sets/${setId}/stats/nscore`, { component, declus });

export const runVariogram = (setId: number, component: string, req: VariogramRequest) =>
  postJson<VariogramResult>(`/api/sets/${setId}/stats/variogram`, { component, ...req });

export const runVariogramMap = (setId: number, component: string, req: VariogramMapRequest) =>
  postJson<VariogramMapResult>(`/api/sets/${setId}/stats/variogram-map`, { component, ...req });

export const runBootstrap = (setId: number, component: string, req: BootstrapRequest) =>
  postJson<BootstrapResult>(`/api/sets/${setId}/stats/bootstrap`, { component, ...req });

export const runLocalUncertainty = (setId: number, component: string, req: LocalRequest) =>
  postJson<LocalResult>(`/api/sets/${setId}/stats/local-uncertainty`, { component, ...req });

// ---- Resumable uploads and background jobs (stage 7) ----

export interface UploadInfo {
  id: string;
  project_id: number;
  filename: string;
  size: number;
  received: number;
  status: "receiving" | "complete";
  chunk_size: number;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface JobInfo {
  id: number;
  project_id: number | null;
  kind: string;
  title: string;
  status: JobStatus;
  progress: number;
  message: string;
  result: { dataset_ids?: number[]; kind?: string } | null;
  error: string | null;
  attempts: number;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
}

export interface JobDetail extends JobInfo {
  params: Record<string, unknown>;
  log: { t: string; level: "info" | "warning" | "error"; text: string }[];
}

export const isFinished = (job: JobInfo) => job.status === "done" || job.status === "failed" || job.status === "cancelled";

const RESUME_PREFIX = "bf.upload:";
const RETRIES = 6;

function resumeKey(projectId: number, file: File) {
  return `${RESUME_PREFIX}${projectId}:${file.name}:${file.size}:${file.lastModified}`;
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

async function sha256Hex(data: ArrayBuffer): Promise<string | null> {
  // crypto.subtle exists only in secure contexts (https, localhost); elsewhere the check is skipped.
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  const digest = await subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("Отменено", "AbortError"));
    }, { once: true });
  });

/** Where to continue an upload of this file, if an earlier attempt was interrupted. */
async function resumable(projectId: number, file: File): Promise<UploadInfo | null> {
  const id = storage()?.getItem(resumeKey(projectId, file));
  if (!id) return null;
  const response = await fetch(`/api/uploads/${id}`);
  if (!response.ok) {
    storage()?.removeItem(resumeKey(projectId, file));
    return null;
  }
  const info = (await response.json()) as UploadInfo;
  return info.size === file.size && info.project_id === projectId ? info : null;
}

/**
 * Sends a file of any size (up to the server's limit, 4 GB by default) in chunks with checksums.
 * Every chunk is retried with a growing pause; after a page reload or a lost connection the
 * same file continues where it stopped.
 */
export async function uploadResumable(
  projectId: number,
  file: File,
  onProgress?: (sent: number, total: number) => void,
  signal?: AbortSignal,
): Promise<UploadInfo> {
  let info = await resumable(projectId, file);
  if (!info) {
    info = await request<UploadInfo>(`/api/projects/${projectId}/uploads`, {
      method: "POST",
      ...json({ filename: file.name, size: file.size }),
    });
    storage()?.setItem(resumeKey(projectId, file), info.id);
  }
  let offset = info.received;
  onProgress?.(offset, file.size);
  while (offset < file.size) {
    const chunk = await file.slice(offset, Math.min(file.size, offset + info.chunk_size)).arrayBuffer();
    const checksum = await sha256Hex(chunk);
    for (let attempt = 0; ; attempt++) {
      signal?.throwIfAborted();
      let response: Response | null = null;
      try {
        response = await fetch(`/api/uploads/${info.id}?offset=${offset}`, {
          method: "PUT",
          body: chunk,
          headers: { "Content-Type": "application/octet-stream", ...(checksum ? { "X-Chunk-SHA256": checksum } : {}) },
          signal,
        });
      } catch (e) {
        if ((e as Error).name === "AbortError") throw e;
      }
      if (response?.ok) {
        info = (await response.json()) as UploadInfo;
        offset = info.received;
        break;
      }
      if (response?.status === 409) {
        // The server has a different position (a retried chunk did arrive): continue from there.
        const body = await response.json().catch(() => null);
        const received = body?.detail?.received;
        if (typeof received === "number") {
          offset = received;
          break;
        }
      }
      if (response && response.status < 500 && response.status !== 409 && response.status !== 422) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : `Ошибка загрузки: ${response.status}`);
      }
      if (attempt >= RETRIES) throw new Error(`Загрузка «${file.name}» прервалась: нет связи с сервером`);
      await sleep(1000 * 2 ** attempt, signal);
    }
    onProgress?.(offset, file.size);
  }
  storage()?.removeItem(resumeKey(projectId, file));
  return info;
}

export async function deleteUpload(id: string): Promise<void> {
  await fetch(`/api/uploads/${id}`, { method: "DELETE" });
}

export interface ImportRequest {
  uploads: string[];
  kind?: "wireframes" | "lines" | "rasters" | "blocks";
  folder?: string;
  section?: VerticalPlacement | null;
}

export const startImport = (projectId: number, body: ImportRequest) =>
  request<JobInfo>(`/api/projects/${projectId}/imports`, { method: "POST", ...json(body) });

export const listJobs = (projectId: number) => request<JobInfo[]>(`/api/projects/${projectId}/jobs`);
export const getJob = (id: number) => request<JobDetail>(`/api/jobs/${id}`);
export const cancelJob = (id: number) => request<JobInfo>(`/api/jobs/${id}/cancel`, { method: "POST" });
/** A failed or cancelled job again, with the same uploaded files. */
export const retryJob = (id: number) => request<JobInfo>(`/api/jobs/${id}/retry`, { method: "POST" });

/** Every change of the project's jobs, as it happens; returns the unsubscribe function. */
export function subscribeJobs(projectId: number, onJob: (job: JobInfo) => void): () => void {
  if (typeof EventSource === "undefined") return () => undefined;
  const source = new EventSource(`/api/projects/${projectId}/jobs/events`);
  source.addEventListener("job", (e) => onJob(JSON.parse((e as MessageEvent).data) as JobInfo));
  return () => source.close();
}

export async function deleteProject(id: number): Promise<void> {
  const response = await fetch(`/api/projects/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Ошибка ${response.status}`);
}

/** What the server found in an uploaded file (import wizard). */
export interface DetectedFile {
  upload: string;
  filename: string;
  size: number;
  status: "receiving" | "complete";
  kind: "wireframes" | "lines" | "rasters" | "blocks" | "sidecar" | "drillholes" | "unknown";
  format: string;
  note: string;
  /** images: their georeference files in this batch */
  sidecars?: string[];
  preview?: {
    summary?: string;
    error?: string;
    items?: { name: string; points?: number | null; triangles?: number | null; entities?: Record<string, number> }[];
    width?: number;
    height?: number;
  };
}

export const detectUploads = (projectId: number, uploads: string[]) =>
  request<DetectedFile[]>(`/api/projects/${projectId}/detect`, { method: "POST", ...json({ uploads }) });

/** Checks and levels of detail of a wireframe file, again (a job). */
export const checkWireframes = (datasetId: number) => request<JobInfo>(`/api/datasets/${datasetId}/check`, { method: "POST" });

// ---- Histogram of assay fields ----

export interface NumericField {
  /** "comp:AU", "attr:DENSITY" or "length" */
  id: string;
  name: string;
  kind: "component" | "attribute" | "derived";
  unit: string;
  count: number;
}

export const getNumericFields = (setId: number) => request<NumericField[]>(`/api/sets/${setId}/stats/fields`);

export type WeightMode = "none" | "length" | "field" | "length_field";

export interface HistogramParams {
  field: string;
  weight: WeightMode;
  weight_field?: string | null;
  log: boolean;
  bin_width?: number | null;
  bins?: number | null;
  start?: number | null;
  end?: number | null;
  negatives: "keep" | "exclude" | "half_abs";
  exclude_zero: boolean;
  /** cuts at both ends: "cap" replaces values beyond by the cut, "exclude" leaves the samples out */
  bottom_cut?: number | null;
  bottom_mode?: CutMode;
  top_cut?: number | null;
  top_mode?: CutMode;
}

export type CutMode = "cap" | "exclude";

export interface Describe {
  count: number;
  weight_sum: number;
  min: number;
  max: number;
  mean: number;
  variance: number;
  std: number;
  cv: number | null;
  skewness: number | null;
  median: number;
  percentiles: Record<string, number>;
  log?: { count: number; mean_ln: number; variance_ln: number; geometric_mean: number };
}

export interface HistogramResult {
  field: string;
  log: boolean;
  weighted: boolean;
  counts: { rows: number; missing: number; bad_weight: number; negative: number; zero_excluded: number; bottom_capped: number; bottom_excluded: number; capped: number; top_excluded: number; nonpositive_log: number; used: number };
  stats: Describe;
  stats_unweighted: Describe | null;
  bins: {
    edges: number[];
    counts: number[];
    weights: number[];
    percent: number[];
    cumulative_percent: number[];
    width: number;
    below: { count: number; weight: number };
    above: { count: number; weight: number };
  };
  conventions: string;
}

export const getHistogram = (setId: number, params: HistogramParams, signal?: AbortSignal) =>
  request<HistogramResult>(`/api/sets/${setId}/stats/histogram`, { method: "POST", ...json(params), signal });

// ---- Q-Q plot ----

export interface SampleParams {
  set_id: number;
  field: string;
  weight: WeightMode;
  weight_field?: string | null;
  negatives: "keep" | "exclude" | "half_abs";
  exclude_zero: boolean;
  top_cut?: number | null;
  holes?: string[] | null;
}

export interface QQSideInfo {
  set_id: number;
  field: string;
  weighted: boolean;
  counts: { rows: number; missing: number; bad_weight: number; negative: number; zero_excluded: number; capped: number; used: number };
  stats: Describe;
}

export interface QQResult {
  mode: "sample" | "theory";
  /** the sample (on the X axis against a sample, on the Y axis against a distribution) */
  x: QQSideInfo;
  y: QQSideInfo | null;
  /** [x, y, probability %] */
  points: [number, number, number][];
  extremes?: { x: [number, number]; y: [number, number] };
  ks?: number;
  distribution?: { name: "normal" | "lognormal"; mu: number; sigma: number; dropped_nonpositive: number };
  ppcc?: number | null;
}

export const getQQ = (
  body: { x: SampleParams; y: ({ kind: "sample" } & SampleParams) | { kind: "normal" | "lognormal" }; quantiles: number },
  signal?: AbortSignal,
) => request<QQResult>("/api/stats/qq", { method: "POST", ...json(body), signal });

// ---- Dendrogram ----

export interface DendrogramParams {
  fields?: string[] | null;
  method: "pearson" | "spearman";
  log: boolean;
  linkage: "average" | "complete" | "single" | "weighted";
  distance: "1-r" | "1-|r|";
  missing: "pairwise" | "complete";
  weight: "none" | "length";
  negatives: "keep" | "exclude" | "half_abs";
  exclude_zero: boolean;
  holes?: string[] | null;
}

export interface DendrogramResult {
  fields: string[];
  names: string[];
  units: string[];
  correlation: (number | null)[][];
  pairs: number[][];
  rows: number;
  dropped_nonpositive_log: number;
  /** scipy linkage: [a, b, distance, size] per merge */
  linkage: number[][];
  leaves: string[];
  /** leaf order as indices into `names` */
  order: number[];
  icoord: number[][];
  dcoord: number[][];
  max_distance: number;
}

export const getDendrogram = (setId: number, params: DendrogramParams, signal?: AbortSignal) =>
  request<DendrogramResult>(`/api/sets/${setId}/stats/dendrogram`, { method: "POST", ...json(params), signal });

// ---- Saved charts ----

/** "downhole": a display of data along the holes in the 3D view (grades, lithology, events) */
export type SavedChartKind = "histogram" | "qq" | "dendrogram" | "striplog" | "downhole";

export interface SavedChart {
  id: number;
  set_id: number;
  kind: SavedChartKind;
  name: string;
  auto_name: boolean;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export const createChart = (setId: number, kind: SavedChartKind, name: string, settings: unknown) =>
  request<SavedChart>(`/api/sets/${setId}/charts`, { method: "POST", ...json({ kind, name, settings }) });
export const getChart = (id: number) => request<SavedChart>(`/api/charts/${id}`);
export const patchChart = (id: number, patch: { name?: string; settings?: unknown; auto_name?: string }) =>
  request<SavedChart>(`/api/charts/${id}`, { method: "PATCH", ...json(patch) });
export async function deleteChart(id: number): Promise<void> {
  const response = await fetch(`/api/charts/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Ошибка ${response.status}`);
}

// ---- block models (dataset kind "blocks")

export interface BlockAttribute {
  key: string;
  name: string;
  kind: "number" | "category";
  unit?: "g/t" | "%" | "ppm" | "ppb" | null;
  labels?: string[];
  source: string;
  stats: {
    count: number;
    empty: number;
    min?: number;
    max?: number;
    mean?: number;
    mean_by_volume?: number | null;
    categories?: number;
    top?: { label: string; count: number; volume: number }[];
  };
}

export interface BlockCheck {
  level: "error" | "warning" | "info";
  code: string;
  message: string;
  count: number | null;
}

export interface BlockModelMeta {
  format: string;
  filename: string;
  block_count: number;
  volume: number;
  extent: { min: number[]; max: number[] };
  render_origin: number[];
  grid: { origin: number[]; parent: number[]; counts: number[]; subblocked: boolean; parents_used: number; rotation: number; from_file: boolean };
  roles: Record<string, string>;
  attributes: BlockAttribute[];
  checks: BlockCheck[];
  notes: string[];
  report: { density?: string | null; density_value?: number | null; grades?: string[]; cutoff_field?: string | null; cutoffs?: number[] };
}

export const blockMeta = (d: Dataset) => d.meta as unknown as BlockModelMeta;

async function binary(url: string): Promise<ArrayBuffer> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Ошибка ${r.status}`);
  return r.arrayBuffer();
}

/** Centre (relative to meta.render_origin) and size of every block, 6 float32 each. */
export const getBlockRender = async (id: number) => new Float32Array(await binary(`/api/blockmodels/${id}/render`));
/** An attribute per block as float32 (categories: code; empty: NaN). */
export const getBlockValues = async (id: number, key: string) => new Float32Array(await binary(`/api/blockmodels/${id}/values/${encodeURIComponent(key)}`));

export interface BlockInfo {
  index: number;
  centre: number[];
  size: number[];
  values: Record<string, number | string | null>;
}

export const getBlock = (id: number, index: number) => request<BlockInfo>(`/api/blockmodels/${id}/blocks/${index}`);

export interface BlockReportParams {
  density?: string | null;
  density_value?: number | null;
  grades: string[];
  cutoff_field?: string | null;
  cutoffs: number[];
  filters?: { key: string; labels?: string[]; min?: number | null; max?: number | null }[];
  group_by?: string | null;
  remember?: boolean;
}

export interface BlockReportRow {
  group: string | null;
  cutoff: number | null;
  blocks: number;
  volume: number;
  tonnes: number;
  grades: Record<string, { grade: number | null; metal: { value: number; unit: string; kg?: number } | null; missing_tonnes: number }>;
}

export interface BlockReport {
  rows: BlockReportRow[];
  excluded_no_density: number;
  grades: { key: string; name: string; unit: string | null }[];
  density: string | number | null;
}

export const blockReport = (id: number, params: BlockReportParams, signal?: AbortSignal) =>
  request<BlockReport>(`/api/blockmodels/${id}/report`, { method: "POST", ...json(params), signal });

export const setBlockUnit = (id: number, key: string, unit: BlockAttribute["unit"]) =>
  request<{ key: string; unit: string | null }>(`/api/blockmodels/${id}/attributes/${encodeURIComponent(key)}`, { method: "PATCH", ...json({ unit }) });

// ---- interval tables and the downhole log

export interface IntervalColumn {
  name: string;
  kind: "number" | "text";
  unit: string;
}

export interface IntervalTableInfo {
  /** "assay" for the set's assays, else the table id as text */
  key: string;
  id: number | null;
  name: string;
  builtin: boolean;
  row_count: number;
  columns: IntervalColumn[];
  /** "event": rows have one depth, no thickness */
  kind?: "interval" | "event";
}

export const getIntervalTables = (setId: number) => request<IntervalTableInfo[]>(`/api/sets/${setId}/interval-tables`);
export const renameIntervalTable = (id: number, name: string) =>
  request<{ id: number; name: string }>(`/api/interval-tables/${id}`, { method: "PATCH", ...json({ name }) });
export const deleteIntervalTable = (id: number) => request<void>(`/api/interval-tables/${id}`, { method: "DELETE" });

export interface DownholeInterval {
  from: number | null;
  to: number | null;
  values: Record<string, number | string | null>;
}

export interface DownholeData {
  tables: { key: string; name: string; columns: IntervalColumn[]; kind?: "interval" | "event" }[];
  holes: { hole_id: string; found: boolean; td: number | null; max_depth: number; tables: Record<string, DownholeInterval[]> }[];
}

export const getDownhole = (setId: number, holes: string[], tables: string[], signal?: AbortSignal, all = false, fields?: Record<string, string[]>) =>
  request<DownholeData>(`/api/sets/${setId}/downhole`, { method: "POST", ...json({ holes, tables, all, fields: fields ?? {} }), signal });

export const getIntervalRows = (id: number, offset: number, limit: number) =>
  request<{ total: number; columns: IntervalColumn[]; rows: Record<string, unknown>[] }>(`/api/interval-tables/${id}/rows?offset=${offset}&limit=${limit}`);

// ---- colourings by grade classes saved in a project

export interface SavedPalette {
  id: number;
  name: string;
  classes: { from: number | null; color: string; label?: string }[];
}

export const getPalettes = (projectId: number) => request<SavedPalette[]>(`/api/projects/${projectId}/palettes`);
export const savePalette = (projectId: number, name: string, classes: SavedPalette["classes"]) =>
  request<SavedPalette>(`/api/projects/${projectId}/palettes`, { method: "PUT", ...json({ name, classes }) });
export const deletePalette = (id: number) => request<void>(`/api/palettes/${id}`, { method: "DELETE" });
