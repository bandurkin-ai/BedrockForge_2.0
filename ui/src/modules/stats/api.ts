/**
 * The core's API of the tab «Статистика» (ТЗ 7.29; ADR 0019 §7, §10; core 13.17 `stats/routes_tab.py` →
 * `stats/tab.py`): `POST /stats/tab/{declus|nscore|backtr|bootstrap}`. The common keys of a body — `set_id`,
 * `table` (`name_key`), `field`, `holes` (empty — all), `declus` (what `/declus` computes; for the others the
 * weights of the values, null — without). The answer is the kernel's plus {set_id, table, field}; an undefined
 * number is null, never 0. More than 20 000 intervals — 422 `too_many_samples` {param: "table"} (Т7.29-2).
 * Nothing is stored.
 */
import { api } from "../../shell/api";

export type DeclusMethod = "cell" | "polygon";

/** Parameters of declustering (the names of `composite.declus`, Э12); absent — the defaults of the core. */
export interface DeclusParams {
  method: DeclusMethod;
  cmin?: number | null;
  cmax?: number | null;
  ncell?: number | null;
  noff?: number | null;
  minmax?: "min" | "max";
  nx?: number | null;
  ny?: number | null;
  /** [xmin, xmax, ymin, ymax] of the polygons; absent — the extent of the points */
  bounds?: [number, number, number, number] | null;
}

export interface TabBody {
  set_id: number;
  table: string | null;
  field: string | null;
  holes?: string[];
  declus: DeclusParams | null;
}

/** Intervals of the table: all, without a value, without a place on a hole path, used (the points). */
export interface TabCounts {
  rows: number;
  missing: number;
  no_position: number;
  used: number;
}

interface Head {
  set_id: number;
  /** the name of the table */
  table: string;
  field: string;
}

export interface DeclusAnswer extends Head {
  method: DeclusMethod;
  count: number;
  naive_mean: number | null;
  declustered_mean: number | null;
  weight_min: number | null;
  weight_max: number | null;
  zero_weights: number;
  /** no cell size moved the mean: the weights are 1 */
  uniform: boolean;
  best_cell_size: number | null;
  /** the curve «размер ячейки → среднее»; size 0 holds the plain mean (as BF); null — polygons */
  cell_sizes: number[] | null;
  means: (number | null)[] | null;
  cmin?: number;
  cmax?: number;
  ncell?: number;
  noff?: number;
  minmax?: "min" | "max";
  nx?: number;
  ny?: number;
  bounds?: number[];
  notes: string[];
  counts: TabCounts;
}

export interface NscoreAnswer extends Head {
  count: number;
  /** the transform table, ascending */
  values: number[];
  scores: number[];
  /** rows of the table (fewer than `count` when declustering gave weights of 0) */
  table_rows: number;
  zmin: number;
  zmax: number;
  /** dh_rows.id of the points and the score of each */
  rows: number[];
  sample_scores: number[];
  weighted: boolean;
  counts: TabCounts;
}

export type LowerTail = "linear" | "power" | null;
export type UpperTail = "linear" | "power" | "hyperbolic" | null;

export interface BacktrBody extends TabBody {
  zmin: number | null;
  zmax: number | null;
  scores: number[];
  ltail: LowerTail;
  utail: UpperTail;
  ltpar: number | null;
  utpar: number | null;
}

export interface BacktrAnswer extends Head {
  /** null — the tail has no number there */
  values: (number | null)[];
}

export type BootStatistic = "mean" | "median" | "std" | "cv" | "p10" | "p90";

export interface BootEntry {
  observed: number | null;
  observed_weighted: number | null;
  /** realizations where the statistic is defined */
  defined: number;
  mean: number | null;
  std: number | null;
  ci_low: number | null;
  ci_high: number | null;
  /** the histogram of the realizations: n + 1 edges */
  edges: number[];
  counts: number[];
}

export interface BootstrapAnswer extends Head {
  count: number;
  n_boot: number;
  seed: number;
  confidence: number;
  weighted: boolean;
  statistics: Record<BootStatistic, BootEntry>;
  counts: TabCounts;
}

export const tabDeclus = (body: TabBody): Promise<DeclusAnswer> => api.post<DeclusAnswer>("/stats/tab/declus", { ...body, weights: false });

export const tabNscore = (body: TabBody & { zmin: number | null; zmax: number | null }): Promise<NscoreAnswer> => api.post<NscoreAnswer>("/stats/tab/nscore", body);

export const tabBacktr = (body: BacktrBody): Promise<BacktrAnswer> => api.post<BacktrAnswer>("/stats/tab/backtr", body);

export const tabBootstrap = (body: TabBody & { n_boot: number | null; seed: number | null; confidence: number | null }): Promise<BootstrapAnswer> => api.post<BootstrapAnswer>("/stats/tab/bootstrap", body);
