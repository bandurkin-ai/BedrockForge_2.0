/** Changes of rasters made from the data tree (ТЗ 7.19, ADR 0011 §3). The list itself is `sceneObjects`
 * (`scene/objects.ts`): one list for the tree and the 3D window. No React here. */
import { api } from "../../shell/api";
import { loadSceneObjects, type RasterInfo } from "../scene/objects";

export async function renameRaster(raster: Pick<RasterInfo, "id" | "version">, name: string): Promise<void> {
  try {
    await api.patch(`/rasters/${raster.id}`, { expected_version: raster.version, name });
  } finally {
    await loadSceneObjects(); // after a 409 too: the next try goes with the new version
  }
}

/** A raster draped on a DTM is not deleted: 422 `raster_in_use` with the DTMs (`tree.ts` `inUseBy`). */
export async function deleteRaster(id: number): Promise<void> {
  try {
    await api.del(`/rasters/${id}`);
  } finally {
    await loadSceneObjects();
  }
}
