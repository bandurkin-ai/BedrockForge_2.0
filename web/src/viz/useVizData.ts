import { getIntervals, getLineGeometry, getTrajectories, getWireframeGeometry, listHoles, type Hole, type IntervalsData, type Trajectories } from "../api";

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

function decode(base64: string): ArrayBuffer {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return bytes.buffer;
}

export interface DecodedWireframe {
  origin: number[];
  vertices: Float32Array;
  triangles: Uint32Array;
}

/** Wireframe geometry never changes after import, so it is cached by id only. */
export const loadWireframe = (id: number): Promise<DecodedWireframe> =>
  cached(`wf:${id}`, async () => {
    const g = await getWireframeGeometry(id);
    return { origin: g.origin, vertices: new Float32Array(decode(g.vertices)), triangles: new Uint32Array(decode(g.triangles)) };
  });

export interface DecodedLines {
  origin: number[];
  vertices: Float32Array;
  offsets: Uint32Array;
}

/** Line geometry never changes after import, so it is cached by id only. */
export const loadLines = (id: number): Promise<DecodedLines> =>
  cached(`ln:${id}`, async () => {
    const g = await getLineGeometry(id);
    return { origin: g.origin, vertices: new Float32Array(decode(g.vertices)), offsets: new Uint32Array(decode(g.offsets)) };
  });
