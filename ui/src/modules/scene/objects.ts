/**
 * Line layers, rasters and wireframes of the open project as the 3D window (and whoever else needs the lists)
 * sees them (ADR 0011 §3): `GET /lines/files` — files with their layers, `GET /rasters` — rasters with their
 * georeference, `GET /wireframes` — wireframes and DTMs with the settings of the relief. The lists follow the
 * project: read when it opens, again on `objects.changed`, after an import of objects and after «Построить
 * ЦТМ». No three.js here (the store is in the main bundle); the picked object of the 3D window is kept here
 * too, for the properties panel.
 */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import type { ExactPick } from "../../viz/mesh/exact";
import type { CloudSummary } from "../lines/classes";
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
  /** a point cloud (ADR 0014): its parts, classes, colour modes and source; null / absent — an ordinary layer */
  cloud?: CloudSummary | null;
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

/** The settings of a DTM: the raster draped on it and how the image is shown (ТЗ 7.20). */
export interface DtmInfo {
  raster_id: number | null;
  /** «Скрыть данные за ЦТМ» */
  clip: boolean;
  /** "#rrggbb": the image is multiplied by it */
  tint: string;
  /** 0…1 */
  opacity: number;
  shade: boolean;
}

/** What the job `wireframe.check` found (ТЗ 7.21, ADR 0013 §3): `wireframes.stats.checks`. */
export interface WireframeChecks {
  /** "failed" — the mesh could not be checked: `error` says why */
  state: "done" | "failed";
  /** edges two neighbours walk the same way: their normals look to different sides */
  normal_conflicts?: number;
  /** pairs of triangles that cross */
  pairs?: number;
  /** triangles that cross another one, and how many of them the red layer of the 3D window holds */
  triangles?: number;
  listed?: number;
  /** how long the checks took, s; when they were made (UTC, ISO) */
  seconds?: number;
  checked_at?: string;
  error?: { code: string; message: string; hint: string };
}

/** Measures of a wireframe taken when it was stored, and its checks once the job has run. */
export interface WireframeStats {
  area?: number | null;
  plan_area?: number | null;
  /** of a closed solid; null — the wireframe is not closed */
  volume?: number | null;
  closed?: boolean;
  /** of a closed solid: where its normals look */
  orientation?: "outward" | "inward" | null;
  open_edges?: number;
  nonmanifold_edges?: number;
  degenerate?: number;
  checks?: WireframeChecks | null;
}

/** Where an imported wireframe came from (`objects.meta.source`, in brief). */
export interface WireframeSource {
  format?: string;
  file?: string;
  /** the name of the wireframe in its file */
  name?: string;
  layer?: string;
  /** "float32" — the file keeps coordinates in single precision (STL) */
  precision?: string;
}

/** A wireframe as `/wireframes` gives it (what the 3D window needs of it). */
export interface WireframeInfo {
  id: number;
  name: string;
  version: number;
  /** "#rrggbb"; null — no colour of its own */
  color: string | null;
  point_count: number;
  triangle_count: number;
  /** [xmin, ymin, zmin, xmax, ymax, zmax] */
  extent: (number | null)[] | null;
  /** null — a wireframe of «Каркасы»; settings — a DTM */
  dtm: DtmInfo | null;
  stats?: WireframeStats;
  /** null — made in the project */
  source?: WireframeSource | null;
}

export interface SceneObjects {
  files: LineFileInfo[];
  rasters: RasterInfo[];
  wireframes: WireframeInfo[];
  /** the lists of the open project have been read */
  loaded: boolean;
  error: string | null;
}

const EMPTY: SceneObjects = { files: [], rasters: [], wireframes: [], loaded: false, error: null };
export const sceneObjects = new Store<SceneObjects>(EMPTY);

export function lineLayers(o: Pick<SceneObjects, "files"> = sceneObjects.get()): LineLayerInfo[] {
  return o.files.flatMap((f) => f.layers);
}

/** Keys of the view settings of these objects: `ln:<layer>`, `rs:<raster>`, `wf:<wireframe>` (ADR 0007 §2). */
export function objectViewKeys(o: Pick<SceneObjects, "files" | "rasters"> & Partial<Pick<SceneObjects, "wireframes">> = sceneObjects.get()): string[] {
  return [...lineLayers(o).map((l) => `ln:${l.id}`), ...o.rasters.map((r) => `rs:${r.id}`), ...(o.wireframes ?? []).map((w) => `wf:${w.id}`)];
}

let seq = 0;

/** Reads the lists; an answer overtaken by a newer request or by another project is dropped. */
export async function loadSceneObjects(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) {
    sceneObjects.set(EMPTY);
    return;
  }
  try {
    const [l, r, w] = await Promise.all([
      api.get<{ files: LineFileInfo[] }>("/lines/files"),
      api.get<{ rasters: RasterInfo[] }>("/rasters"),
      api.get<{ wireframes?: WireframeInfo[] }>("/wireframes"),
    ]);
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = sceneObjects.get();
    // the same lists keep their identity: subscribers compare by reference
    const files = JSON.stringify(l.files) === JSON.stringify(cur.files) ? cur.files : l.files;
    const rasters = JSON.stringify(r.rasters) === JSON.stringify(cur.rasters) ? cur.rasters : r.rasters;
    const found = w.wireframes ?? [];
    const wireframes = JSON.stringify(found) === JSON.stringify(cur.wireframes) ? cur.wireframes : found;
    if (files !== cur.files || rasters !== cur.rasters || wireframes !== cur.wireframes || !cur.loaded || cur.error) sceneObjects.set({ files, rasters, wireframes, loaded: true, error: null });
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
    if (job.status === "done" && (job.kind.startsWith("import.") || job.kind === "model.dtm")) void loadSceneObjects();
    // a check writes its results wireframe by wireframe: a cancelled or failed one has changed «Свойства» too
    else if (job.kind === "wireframe.check") void loadSceneObjects();
  });
}

// --- the object picked in the 3D window --------------------------------------------------------------

export type PickedObject =
  /**
   * `line` — the line (point) of the layer: the row of its attributes; `xyz` — the picked place, absolute;
   * `cloud` — a point of a cloud: the colour mode, its class (when the mode draws classes), `h` / `dz`, m
   */
  | { kind: "lines"; id: number; line: number; on: "line" | "point"; xyz: [number, number, number]; cloud?: { mode: "class" | "layer" | "h" | "dz"; class: number | null; value: number | null } }
  | { kind: "raster"; id: number; xyz: [number, number, number] }
  /**
   * a wireframe or a DTM. `xyz` — the picked place: as the level of detail in view shows it, then — once the
   * core has answered — on the exact triangle; `exact` — that triangle (the point, how it lies), null — the
   * core found none under the click, undefined — not answered yet
   */
  | { kind: "wireframe"; id: number; xyz: [number, number, number]; exact?: ExactPick | null };

export const pickedObject = new Store<{ picked: PickedObject | null }>({ picked: null });
