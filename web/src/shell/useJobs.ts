import { useCallback, useEffect, useRef, useState } from "react";
import { isFinished, listJobs, subscribeJobs, type JobInfo } from "../api";

/** A file being sent from this tab (before its import job exists). */
export interface UploadState {
  key: string;
  filename: string;
  sent: number;
  total: number;
  status: "uploading" | "error" | "done";
  error?: string;
  abort: AbortController;
}

/**
 * The project's background jobs, kept current by the server's event stream, and the uploads
 * of this tab. `onFinished` runs once per job that ends while the tab is open.
 */
export function useJobs(projectId: number, onFinished: (job: JobInfo) => void) {
  const [jobs, setJobs] = useState<JobInfo[]>([]);
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const seen = useRef(new Map<number, string>());
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  const merge = useCallback((job: JobInfo) => {
    const before = seen.current.get(job.id);
    seen.current.set(job.id, job.status);
    if (before !== undefined && before !== job.status && isFinished(job)) finishedRef.current(job);
    setJobs((prev) => {
      const i = prev.findIndex((j) => j.id === job.id);
      if (i < 0) return [job, ...prev].sort((a, b) => b.id - a.id).slice(0, 100);
      const next = prev.slice();
      next[i] = job;
      return next;
    });
  }, []);

  useEffect(() => {
    let alive = true;
    listJobs(projectId)
      .then((list) => {
        if (!alive) return;
        for (const j of list) seen.current.set(j.id, j.status);
        setJobs(list);
      })
      .catch(() => undefined);
    const stop = subscribeJobs(projectId, merge);
    return () => {
      alive = false;
      stop();
    };
  }, [projectId, merge]);

  const trackUpload = useCallback((filename: string, total: number): UploadState => {
    const state: UploadState = { key: `${Date.now()}:${Math.random()}`, filename, sent: 0, total, status: "uploading", abort: new AbortController() };
    setUploads((prev) => [state, ...prev].slice(0, 50));
    return state;
  }, []);

  const updateUpload = useCallback((key: string, patch: Partial<UploadState>) => {
    setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, ...patch } : u)));
  }, []);

  const clearUploads = useCallback(() => setUploads((prev) => prev.filter((u) => u.status === "uploading")), []);

  const active = jobs.filter((j) => !isFinished(j)).length + uploads.filter((u) => u.status === "uploading").length;
  return { jobs, uploads, active, merge, trackUpload, updateUpload, clearUploads };
}
