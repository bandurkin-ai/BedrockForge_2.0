/**
 * Data of the displays along holes as the model works with it (the shapes of BF `api.ts`, ADR 0007 §3):
 * built in BF2 from the BFA1 interval parts (3D) and from `/viz/sets/{id}/log` (the window «Данные по
 * скважинам»). A missing value is null — never 0.
 */
export interface IntervalColumn {
  name: string;
  kind: "number" | "text";
  unit: string;
}

export interface IntervalTableInfo {
  /** `dh_tables.name_key`: survives a replaced table and `.bfggis` */
  key: string;
  id: number | null;
  name: string;
  builtin: boolean;
  row_count: number;
  columns: IntervalColumn[];
  /** "event": rows have one depth, no thickness */
  kind?: "interval" | "event";
}

export interface DownholeInterval {
  from: number | null;
  to: number | null;
  values: Record<string, number | string | null>;
}

export interface DownholeData {
  tables: { key: string; name: string; columns: IntervalColumn[]; kind?: "interval" | "event" }[];
  holes: { hole_id: string; found: boolean; td: number | null; max_depth: number; tables: Record<string, DownholeInterval[]> }[];
}

/** Quantiles of a field for the colour scale (from `/viz/sets/{id}/field-stats`). */
export interface ValueStats {
  count: number;
  min: number | null;
  max: number | null;
  p05: number | null;
  p50: number | null;
  p95: number | null;
}
