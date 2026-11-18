/**
 * Wireframes of the open project (ТЗ 7.20, ADR 0011 §1, §3): the list of `GET /wireframes` for the sections
 * «Каркасы» (`dtm` is null) and «ЦТМ» (`dtm` holds the settings of the relief). Read when the project opens,
 * on `objects.changed` and after a job that makes wireframes (`bind.ts`). No React here.
 */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";

/** The settings of a DTM: the raster draped on it and how it is shown. */
export interface DtmSettings {
  raster_id: number | null;
  clip: boolean;
  /** "#rrggbb": the colour the image is multiplied by */
  tint: string;
  /** 0…1 */
  opacity: number;
  shade: boolean;
}

export interface WireframeInfo {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
  point_count: number;
  triangle_count: number;
  extent: (number | null)[] | null;
  /** null — a wireframe of «Каркасы»; settings — a DTM */
  dtm: DtmSettings | null;
}

export interface WireframesState {
  wireframes: WireframeInfo[];
  loaded: boolean;
  error: string | null;
}

const EMPTY: WireframesState = { wireframes: [], loaded: false, error: null };
export const wireframesStore = new Store<WireframesState>(EMPTY);

const text = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** «Каркасы»: everything that is not a DTM. */
export const solids = (list: readonly WireframeInfo[]): WireframeInfo[] => list.filter((w) => w.dtm === null);
/** «ЦТМ»: wireframes with the settings of a relief. */
export const dtms = (list: readonly WireframeInfo[]): WireframeInfo[] => list.filter((w) => w.dtm !== null);

export const wireframeById = (id: number): WireframeInfo | undefined => wireframesStore.get().wireframes.find((w) => w.id === id);

let seq = 0;

/** Reads the list; an answer overtaken by a newer request or by another project is dropped. */
export async function loadWireframes(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) {
    wireframesStore.set(EMPTY);
    return;
  }
  try {
    const r = await api.get<{ wireframes: WireframeInfo[] }>("/wireframes");
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = wireframesStore.get();
    // the same list keeps its identity: subscribers compare by reference
    const same = JSON.stringify(r.wireframes) === JSON.stringify(cur.wireframes);
    if (!same || !cur.loaded || cur.error) wireframesStore.set({ wireframes: same ? cur.wireframes : r.wireframes, loaded: true, error: null });
  } catch (e) {
    if (mine === seq) wireframesStore.set({ error: text(e) });
  }
}

export function clearWireframes(): void {
  seq++;
  wireframesStore.set(EMPTY);
}

function current(id: number): WireframeInfo {
  const w = wireframeById(id);
  if (!w) throw new Error("Каркас не найден: обновите дерево проекта");
  return w;
}

/** `wireframe.dtm`: `on` true — into «ЦТМ» (default settings), false — back to «Каркасы» (settings and raster cleared). */
export async function setDtm(id: number, on: boolean): Promise<WireframeInfo> {
  const w = current(id);
  try {
    return await api.put<WireframeInfo>(`/wireframes/${id}/dtm`, { expected_version: w.version, on });
  } finally {
    await loadWireframes(); // after a 409 too: the next try goes with the new version
  }
}

export async function renameWireframe(id: number, name: string): Promise<WireframeInfo> {
  const w = current(id);
  try {
    return await api.patch<WireframeInfo>(`/wireframes/${id}`, { expected_version: w.version, name });
  } finally {
    await loadWireframes();
  }
}

export async function deleteWireframe(id: number): Promise<void> {
  try {
    await api.del(`/wireframes/${id}`);
  } finally {
    await loadWireframes();
  }
}
