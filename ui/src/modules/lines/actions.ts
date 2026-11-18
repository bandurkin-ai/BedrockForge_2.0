/** Changes of line files and layers made from the data tree (ТЗ 7.18, ADR 0011 §3). The lists themselves are
 * `sceneObjects` (`scene/objects.ts`): one list for the tree and the 3D window. No React here. */
import { api } from "../../shell/api";
import { loadSceneObjects, type LineLayerInfo } from "../scene/objects";

export async function renameLayer(layer: Pick<LineLayerInfo, "id" | "version">, name: string): Promise<void> {
  try {
    await api.patch(`/lines/layers/${layer.id}`, { expected_version: layer.version, name });
  } finally {
    await loadSceneObjects(); // after a 409 too: the next try goes with the new version
  }
}

/** Deletes a file of lines with all its layers. */
export async function deleteLineFile(id: number): Promise<void> {
  try {
    await api.del(`/lines/files/${id}`);
  } finally {
    await loadSceneObjects();
  }
}
