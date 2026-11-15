import { getDownhole, getIntervals, getLineGeometry, getTrajectories, getWireframeGeometry, listHoles, type DownholeData, type Hole, type IntervalsData, type Trajectories, type WireframeGeometry } from "../api";
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

/** Where a wireframe's buffers are; re-read when its levels of detail change. */
export const loadWireframeInfo = (id: number, lodLevels: number): Promise<WireframeGeometry> =>
  cached(`wfi:${id}:${lodLevels}`, () => getWireframeGeometry(id));

export interface WireframeBuffers {
  vertices: Float32Array;
  triangles: Uint32Array;
}

/** One level of detail (0: full). Buffers never change for a level, so they are cached by id and level. */
export const loadWireframeLevel = (info: WireframeGeometry, level: number): Promise<WireframeBuffers> =>
  cached(`wf:${info.id}:${level}`, async () => {
    const lod = info.lods.find((l) => l.level === level);
    const urls = level === 0 ? [info.vertices, info.triangles] : [lod!.vertices, lod!.triangles_url];
    const { buffers } = await loadBuffers(urls);
    return { vertices: new Float32Array(buffers[0]), triangles: new Uint32Array(buffers[1]) };
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

/** The data of interval tables along the holes for the 3D view: every hole (or the listed ones), only the fields drawn. */
export const loadDownhole = (setId: number, holes: string[] | "all", fields: Record<string, string[]>, refreshKey: number): Promise<DownholeData> =>
  cached(`dh:${setId}:${refreshKey}:${holes === "all" ? "all" : holes.join(",")}:${JSON.stringify(fields)}`, () =>
    getDownhole(setId, holes === "all" ? [] : holes, Object.keys(fields), undefined, holes === "all", fields));
