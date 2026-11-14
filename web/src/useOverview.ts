import { useEffect, useState } from "react";
import { getAudit, getSummary, listDatasets, listHoles, type AuditRun, type Dataset, type Hole, type Summary } from "./api";

export interface Overview {
  datasets: Dataset[] | null;
  /** The refreshKey the datasets were loaded for (-1 before the first load). */
  datasetsKey: number;
  /** The active drillhole set: its summary, holes and latest audit. */
  summary: Summary | null;
  holes: Hole[];
  audit: AuditRun | null;
  error: string | null;
}

/** The project tree and the active set's data; reloads when `refreshKey` changes. */
export function useOverview(projectId: number, setId: number | null, refreshKey: number): Overview {
  const [tree, setTree] = useState<{ list: Dataset[] | null; key: number }>({ list: null, key: -1 });
  const [active, setActive] = useState<Pick<Overview, "summary" | "holes" | "audit">>({ summary: null, holes: [], audit: null });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDatasets(projectId)
      .then((list) => {
        if (cancelled) return;
        setTree({ list, key: refreshKey });
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  useEffect(() => {
    if (setId === null) {
      setActive({ summary: null, holes: [], audit: null });
      return;
    }
    let cancelled = false;
    Promise.all([getSummary(setId), listHoles(setId), getAudit(setId)])
      .then(([summary, holes, audit]) => !cancelled && setActive({ summary, holes, audit }))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [setId, refreshKey]);

  return { datasets: tree.list, datasetsKey: tree.key, ...active, error };
}
