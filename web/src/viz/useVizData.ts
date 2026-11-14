import { getIntervals, getLineGeometry, getTrajectories, getWireframeGeometry, listHoles, type Hole, type IntervalsData, type Trajectories } from "../api";
import { loadBuffers, type Bounds } from "./geometryLoader";

// Requests are cached for the session so toggling layers does not refetch megabytes.
// Keys contain refreshKey, so a re-import or a new audit run bypasses stale entries.
const cache = new Map<string, Promise<unknown>>();

export function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  let entry = cache.get(key) as Promise<T> | undefined;
  if (!entry) {
    entry = load().catch((e) => {
      cache.delete(key);
      throw e;
    });
    cache.set(key, entry);
  }
  return entry;
}

export const loadTrajectories = (setId: number, refreshKey: number): Promise<Trajectories> =>
  cached(`traj:${setId}:${refreshKey}`, () => getTrajectories(setId));

export const loadIntervals = (setId: number, component: string, refreshKey: number): Promise<IntervalsData> =>
  cached(`int:${setId}:${refreshKey}:${component}`, () => getIntervals(setId, component));

export const loadHoles = (setId: number, refreshKey: number): Promise<Hole[]> =>
  cached(`holes:${setId}:${refreshKey}`, () => listHoles(setId));

export interface DecodedWireframe {
  origin: number[];
  vertices: Float32Array;
  triangles: Uint32Array;
  /** of `vertices`, relative to origin */
  bounds: Bounds | null;
}

/** Wireframe geometry never changes after import, so it is cached by id only. */
export const loadWireframe = (id: number): Promise<DecodedWireframe> =>
  cached(`wf:${id}`, async () => {
    const g = await getWireframeGeometry(id);
    const { buffers, bounds } = await loadBuffers([g.vertices, g.triangles], 0);
    return { origin: g.origin, vertices: new Float32Array(buffers[0]), triangles: new Uint32Array(buffers[1]), bounds };
  });

export interface DecodedLines {
  origin: number[];
  vertices: Float32Array;
  offsets: Uint32Array;
  bounds: Bounds | null;
}

/** Line geometry never changes after import, so it is cached by id only. */
export const loadLines = (id: number): Promise<DecodedLines> =>
  cached(`ln:${id}`, async () => {
    const g = await getLineGeometry(id);
    const { buffers, bounds } = await loadBuffers([g.vertices, g.offsets], 0);
    return { origin: g.origin, vertices: new Float32Array(buffers[0]), offsets: new Uint32Array(buffers[1]), bounds };
  });
