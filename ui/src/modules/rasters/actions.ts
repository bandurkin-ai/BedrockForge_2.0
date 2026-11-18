/** Changes of rasters made from the data tree, «Свойства» and the commands (ТЗ 7.19, ADR 0011 §3). The list
 * itself is `sceneObjects` (`scene/objects.ts`): one list for the tree and the 3D window. Every change carries
 * the version of the raster; the list is read again after it — after a 409 too. No React here. */
import { api } from "../../shell/api";
import { loadSceneObjects, sceneObjects, type RasterInfo } from "../scene/objects";
import type { Edges, RasterRow } from "./props";

type Versioned = Pick<RasterInfo, "id" | "version">;

export const rasterById = (id: number): RasterRow | undefined => sceneObjects.get().rasters.find((r) => r.id === id);

async function change<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } finally {
    await loadSceneObjects(); // after a 409 too: the next try goes with the new version
  }
}

export function renameRaster(raster: Versioned, name: string): Promise<void> {
  return change(() => api.patch(`/rasters/${raster.id}`, { expected_version: raster.version, name }));
}

/** `raster.set`: `elevation` null puts a plan raster under the lowest object; `vertical` — the edges of a
 * raster in a vertical plane. Only what is given changes. */
export function setRaster(raster: Versioned, changes: { elevation?: number | null; opacity?: number; vertical?: Edges }): Promise<RasterInfo> {
  return change(() => api.patch<RasterInfo>(`/rasters/${raster.id}`, { expected_version: raster.version, ...changes }));
}

/** «Сдвинуть»: +dz up, −dz down; every corner of a vertical raster. */
export function shiftRaster(raster: Versioned, dz: number): Promise<RasterInfo> {
  return change(() => api.post<RasterInfo>(`/rasters/${raster.id}/shift`, { expected_version: raster.version, dz }));
}

/** «На текущий разрез»: `zTop` null — the raster spans the heights of the data. */
export function placeOnSection(raster: Versioned, zTop: number | null): Promise<RasterInfo> {
  return change(() => api.post<RasterInfo>(`/rasters/${raster.id}/place-on-section`, { expected_version: raster.version, z_top: zTop }));
}

/** A raster draped on a DTM is not deleted: 422 `raster_in_use` with the DTMs (`tree.ts` `inUseBy`). */
export function deleteRaster(id: number): Promise<void> {
  return change(() => api.del(`/rasters/${id}`));
}

/** Starts the job `export.raster`: the source file with its georeference, or a GeoTIFF. */
export function startRasterExport(id: number, path: string, format: "source" | "geotiff"): Promise<{ source: string; id: number }> {
  return api.post(`/rasters/${id}/export`, { path, format });
}
