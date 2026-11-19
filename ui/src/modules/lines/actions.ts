/** Changes of line files and layers made from the data tree, «Свойства» and the commands (ТЗ 7.18, ADR 0011
 * §3). The lists themselves are `sceneObjects` (`scene/objects.ts`): one list for the tree and the 3D window.
 * Every change carries the version of the layer; the lists are read again after it — after a 409 too. No React. */
import { api } from "../../shell/api";
import { loadSceneObjects, type LineLayerInfo } from "../scene/objects";
import { attrsPath, type AttrPage } from "./props";

type Versioned = Pick<LineLayerInfo, "id" | "version">;

async function change<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } finally {
    await loadSceneObjects(); // after a 409 too: the next try goes with the new version
  }
}

export function renameLayer(layer: Versioned, name: string): Promise<void> {
  return change(() => api.patch(`/lines/layers/${layer.id}`, { expected_version: layer.version, name }));
}

/** The colour of a layer: "#rrggbb", or null — no colour of its own. */
export function setLayerColor(layer: Versioned, color: string | null): Promise<void> {
  return change(() => api.patch(`/lines/layers/${layer.id}`, { expected_version: layer.version, color }));
}

/** Deletes a file of lines with all its layers. */
export function deleteLineFile(id: number): Promise<void> {
  return change(() => api.del(`/lines/files/${id}`));
}

/** A page of the attributes of a layer: a row per line (per point). */
export function loadAttrs(layerId: number, offset: number, limit?: number): Promise<AttrPage> {
  return api.get<AttrPage>(attrsPath(layerId, offset, limit));
}

export interface LinesExportBody {
  path: string;
  format: string;
  layer_ids: number[];
  delimiter?: string;
  decimal?: string;
  /** LAS of a point cloud: a copy of its source with the classes of the cloud, or a new file */
  las?: "source" | "new";
}

/** Starts the job `export.lines`. */
export function startLinesExport(body: LinesExportBody, overwrite = false): Promise<{ source: string; id: number }> {
  return api.post("/lines/export", { ...body, overwrite });
}
