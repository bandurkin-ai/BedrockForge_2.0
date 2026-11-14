import { useEffect, useState } from "react";
import {
  getHoleLog,
  getIntervals,
  getTrajectories,
  type HoleLog,
  type IntervalsData,
  type Trajectories,
} from "../api";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

// Requests are cached for the session so switching between views does not refetch ~2 MB.
// The key contains refreshKey, so a re-import or a new audit run bypasses stale entries.
const cache = new Map<string, Promise<unknown>>();

function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
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

function useCached<T>(key: string | null, load: () => Promise<T>): Async<T> {
  const [state, setState] = useState<Async<T>>({ data: null, loading: key !== null, error: null });

  useEffect(() => {
    if (key === null) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    cached(key, load)
      .then((data) => !cancelled && setState({ data, loading: false, error: null }))
      .catch((e: Error) => !cancelled && setState({ data: null, loading: false, error: e.message }));
    return () => {
      cancelled = true;
    };
    // `load` is derived from the key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}

export const useTrajectories = (projectId: number, refreshKey: number): Async<Trajectories> =>
  useCached(`traj:${projectId}:${refreshKey}`, () => getTrajectories(projectId));

export const useIntervals = (projectId: number, component: string | null, refreshKey: number): Async<IntervalsData> =>
  useCached(component ? `int:${projectId}:${refreshKey}:${component}` : null, () => getIntervals(projectId, component!));

export const useHoleLog = (projectId: number, holeId: string | null, refreshKey: number): Async<HoleLog> =>
  useCached(holeId ? `log:${projectId}:${refreshKey}:${holeId}` : null, () => getHoleLog(projectId, holeId!));
