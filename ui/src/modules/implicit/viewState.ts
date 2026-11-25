/**
 * What of the implicit models the 3D window shows (ТЗ 8.9.4; ADR 0026 «Команды реестра»): the data points of a group (`POST /models/{id}/points/preview`,
 * the columns of the core) and the ellipsoid of the anisotropy of a model by hand. The wireframes of the outputs are ordinary wireframes of the project
 * (the layer of wireframes shows them): nothing of them is here. The choice lives for the session (`implicit.show`; the tree's check boxes run it);
 * the points are read once for the version of the model and dropped when the model changes, is deleted or the project is another one. A gap of a column
 * (`null`) is a point that is not drawn — never a point at 0 (8.0.1). The places stay float64 (the layer takes float32 from an integer origin,
 * `viz/origin.ts`). No React, no three.js.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { previewPoints, type PreviewAnswer } from "./api";
import { implicitStore, modelById } from "./state";

/** How many points of a group the 3D takes (the first ones in the canonical order of the core; `truncated` says there are more). */
export const SHOW_LIMIT = 50_000;

/** The kinds of the points of the core (`spec.POINT_TYPES`): the index of the column `kind`. */
export const KIND_NAMES = ["contact", "inside", "outside", "manual", "value"] as const;
export const KIND_TITLES: Record<(typeof KIND_NAMES)[number], string> = { contact: "контакт", inside: "внутри", outside: "снаружи", manual: "ручные", value: "по значению" };
/** The colours of the kinds (docs/brand: Copper Orange — the contact that is lit, Cobalt — the data inside, Slate — outside, Light Stone — by hand). */
export const KIND_COLORS: Record<(typeof KIND_NAMES)[number], string> = { contact: "#f46a1a", inside: "#1f6bff", outside: "#8a99a8", manual: "#e6e9ee", value: "#5b8dff" };

export interface PointsEntry {
  status: "loading" | "ready" | "error";
  /** the version of the model the points are of */
  version: number;
  /** project coordinates, float64, three numbers a point (only the points with all three coordinates) */
  xyz: Float64Array;
  /** the index of `KIND_NAMES`, a number a point */
  kinds: Uint8Array;
  /** the points of the group in the model, the points taken, the points drawn (without a gap) */
  total: number;
  taken: number;
  drawn: number;
  truncated: boolean;
  error: string | null;
}

export interface ViewState {
  /** `pointsKey(model, group)` of the groups shown */
  points: string[];
  /** the models whose ellipsoid is shown */
  ellipsoids: number[];
  entries: Record<string, PointsEntry>;
}

export const pointsKey = (model: number, group: number): string => `${model}:${group}`;

const EMPTY: ViewState = { points: [], ellipsoids: [], entries: {} };
export const implicitViewStore = new Store<ViewState>(EMPTY);

export const pointsShown = (s: ViewState, model: number, group: number): boolean => s.points.includes(pointsKey(model, group));
export const ellipsoidShown = (s: ViewState, model: number): boolean => s.ellipsoids.includes(model);

/** The columns of an answer as the layer takes them: the points with a gap in a coordinate are left out and counted. */
export function pointsOf(a: PreviewAnswer, version: number): PointsEntry {
  const { x, y, z, kind } = a.points;
  const n = Math.min(x.length, y.length, z.length);
  const xyz = new Float64Array(n * 3);
  const kinds = new Uint8Array(n);
  let k = 0;
  for (let i = 0; i < n; i++) {
    const px = x[i];
    const py = y[i];
    const pz = z[i];
    if (px === null || py === null || pz === null) continue;
    xyz[3 * k] = px;
    xyz[3 * k + 1] = py;
    xyz[3 * k + 2] = pz;
    const c = kind[i];
    kinds[k] = Number.isInteger(c) && c >= 0 && c < KIND_NAMES.length ? c : 2;
    k++;
  }
  return { status: "ready", version, xyz: xyz.slice(0, k * 3), kinds: kinds.slice(0, k), total: a.n, taken: n, drawn: k, truncated: a.truncated, error: null };
}

/** The colours of the points by kind, 0…1, three numbers a point (for `viz/xval/errorPoints.ts`). */
export function colorsOf(kinds: Uint8Array): Float32Array {
  const palette = KIND_NAMES.map((k) => {
    const h = KIND_COLORS[k];
    return [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];
  });
  const out = new Float32Array(kinds.length * 3);
  for (let i = 0; i < kinds.length; i++) out.set(palette[kinds[i]] ?? palette[2], 3 * i);
  return out;
}

/** «1 241 точка: контакт 5, внутри 27, снаружи 1 209» — of the points drawn. */
export function kindCounts(e: Pick<PointsEntry, "kinds">): Record<(typeof KIND_NAMES)[number], number> {
  const out = { contact: 0, inside: 0, outside: 0, manual: 0, value: 0 };
  for (const c of e.kinds) out[KIND_NAMES[c]]++;
  return out;
}

const loadSeq = new Map<string, number>();

/** Reads the points of a group (the version of the model asked at the moment); a read that another one or a change of the project overtook is dropped. */
export async function loadPoints(model: number, group: number): Promise<PointsEntry> {
  const key = pointsKey(model, group);
  const version = modelById(model)?.version ?? 0;
  const mine = (loadSeq.get(key) ?? 0) + 1;
  loadSeq.set(key, mine);
  const uid = projectStore.get().current?.uid ?? null;
  const loading: PointsEntry = { status: "loading", version, xyz: new Float64Array(0), kinds: new Uint8Array(0), total: 0, taken: 0, drawn: 0, truncated: false, error: null };
  implicitViewStore.set((s) => ({ entries: { ...s.entries, [key]: loading } }));
  const settle = (e: PointsEntry): PointsEntry => {
    if (loadSeq.get(key) !== mine || projectStore.get().current?.uid !== uid) return e;
    implicitViewStore.set((s) => (s.points.includes(key) || e.status === "error" ? { entries: { ...s.entries, [key]: e } } : {}));
    return e;
  };
  try {
    return settle(pointsOf(await previewPoints(model, { group_id: group, limit: SHOW_LIMIT }), version));
  } catch (e) {
    return settle({ ...loading, status: "error", error: errorText(e) });
  }
}

/** Switches the points of a group; the answer is the entry once it is read (or the one that is there). */
export async function setPoints(model: number, group: number, on: boolean): Promise<PointsEntry | null> {
  const key = pointsKey(model, group);
  if (!on) {
    loadSeq.set(key, (loadSeq.get(key) ?? 0) + 1);
    implicitViewStore.set((s) => {
      const entries = { ...s.entries };
      delete entries[key];
      return { points: s.points.filter((k) => k !== key), entries };
    });
    return null;
  }
  implicitViewStore.set((s) => (s.points.includes(key) ? {} : { points: [...s.points, key] }));
  const have = implicitViewStore.get().entries[key];
  if (have && have.status === "ready" && have.version === (modelById(model)?.version ?? 0)) return have;
  return loadPoints(model, group);
}

export function setEllipsoid(model: number, on: boolean): void {
  implicitViewStore.set((s) => ({ ellipsoids: on ? (s.ellipsoids.includes(model) ? s.ellipsoids : [...s.ellipsoids, model]) : s.ellipsoids.filter((m) => m !== model) }));
}

/** A model is gone (`model` given) or the project is another one: what was shown of it goes. */
export function forgetView(model?: number): void {
  if (model === undefined) {
    loadSeq.clear();
    implicitViewStore.set(EMPTY);
    return;
  }
  const mine = (k: string) => k.startsWith(`${model}:`);
  for (const k of [...loadSeq.keys()]) if (mine(k)) loadSeq.set(k, (loadSeq.get(k) ?? 0) + 1);
  implicitViewStore.set((s) => ({ points: s.points.filter((k) => !mine(k)), ellipsoids: s.ellipsoids.filter((m) => m !== model), entries: Object.fromEntries(Object.entries(s.entries).filter(([k]) => !mine(k))) }));
}

/**
 * The list of the models was read: what is shown of a model that is not in it goes; the points of a model whose version moved (or whose
 * group is gone) are read again — the 3D follows the data, not the old answer.
 */
export function followModels(): void {
  if (!implicitStore.get().loaded) return;
  const view = implicitViewStore.get();
  for (const m of new Set([...view.points.map((k) => Number(k.split(":")[0])), ...view.ellipsoids])) {
    if (!modelById(m)) forgetView(m);
  }
  for (const key of implicitViewStore.get().points) {
    const [m, g] = key.split(":").map(Number);
    const row = modelById(m);
    if (!row) continue;
    if (!row.group_list.some((x) => x.id === g)) {
      void setPoints(m, g, false);
      continue;
    }
    const e = implicitViewStore.get().entries[key];
    if (e && e.status !== "loading" && e.version !== row.version) void loadPoints(m, g);
  }
}
