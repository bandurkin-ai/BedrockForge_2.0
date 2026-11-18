/**
 * Wireframes of the open project (ТЗ 7.20, ADR 0011 §1, §3) for the sections «Каркасы» (`dtm` is null) and
 * «ЦТМ» (`dtm` holds the settings of the relief). The list is `sceneObjects.wireframes` (`scene/objects.ts`):
 * one list for the tree, «Свойства» and the 3D window. Every change carries the version of the wireframe; the
 * list is read again after it — after a 409 too. No React here.
 */
import { api, CoreError } from "../../shell/api";
import { loadSceneObjects, sceneObjects, type DtmInfo, type WireframeInfo as SceneWireframe } from "../scene/objects";

/** The settings of a DTM: the raster draped on it and how it is shown. */
export type DtmSettings = DtmInfo;

/** An attribute of the user (ТЗ 7.20): text, number, yes/no or empty. */
export interface WireframeAttr {
  name: string;
  value: string | number | boolean | null;
}

/** A wireframe as `/wireframes` gives it: with its folder of the tree and the attributes of the user. */
export type WireframeInfo = SceneWireframe & { folder?: string | null; attrs?: WireframeAttr[] };

/** What `PUT /wireframes/{id}/dtm` takes: only the given settings change; `raster_id` 0 takes the raster off. */
export interface DtmChanges {
  on?: boolean;
  raster_id?: number;
  clip?: boolean;
  tint?: string;
  opacity?: number;
  shade?: boolean;
}

/** `PATCH /wireframes/{id}/attrs`: `rename` {old: new}, then `remove` [names], then `set` {name: value}. */
export interface AttrsPatch {
  rename?: Record<string, string>;
  remove?: string[];
  set?: Record<string, WireframeAttr["value"]>;
}

/** «Каркасы»: everything that is not a DTM. */
export const solids = <T extends Pick<WireframeInfo, "dtm">>(list: readonly T[]): T[] => list.filter((w) => w.dtm === null);
/** «ЦТМ»: wireframes with the settings of a relief. */
export const dtms = <T extends Pick<WireframeInfo, "dtm">>(list: readonly T[]): T[] => list.filter((w) => w.dtm !== null);

export const wireframeById = (id: number): WireframeInfo | undefined => sceneObjects.get().wireframes.find((w) => w.id === id);

function current(id: number): WireframeInfo {
  const w = wireframeById(id);
  if (!w) throw new Error("Каркас не найден: обновите дерево проекта");
  return w;
}

async function change<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } finally {
    await loadSceneObjects(); // after a 409 too: the next try goes with the new version
  }
}

/** `wireframe.dtm`: `on` true — into «ЦТМ» (default settings), false — back to «Каркасы» (settings and raster cleared). */
export function setDtm(id: number, on: boolean): Promise<WireframeInfo> {
  return setDtmSettings(id, { on });
}

/** The settings of a DTM; settings without `on` move a wireframe to «ЦТМ». */
export async function setDtmSettings(id: number, changes: DtmChanges): Promise<WireframeInfo> {
  const put = () => change(() => api.put<WireframeInfo>(`/wireframes/${id}/dtm`, { expected_version: current(id).version, ...changes }));
  try {
    return await put();
  } catch (e) {
    // The settings are a partial change: the given fields only. A 409 means the version moved under them (a
    // write of the core or another window); the list was read again, so one more try goes with the new version
    // (windows E2E Э7: four quick changes of the panel, one of them lost).
    if (e instanceof CoreError && e.status === 409) return put();
    throw e;
  }
}

export function renameWireframe(id: number, name: string): Promise<WireframeInfo> {
  const w = current(id);
  return change(() => api.patch<WireframeInfo>(`/wireframes/${id}`, { expected_version: w.version, name }));
}

/** The colour of a wireframe: "#rrggbb", or null — no colour of its own. */
export function setWireframeColor(id: number, color: string | null): Promise<WireframeInfo> {
  const w = current(id);
  return change(() => api.patch<WireframeInfo>(`/wireframes/${id}`, { expected_version: w.version, color }));
}

/** Attributes of the user: add, change, rename, delete. */
export function editAttrs(id: number, patch: AttrsPatch): Promise<WireframeInfo> {
  const w = current(id);
  return change(() => api.patch<WireframeInfo>(`/wireframes/${id}/attrs`, { expected_version: w.version, ...patch }));
}

export function deleteWireframe(id: number): Promise<void> {
  return change(() => api.del(`/wireframes/${id}`));
}
