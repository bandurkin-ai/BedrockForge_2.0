/**
 * Line layers and rasters of the open project as the 3D window (and whoever else needs the lists) sees them
 * (ADR 0011 §3): `GET /lines/files` — files with their layers, `GET /rasters` — rasters with their
 * georeference. The lists follow the project: read when it opens, again on `objects.changed` and after an
 * import of objects. No three.js here (the store is in the main bundle); the picked object of the 3D window
 * is kept here too, for the properties panel.
 */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";

/** A layer of a line file, as `/lines/files` and `/lines/layers/{id}` give it. */
export interface LineLayerInfo {
  id: number;
  file_id: number;
  name: string;
  version: number;
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
  points_only: boolean;
  line_count: number;
  point_count: number;
  length: number;
  closed_count: number;
  closed: "none" | "some" | "all";
  flat: boolean;
  extent: (number | null)[] | null;
  attr_schema: { name: string; type?: string; unit?: string | null; scope?: string }[];
}

export interface LineFileInfo {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  format: string | null;
  label: string | null;
  filename: string | null;
  crs_name: string | null;
  warnings: string[];
  layers: LineLayerInfo[];
}

/** A raster as `/rasters` gives it. */
export interface RasterInfo {
  id: number;
  name: string;
  version: number;
  width: number;
  height: number;
  georef: { corners3d?: (number | null)[][] | null; vertical?: boolean; source?: string; pixel?: number; checks?: unknown[] } | null;
  /** the level of a plan raster; null — under the lowest object */
  elevation: number | null;
  vertical: boolean;
  /** 0.05…1 */
  opacity: number;
  has_elevation: boolean;
  /** the display copies: level 0 — ≤ 4096 px, 1 — ≤ 1024 px */
  displays: { level: number; width: number; height: number; mime: string }[];
  /** role (`display0`, `display1`) → SHA-256 of the image */
  images: Record<string, string>;
  warnings: string[];
}

export interface SceneObjects {
  files: LineFileInfo[];
  rasters: RasterInfo[];
  /** the lists of the open project have been read */
  loaded: boolean;
  error: string | null;
}

const EMPTY: SceneObjects = { files: [], rasters: [], loaded: false, error: null };
export const sceneObjects = new Store<SceneObjects>(EMPTY);

export function lineLayers(o: Pick<SceneObjects, "files"> = sceneObjects.get()): LineLayerInfo[] {
  return o.files.flatMap((f) => f.layers);
}

/** Keys of the view settings of these objects: `ln:<layer>`, `rs:<raster>` (ADR 0007 §2). */
export function objectViewKeys(o: Pick<SceneObjects, "files" | "rasters"> = sceneObjects.get()): string[] {
  return [...lineLayers(o).map((l) => `ln:${l.id}`), ...o.rasters.map((r) => `rs:${r.id}`)];
}

let seq = 0;

/** Reads both lists; an answer overtaken by a newer request or by another project is dropped. */
export async function loadSceneObjects(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) {
    sceneObjects.set(EMPTY);
    return;
  }
  try {
    const [l, r] = await Promise.all([api.get<{ files: LineFileInfo[] }>("/lines/files"), api.get<{ rasters: RasterInfo[] }>("/rasters")]);
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = sceneObjects.get();
    // the same lists keep their identity: subscribers compare by reference
    const files = JSON.stringify(l.files) === JSON.stringify(cur.files) ? cur.files : l.files;
    const rasters = JSON.stringify(r.rasters) === JSON.stringify(cur.rasters) ? cur.rasters : r.rasters;
    if (files !== cur.files || rasters !== cur.rasters || !cur.loaded || cur.error) sceneObjects.set({ files, rasters, loaded: true, error: null });
  } catch (e) {
    if (mine !== seq) return;
    sceneObjects.set({ error: e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e) });
  }
}

/** Keeps the lists in step with the project (called once, with the scene commands). */
export function bindSceneObjects(): void {
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    sceneObjects.set(EMPTY);
    pickedObject.set({ picked: null });
    void loadSceneObjects();
  });
  onCoreEvent((event) => {
    if (event === "objects.changed") void loadSceneObjects();
  });
  onJobFinished((job) => {
    if (job.status === "done" && job.kind.startsWith("import.")) void loadSceneObjects();
  });
}

// --- the object picked in the 3D window --------------------------------------------------------------

export type PickedObject =
  /** `line` — the line (point) of the layer: the row of its attributes; `xyz` — the picked place, absolute */
  | { kind: "lines"; id: number; line: number; on: "line" | "point"; xyz: [number, number, number] }
  | { kind: "raster"; id: number; xyz: [number, number, number] };

export const pickedObject = new Store<{ picked: PickedObject | null }>({ picked: null });
