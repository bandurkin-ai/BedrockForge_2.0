/**
 * What the tab «Карта опоры» of the check window knows (ТЗ 7.35 Д-32 п. 2; docs/progress/e19-api.md §8.2): the surfaces of the model with the
 * share of the area beyond D_оп, the threshold (the text of the field; empty — 2·D_max of the core), the surface painted in the 3D window.
 * The distances do not depend on the threshold: moving D_оп repaints the vertices (`supportScene.repaint`, no read) and asks the core only for
 * the shares. A read that a newer one overtook is dropped. No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { loadSupport, loadSupportMesh, type SupportAnswer, type SupportSurface } from "./checkApi";
import { parseField } from "./CheckState";
import { hideSupport, repaint, showSupport, supportShown } from "./supportScene";
import { countBeyond, meshData, surfaceLine } from "./supportModel";

export interface SupportWin {
  modelId: number | null;
  phase: "idle" | "loading" | "ok" | "error";
  answer: SupportAnswer | null;
  error: string | null;
  /** the text of the field «Порог D_оп, м»; empty — the default of the core */
  thresholdText: string;
  /** the surface chosen (`wireframe_id`) */
  surfaceId: number | null;
  /** the surface of the map in the 3D window */
  shownId: number | null;
  /** the map is being read */
  painting: boolean;
  /** the line the last action told */
  note: string | null;
  /** ms of the last repaint (the pass over the vertices) */
  repaintMs: number | null;
  /** vertices beyond D_оп and without data points of the surface painted */
  counts: { beyond: number; noData: number; vertices: number } | null;
}

const fresh = (): SupportWin => ({ modelId: null, phase: "idle", answer: null, error: null, thresholdText: "", surfaceId: null, shownId: null, painting: false, note: null, repaintMs: null, counts: null });

export const supportStore = new Store<SupportWin>(fresh());

let seq = 0;
let paint = 0;
/** the distances of the surface painted: the repaint reads them, the counts too */
let distances: Float32Array | null = null;

/** The threshold of the field: a number, null — the default of the core, NaN — wrong. */
export const thresholdOf = (s: SupportWin): number | null => {
  const v = parseField(s.thresholdText);
  return v === null || (Number.isFinite(v) && v > 0) ? v : Number.NaN;
};

/** The share of the area beyond D_оп of a surface, 0…1 — for «Свойства» (null — no map was read for it). */
export const shareOf = (wireframeId: number): number | null => supportStore.get().answer?.surfaces.find((x) => x.wireframe_id === wireframeId)?.share_beyond ?? null;

export const surfaceOf = (wireframeId: number | null): SupportSurface | null => supportStore.get().answer?.surfaces.find((x) => x.wireframe_id === wireframeId) ?? null;

/** Reads the surfaces (and the shares) for the threshold now in the field. */
export async function reloadSupport(): Promise<SupportAnswer | null> {
  const s = supportStore.get();
  if (s.modelId === null) return null;
  const t = thresholdOf(s);
  if (t !== null && Number.isNaN(t)) {
    supportStore.set({ note: "Порог D_оп — число метров больше нуля." });
    return null;
  }
  const mine = ++seq;
  const uid = projectStore.get().current?.uid ?? null;
  supportStore.set({ phase: "loading", error: null, note: null });
  try {
    const answer = await loadSupport(s.modelId, t);
    if (mine !== seq || (projectStore.get().current?.uid ?? null) !== uid) return null;
    const keep = supportStore.get().surfaceId;
    const only = answer.surfaces.length === 1 ? answer.surfaces[0].wireframe_id : null;
    supportStore.set({ phase: "ok", answer, error: null, surfaceId: keep !== null && answer.surfaces.some((x) => x.wireframe_id === keep) ? keep : only });
    return answer;
  } catch (e) {
    if (mine !== seq) return null;
    supportStore.set({ phase: "error", answer: null, error: errorText(e) });
    return null;
  }
}

/** The tab for a model; `threshold` fills the field. */
export async function openSupport(modelId: number, threshold: number | null = null): Promise<SupportAnswer | null> {
  const same = supportStore.get().modelId === modelId;
  if (!same) {
    if (supportShown()) hideSupport();
    supportStore.set({ ...fresh(), modelId });
  }
  if (threshold !== null) supportStore.set({ thresholdText: String(threshold).replace(".", ",") });
  if (same && threshold === null && supportStore.get().phase === "ok") return supportStore.get().answer;
  return reloadSupport();
}

export const setThresholdText = (thresholdText: string): void => supportStore.set({ thresholdText });
export const chooseSurface = (surfaceId: number | null): void => supportStore.set({ surfaceId });

/** «Применить» to the threshold: the painted surface is repainted at once, the shares are read again. */
export async function applyThreshold(): Promise<void> {
  const s = supportStore.get();
  const t = thresholdOf(s);
  if (t !== null && Number.isNaN(t)) {
    supportStore.set({ note: "Порог D_оп — число метров больше нуля." });
    return;
  }
  const answer = await reloadSupport();
  const used = t ?? answer?.threshold ?? null;
  if (used === null || !answer || supportStore.get().shownId === null || !distances) return;
  const ms = repaint(used);
  supportStore.set({ repaintMs: ms, counts: { ...countBeyond(distances, used), vertices: distances.length } });
}

/** «Показать карту в 3D»: the distances and the whole surface in one answer of the core, the surface painted. Returns the line for the status. */
export async function showMap(surfaceId?: number): Promise<string> {
  const s = supportStore.get();
  const id = surfaceId ?? s.surfaceId;
  if (s.modelId === null || id === null) return "Выберите поверхность";
  const t = thresholdOf(s);
  if (t !== null && Number.isNaN(t)) {
    supportStore.set({ note: "Порог D_оп — число метров больше нуля." });
    return "Порог D_оп — число метров больше нуля.";
  }
  const mine = ++paint;
  supportStore.set({ painting: true, note: null });
  try {
    const got = await loadSupportMesh(s.modelId, id, t);
    if (mine !== paint) return "Отменено";
    const threshold = got.threshold > 0 ? got.threshold : (t ?? 0);
    showSupport({ wireframeId: id, mesh: meshData(got.mesh), values: got.values, threshold });
    distances = got.values;
    const counts = { ...countBeyond(got.values, threshold), vertices: got.values.length };
    const surf = got.surface ?? surfaceOf(id);
    supportStore.set({ painting: false, shownId: id, surfaceId: id, counts, note: surf ? surfaceLine(surf) : null, repaintMs: null });
    return surf ? surfaceLine(surf) : "Карта опоры показана";
  } catch (e) {
    if (mine !== paint) return "Отменено";
    const text = errorText(e);
    supportStore.set({ painting: false, note: text });
    return text;
  }
}

export function hideMap(): void {
  paint++;
  hideSupport();
  distances = null;
  supportStore.set({ shownId: null, painting: false, counts: null, repaintMs: null, note: null });
}

/** The window is closed or another project is open: the map leaves the 3D window, the state is clean. */
export function resetSupport(): void {
  seq++;
  paint++;
  if (supportShown()) hideSupport();
  distances = null;
  supportStore.set(fresh());
}
