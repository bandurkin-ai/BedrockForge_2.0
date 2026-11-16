import { useEffect, useMemo, useRef, useState } from "react";
import { runSwath, type SwathResult } from "../api";
import type { SwathLayerInput } from "../viz/SceneView";
import type { SwathState } from "./SwathPanel";
import { buildSwath, swathProblem, swathRequest, type SwathBuild, type SwathSettings } from "./swath";

export interface SwathItem {
  id: number;
  settings: SwathSettings;
}

/**
 * Counts the swath plots that are open or drawn in the 3D view: again a moment after their
 * settings change (only what changes the numbers), and when the project's data change.
 */
export function useSwathStates(items: SwathItem[], refreshKey: number): Record<number, SwathState> {
  const [states, setStates] = useState<Record<number, SwathState>>({});
  const jobs = useRef(new Map<number, { key: string; timer: number; controller: AbortController | null }>());
  const keyed = useMemo(() => items.map(({ id, settings }) => {
    const problem = swathProblem(settings);
    return { id, settings, problem, key: problem ? `!${problem}` : `${refreshKey}:${JSON.stringify(swathRequest(settings))}` };
  }), [items, refreshKey]);
  const signature = keyed.map((k) => `${k.id}:${k.key}`).join("|");

  useEffect(() => {
    const wanted = new Set(keyed.map((k) => k.id));
    const stop = (id: number) => {
      const job = jobs.current.get(id);
      if (!job) return;
      window.clearTimeout(job.timer);
      job.controller?.abort();
    };
    const gone = [...jobs.current.keys()].filter((id) => !wanted.has(id));
    for (const id of gone) {
      stop(id);
      jobs.current.delete(id);
    }
    if (gone.length) setStates((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => wanted.has(Number(id)))));
    for (const { id, settings, problem, key } of keyed) {
      const job = jobs.current.get(id);
      if (job?.key === key) continue;
      stop(id);
      if (problem) {
        jobs.current.set(id, { key, timer: 0, controller: null });
        setStates((prev) => ({ ...prev, [id]: { busy: false, error: null, result: prev[id]?.result ?? null } }));
        continue;
      }
      const controller = new AbortController();
      const mine = () => jobs.current.get(id)?.key === key;
      const timer = window.setTimeout(() => {
        runSwath(swathRequest(settings), controller.signal).then(
          (result: SwathResult) => mine() && setStates((prev) => ({ ...prev, [id]: { busy: false, error: null, result } })),
          (e: Error) => e.name !== "AbortError" && mine() && setStates((prev) => ({ ...prev, [id]: { busy: false, error: e.message, result: null } })),
        );
      }, job ? 350 : 0);
      jobs.current.set(id, { key, timer, controller });
      setStates((prev) => ({ ...prev, [id]: { busy: true, error: null, result: prev[id]?.result ?? null } }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  useEffect(() => () => {
    for (const job of jobs.current.values()) {
      window.clearTimeout(job.timer);
      job.controller?.abort();
    }
    jobs.current.clear();
  }, []);
  return states;
}

/**
 * What the 3D view draws for the plots that are on: a build is made again only when its result,
 * its settings or the swath under the cursor changed.
 */
export function useSwathLayers(items: SwathItem[], states: Record<number, SwathState>, hover: { id: number; index: number } | null): SwathLayerInput[] {
  const cache = useRef(new Map<number, { result: SwathResult; settings: SwathSettings; hover: number | null; build: SwathBuild }>());
  return useMemo(() => {
    const out: SwathLayerInput[] = [];
    const seen = new Set<number>();
    for (const { id, settings } of items) {
      const result = states[id]?.result;
      // An answer for another list of series is on its way out: nothing to draw the lines with.
      if (!result || result.series.length !== settings.series.length) continue;
      seen.add(id);
      const h = hover?.id === id ? hover.index : null;
      let entry = cache.current.get(id);
      if (!entry || entry.result !== result || entry.settings !== settings || entry.hover !== h) {
        entry = { result, settings, hover: h, build: buildSwath(result, settings, h) };
        cache.current.set(id, entry);
      }
      out.push({ id, build: entry.build });
    }
    for (const id of [...cache.current.keys()]) if (!seen.has(id)) cache.current.delete(id);
    return out;
  }, [items, states, hover]);
}
