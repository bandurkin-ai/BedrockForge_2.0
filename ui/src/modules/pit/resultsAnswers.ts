/**
 * The answers of the core for the results of the pit optimisation (ТЗ 8.11.4–8.11.5; ADR 0028 решения 25–27; docs/progress/e21-api.md §9–§11) and their readers:
 * the result of the job `pit.surfaces` (the DTMs of the shells), of the job `pit.solid` (the closed solid of the pit) and the answer of `POST /pit/compare`
 * (two scenarios). The forms are those of `core/tests/data/pit/contract_answers.json` (`surfaces_result`, `solid_result`, `compare_result`). A value that is
 * not known is `null` — never 0; a shell that only one scenario has has `null` on the other side. No React.
 */
import { list, num, obj, readWarnings, str, type Warning } from "./answers";

const flag = (v: unknown): boolean => v === true;

// ---- the surfaces (§9) ----

export interface SurfaceWire {
  /** the number of the shell, 1…N (the value of the field PIT) */
  shell: number;
  rf: number | null;
  wireframe_id: number | null;
  name: string;
  nodes: number | null;
  triangles: number | null;
  /** the DTM of an earlier run was replaced in place */
  replaced: boolean;
}

export interface SurfacesResult {
  scenario_id: number;
  /** the folder of the scenario in the tree, «Карьер/‹имя›» (`folder` of the core); the sample has `folder_id` */
  folder: string | null;
  folder_id: number | null;
  wireframes: SurfaceWire[];
  /** the shells that got no DTM (the relief does not cover the plan): a gap, not a flat surface */
  skipped: number[];
  warnings: Warning[];
}

/** `{scenario_id, folder_id, wireframes: [{shell, rf, wireframe_id, name, nodes, triangles}], events}` → the result; null when it has no scenario. */
export function readSurfaces(raw: unknown): SurfacesResult | null {
  const r = obj(raw);
  const id = num(r.scenario_id);
  if (id === null) return null;
  return {
    scenario_id: id,
    folder: str(r.folder),
    folder_id: num(r.folder_id),
    wireframes: list(r.wireframes).flatMap((x): SurfaceWire[] => {
      const w = obj(x);
      const shell = num(w.shell);
      return shell === null ? [] : [{ shell, rf: num(w.rf), wireframe_id: num(w.wireframe_id), name: str(w.name) ?? "", nodes: num(w.nodes), triangles: num(w.triangles), replaced: flag(w.replaced) }];
    }),
    skipped: list(r.skipped).flatMap((x) => (num(x) === null ? [] : [x as number])),
    warnings: readWarnings(r.warnings),
  };
}

// ---- the solid (§10) ----

export interface SolidResult {
  scenario_id: number;
  shell: number | null;
  rf: number | null;
  wireframe_id: number | null;
  name: string;
  /** the volume of the solid, m³ */
  volume: number | null;
  /** Σ V of the blocks of the shell, m³ */
  volume_blocks: number | null;
  /** the difference of the two, % */
  volume_diff_pct: number | null;
  parts: number | null;
  closed: boolean;
  /** `between` (the solid between the relief and the surface) or `difference` (the reserve way) */
  method: string;
  /** the folder of the scenario in the tree (`folder` of the core; the sample has none) */
  folder: string | null;
  /** the solid of an earlier run was replaced in place */
  replaced: boolean;
  warnings: Warning[];
}

export function readSolid(raw: unknown): SolidResult | null {
  const r = obj(raw);
  const id = num(r.scenario_id);
  if (id === null) return null;
  return {
    scenario_id: id,
    shell: num(r.shell),
    rf: num(r.rf),
    wireframe_id: num(r.wireframe_id),
    name: str(r.name) ?? "",
    volume: num(r.volume),
    volume_blocks: num(r.volume_blocks),
    volume_diff_pct: num(r.volume_diff_pct),
    parts: num(r.parts),
    closed: r.closed !== false,
    method: str(r.method) ?? "",
    folder: str(r.folder),
    replaced: flag(r.replaced),
    warnings: readWarnings(r.warnings),
  };
}

// ---- the comparison (§11) ----

export interface CompareSide {
  id: number;
  name: string;
  version: number | null;
  model: { id: number | null; name: string; cells_sha: string | null };
  solver: string;
  params_hash: string | null;
}

/** The numbers of a pit C_k (cumulative at the base price) and the NPV of its best and worst case; every one may be null. */
export interface ShellNumbers {
  blocks: number | null;
  ore_t: number | null;
  waste_t: number | null;
  cf: number | null;
  npv_best: number | null;
  npv_worst: number | null;
}

export const SHELL_KEYS = ["blocks", "ore_t", "waste_t", "cf", "npv_best", "npv_worst"] as const;
export type ShellKey = (typeof SHELL_KEYS)[number];

export type ShellStatus = "both" | "only_a" | "only_b";

export interface CompareShell {
  rf: number;
  status: ShellStatus;
  k_a: number | null;
  k_b: number | null;
  /** the side that has no such shell is null (a gap, not 0) */
  a: ShellNumbers | null;
  b: ShellNumbers | null;
  diff: ShellNumbers | null;
}

export interface ParamDiff {
  /** the path of the keys: `elements.0.price`, `rf.3` */
  param: string;
  a: unknown;
  b: unknown;
}

export interface OnlyBlocks {
  blocks: number | null;
  tonnes: number | null;
  /** the blocks that have no tonnes (no density): counted, not in the sum */
  no_tonnes: number | null;
}

export interface CompareWarning extends Warning {
  side: string | null;
}

export interface CompareAnswer {
  a: CompareSide;
  b: CompareSide;
  same_grid: boolean;
  params_diff: ParamDiff[];
  shells: CompareShell[];
  /** the totals over the last shell of each side (`totals` of the core; null — not in the answer) */
  totals: { a: ShellNumbers | null; b: ShellNumbers | null; diff: ShellNumbers | null } | null;
  /** the blocks of the final pit only A has / only B has; null — the grids differ (the warning says it) */
  blocks: { only_a: OnlyBlocks; only_b: OnlyBlocks } | null;
  warnings: CompareWarning[];
}

const readSide = (v: unknown): CompareSide | null => {
  const s = obj(v);
  const id = num(s.id);
  if (id === null) return null;
  const m = obj(s.model);
  return { id, name: str(s.name) ?? "", version: num(s.version), model: { id: num(m.id), name: str(m.name) ?? "", cells_sha: str(m.cells_sha) }, solver: str(s.solver) ?? "", params_hash: str(s.params_hash) };
};

/** The numbers of one side of a shell: null when the side has none (`null` in the answer), a key that is missing or null is null. */
export function readNumbers(v: unknown): ShellNumbers | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const s = obj(v);
  return { blocks: num(s.blocks), ore_t: num(s.ore_t), waste_t: num(s.waste_t), cf: num(s.cf), npv_best: num(s.npv_best), npv_worst: num(s.npv_worst) };
}

const statusOf = (v: unknown): ShellStatus => (v === "only_a" || v === "only_b" ? v : "both");

const readOnly = (v: unknown): OnlyBlocks => {
  const o = obj(v);
  return { blocks: num(o.blocks), tonnes: num(o.tonnes), no_tonnes: num(o.no_tonnes) };
};

/** `POST /pit/compare` → the comparison; null when a head is missing (the answer is not the core's). */
export function readCompare(raw: unknown): CompareAnswer | null {
  const r = obj(raw);
  const a = readSide(r.a);
  const b = readSide(r.b);
  if (!a || !b) return null;
  const blocks = obj(r.blocks);
  const totals = obj(r.totals);
  return {
    a,
    b,
    same_grid: flag(r.same_grid),
    params_diff: list(r.params_diff).flatMap((x): ParamDiff[] => {
      const d = obj(x);
      return typeof d.param === "string" ? [{ param: d.param, a: d.a ?? null, b: d.b ?? null }] : [];
    }),
    shells: list(r.shells).flatMap((x): CompareShell[] => {
      const s = obj(x);
      const rf = num(s.rf);
      if (rf === null) return [];
      const status = statusOf(s.status);
      // a side that is not in the shell has no numbers, whatever the answer says: a gap is never 0
      return [{ rf, status, k_a: num(s.k_a), k_b: num(s.k_b), a: status === "only_b" ? null : readNumbers(s.a), b: status === "only_a" ? null : readNumbers(s.b), diff: status === "both" ? readNumbers(s.diff) : null }];
    }),
    totals: Object.keys(totals).length ? { a: readNumbers(totals.a), b: readNumbers(totals.b), diff: readNumbers(totals.diff) } : null,
    blocks: Object.keys(blocks).length ? { only_a: readOnly(blocks.only_a), only_b: readOnly(blocks.only_b) } : null,
    warnings: readWarnings(r.warnings).map((w, i) => ({ ...w, side: str(obj(list(r.warnings)[i]).side) })),
  };
}
