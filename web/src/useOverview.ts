import { useEffect, useState } from "react";
import { getAudit, getSummary, listHoles, type AuditRun, type Hole, type Summary } from "./api";

export interface Overview {
  summary: Summary | null;
  holes: Hole[];
  audit: AuditRun | null;
  error: string | null;
}

/** Project-wide data for the sidebar and status bar; reloads when `refreshKey` changes. */
export function useOverview(projectId: number, refreshKey: number): Overview {
  const [state, setState] = useState<Overview>({ summary: null, holes: [], audit: null, error: null });

  useEffect(() => {
    let cancelled = false;
    Promise.all([getSummary(projectId), listHoles(projectId), getAudit(projectId)])
      .then(([summary, holes, audit]) => {
        if (!cancelled) setState({ summary, holes, audit, error: null });
      })
      .catch((e: Error) => {
        if (!cancelled) setState((prev) => ({ ...prev, error: e.message }));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  return state;
}
