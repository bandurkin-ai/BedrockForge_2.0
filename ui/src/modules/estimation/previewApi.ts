/**
 * The preview of one block (`POST /geostat/estimate/preview`, docs/progress/e18-api.md §7; ТЗ 8.8.4: «в 3D у выбранного блока —
 * эллипсоид, отобранные пробы и их веса»): the parameters of the wizard (not saved yet) estimate one block with the same kernel as
 * the run, ≈ 20 ms. The answer: the estimate and the other output values, the status, the passes tried, the samples of the
 * neighbourhood with their weights λ and the reason each is in or out, the ellipsoid of the pass that gave the estimate. No React.
 */
import { api } from "../../shell/api";
import { GEOSTAT_API, isNum, numOrNull, obj, str, type Triple } from "./api";

const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? ([v[0], v[1], v[2]] as Triple) : null);

/** A sample of the neighbourhood of a block: where it is, the value (raw and used), the distance, the octant, the weight λ (null — not chosen) and why. */
export interface PreviewSample {
  i: number;
  row_id: number | null;
  hole: string;
  from: number | null;
  to: number | null;
  xyz: Triple | null;
  z_raw: number | null;
  z: number | null;
  /** the distance in the metric of the ellipsoid */
  h: number | null;
  /** the Euclidean distance */
  dist: number | null;
  octant: number | null;
  lambda: number | null;
  reason: string;
}

/** The pass the search tried: it estimated the block (`ok`), had too few samples (`few`) or a singular system (`singular`). */
export interface PreviewPass {
  pass: number;
  ok: boolean;
  n: number;
  reason: "few" | "singular" | null;
}

export interface PreviewEllipsoid {
  centre: Triple | null;
  ang1: number;
  ang2: number;
  ang3: number;
  r1: number;
  r2: number;
  r3: number;
  pass: number;
}

export interface PreviewAnswer {
  at: { row: number; p: number; ijk: Triple | null };
  centre: Triple | null;
  size: Triple | null;
  label: string | null;
  /** the status of the block, 0…3 (`STATUS_LABELS`) */
  status: number;
  pass: number | null;
  /** the output fields of the row by the key (EST, KV, …); a gap is null */
  values: Record<string, number | null>;
  mu: number | null;
  samples: PreviewSample[];
  ellipsoid: PreviewEllipsoid | null;
  passes: PreviewPass[];
  origin: Triple | null;
  counts: { candidates: number; selected: number };
}

function readSample(raw: unknown): PreviewSample {
  const o = obj(raw);
  return {
    i: Number(o.i ?? 0),
    row_id: numOrNull(o.row_id),
    hole: String(o.hole ?? ""),
    from: numOrNull(o.from),
    to: numOrNull(o.to),
    xyz: triple(o.xyz),
    z_raw: numOrNull(o.z_raw),
    z: numOrNull(o.z),
    h: numOrNull(o.h),
    dist: numOrNull(o.dist),
    octant: numOrNull(o.octant),
    lambda: numOrNull(o.lambda),
    reason: String(o.reason ?? ""),
  };
}

export function readPreview(raw: unknown): PreviewAnswer {
  const o = obj(raw);
  const at = obj(o.at);
  const e = obj(o.ellipsoid);
  const c = obj(o.counts);
  return {
    at: { row: Number(at.row ?? 0), p: Number(at.p ?? 0), ijk: triple(at.ijk) },
    centre: triple(o.centre),
    size: triple(o.size),
    label: str(o.label),
    status: numOrNull(o.status) ?? 0,
    pass: numOrNull(o.pass),
    values: Object.fromEntries(Object.entries(obj(o.values)).map(([k, v]) => [k, numOrNull(v)])),
    mu: numOrNull(o.mu),
    samples: (Array.isArray(o.samples) ? o.samples : []).map(readSample),
    ellipsoid: Object.keys(e).length ? { centre: triple(e.centre), ang1: Number(e.ang1 ?? 0), ang2: Number(e.ang2 ?? 0), ang3: Number(e.ang3 ?? 0), r1: Number(e.r1 ?? 0), r2: Number(e.r2 ?? 0), r3: Number(e.r3 ?? 0), pass: Number(e.pass ?? 1) } : null,
    passes: (Array.isArray(o.passes) ? o.passes : []).map((p) => {
      const x = obj(p);
      return { pass: Number(x.pass ?? 0), ok: x.ok === true, n: Number(x.n ?? 0), reason: x.reason === "few" || x.reason === "singular" ? x.reason : null };
    }),
    origin: triple(o.origin),
    counts: { candidates: Number(c.candidates ?? 0), selected: Number(c.selected ?? 0) },
  };
}

/** The block: a parent by its indices, a row of the model, a point. */
export type PreviewAt = { ijk: Triple } | { row: number } | { xyz: Triple };

/** `POST /estimate/preview {estimation: the body of the row, at}`: one block as the core would estimate it with these parameters. */
export const loadPreview = async (estimation: Record<string, unknown>, at: PreviewAt): Promise<PreviewAnswer> => readPreview(await api.post(`${GEOSTAT_API}/estimate/preview`, { estimation, at }));

/** The same for a stored row: `{estimation_id, at}` (the command `estimate.ellipsoid {id}`: the row as it is in the project, at a block). */
export const loadRowPreview = async (estimationId: number, at: PreviewAt): Promise<PreviewAnswer> => readPreview(await api.post(`${GEOSTAT_API}/estimate/preview`, { estimation_id: estimationId, at }));

/** The words of the reasons a candidate is in or out of the estimate (the list of the core, `E/search.REASONS`). */
export const REASON_LABELS: Record<string, string> = {
  selected: "выбрана",
  hy_capped: "урезана до t",
  outside: "вне эллипсоида",
  hole_limit: "лимит скважины",
  octant_limit: "лимит октанта",
  other_domain: "другой домен (жёсткая граница)",
  soft_far: "дальше d_м (мягкая граница)",
  hy_dropped: "ураганная, отброшена",
  merged: "совпадающая точка (усреднена)",
  over_n_max: "за n_max",
};

/** The words of a pass that did not give the estimate. */
export const PASS_REASONS: Record<string, string> = { few: "мало проб", singular: "вырожденная система" };
