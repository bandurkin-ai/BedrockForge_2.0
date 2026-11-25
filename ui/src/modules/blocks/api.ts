/**
 * Regular block models of the project as the core gives them (ADR 0017 §3; `core/bf_core/blocks/router.py`):
 * `GET /blocks` — the tree (no `tensors`, fields without stats; `grid.variable` tells a variable step),
 * `GET /blocks/{id}` — «Свойства» (tensors, fields with labels and stats, the source), `PATCH /blocks/{id}`
 * {name, expected_version} — rename (409 `version_conflict` / `name_taken`), `DELETE /blocks/{id}`. The core
 * publishes `objects.changed {kind: "block_model", id[, deleted]}`. Coordinates are float64 numbers as given.
 */
import { api } from "../../shell/api";

export type Triple = [number, number, number];

export interface BlockGrid {
  /** the minimum corner of cell (0, 0, 0), float64 */
  corner: Triple;
  counts: Triple;
  /** a regular step — or `tensors` (details only) when the step varies */
  size?: Triple;
  tensors?: { x: number[]; y: number[]; z: number[] };
  order: "F";
  variable: boolean;
}

/** `count` — occupied cells with a value, `missing` — without one; no values — `min`… `std` are null. */
export interface NumberStats {
  count: number;
  missing: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
}

export interface CategoryStats {
  count: number;
  missing: number;
  /** cells per label, in the order of `labels` */
  counts: number[];
}

export interface BlockField {
  name: string;
  kind: "number" | "category";
  unit: string | null;
  labels?: string[];
  stats?: NumberStats | CategoryStats;
}

export interface BlockSource {
  /** of an import; a model made by an operation has `op` (fill, code, prototype, regularize, calc) and `params` instead */
  format?: string;
  op?: string;
  file?: string | null;
  element?: string | null;
}

export interface BlockModelInfo {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  /** [xmin, ymin, zmin, xmax, ymax, zmax] of the whole grid */
  extent: number[] | null;
  crs: string | null;
  grid: BlockGrid;
  /** occupied cells (rows of the file of cells) */
  cells: number;
  fields: BlockField[];
  source: BlockSource | null;
  warnings: string[];
  /** bytes of the file of cells */
  size: number | null;
  created_at: string;
  updated_at: string;
}

export const listBlockModels = async (): Promise<BlockModelInfo[]> => (await api.get<{ block_models?: BlockModelInfo[] }>("/blocks")).block_models ?? [];

export const getBlockModel = (id: number): Promise<BlockModelInfo> => api.get<BlockModelInfo>(`/blocks/${id}`);

export const patchBlockModel = (id: number, version: number, name: string): Promise<BlockModelInfo> => api.patch<BlockModelInfo>(`/blocks/${id}`, { name, expected_version: version });

export const deleteBlockModelApi = (id: number): Promise<{ deleted: number }> => api.del<{ deleted: number }>(`/blocks/${id}`);
