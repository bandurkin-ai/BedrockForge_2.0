/** Background jobs as the «Задания» tab sees them (ТЗ 7.6): the list from the core plus live SSE events. */
import { api } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { coreTarget } from "../../shell/api";
import { Store } from "../../shell/store";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: number;
  source: string;
  kind: string;
  title: string;
  queue: string;
  status: JobStatus;
  progress: number;
  message: string | null;
  error: { code: string; message: string; hint: string } | null;
  /** the parameters the job was started with (e.g. `wireframe_ids` of `wireframe.check`) */
  params?: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  attempts: number;
  max_attempts: number;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  why_waiting: string | null;
}

export interface WorkersInfo {
  workers: { name: string; queue: string; alive: boolean; job: number | null }[];
  heavy_workers: number;
  stopped_reason: string | null;
}

export const STATUS_TITLES: Record<JobStatus, string> = {
  queued: "В очереди",
  running: "Выполняется",
  done: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

export const jobsStore = new Store<{ jobs: Job[]; workers: WorkersInfo | null; error: string | null }>({ jobs: [], workers: null, error: null });

/** Listeners of finished jobs (imports open their project, exports offer the folder). */
const finishedListeners = new Set<(job: Job) => void>();
export function onJobFinished(fn: (job: Job) => void): () => void {
  finishedListeners.add(fn);
  return () => finishedListeners.delete(fn);
}

const key = (j: Pick<Job, "source" | "id">) => `${j.source}:${j.id}`;

const ENDED: readonly JobStatus[] = ["done", "failed", "cancelled"];
/** The first answer of the core is what the window found there (jobs of the past): it reports nothing; the later ones report every job that ended. */
let baseline = false;

/** A job reported ended: it changed its status in the list we hold, or came to the list already ended (a small import is over between two reads). */
export function endedNow(was: JobStatus | undefined, now: JobStatus, known: boolean): boolean {
  return ENDED.includes(now) && (was === undefined ? known : was !== now);
}

export async function refresh(): Promise<void> {
  try {
    const r = await api.get<{ jobs: Job[]; workers: WorkersInfo }>("/jobs");
    const before = new Map(jobsStore.get().jobs.map((j) => [key(j), j.status]));
    const known = baseline;
    baseline = true;
    jobsStore.set({ jobs: r.jobs, workers: r.workers, error: null });
    for (const j of r.jobs) if (endedNow(before.get(key(j)), j.status, known)) for (const fn of finishedListeners) fn(j);
  } catch (e) {
    jobsStore.set({ error: e instanceof Error ? e.message : String(e) });
  }
}

/** Live progress of one job from an SSE event (the list is reloaded on status changes). */
export function applyEvent(data: Partial<Job> & { source: string; id: number }): boolean {
  const jobs = jobsStore.get().jobs;
  const i = jobs.findIndex((j) => key(j) === key(data));
  if (i < 0 || (data.status && data.status !== jobs[i].status)) return false;
  const next = [...jobs];
  next[i] = { ...jobs[i], ...Object.fromEntries(Object.entries(data).filter(([, v]) => v !== undefined)) } as Job;
  jobsStore.set({ jobs: next });
  return true;
}

/** Splits an SSE text stream into events (`event:` / `data:` lines, blank line ends one). */
export function parseSse(buffer: string): { events: { event: string; data: string }[]; rest: string } {
  const events: { event: string; data: string }[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

let running = false;
const eventListeners = new Set<(event: string, data: string) => void>();

/** Other events of the core stream (drillholes.changed, objects.changed…) for the modules that need them. */
export function onCoreEvent(fn: (event: string, data: string) => void): () => void {
  eventListeners.add(fn);
  return () => eventListeners.delete(fn);
}

/** One SSE connection per window (ADR 0002 §3): fetch + stream, reconnect after a core restart. */
export function startEvents(): () => void {
  if (running) return () => undefined;
  running = true;
  let stop = false;
  let ctrl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const schedule = () => {
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      void refresh();
    }, 150);
  };
  const loop = async () => {
    while (!stop) {
      const target = coreTarget();
      if (!target) {
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      ctrl = new AbortController();
      try {
        const r = await fetch(apiUrl(target, "/events"), { headers: authHeaders(target), signal: ctrl.signal });
        if (!r.ok || !r.body) throw new Error(String(r.status));
        void refresh();
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const parsed = parseSse(buf + value);
          buf = parsed.rest;
          for (const ev of parsed.events) {
            if (ev.event !== "job") {
              for (const fn of eventListeners) fn(ev.event, ev.data);
              continue;
            }
            if (!applyEvent(JSON.parse(ev.data))) schedule();
          }
        }
      } catch {
        /* the core restarts: reconnect */
      }
      if (!stop) await new Promise((r) => setTimeout(r, 1000));
    }
  };
  void loop();
  return () => {
    stop = true;
    running = false;
    ctrl?.abort();
  };
}

/** Finished jobs «Очистить выполненные» removes (ТЗ 7.35 Д-10). */
export function finishedJobs(jobs: Pick<Job, "status">[], includeFailed: boolean): number {
  return jobs.filter((j) => j.status === "done" || j.status === "cancelled" || (includeFailed && j.status === "failed")).length;
}

/** Why «Очистить выполненные» is unavailable, or null. */
export function clearReason(jobs: Pick<Job, "status">[], includeFailed: boolean): string | null {
  return finishedJobs(jobs, includeFailed) ? null : "Нет выполненных заданий";
}

export async function clearFinished(includeFailed: boolean): Promise<number> {
  const r = await api.post<{ removed: number }>("/jobs/clear", { include_failed: includeFailed });
  await refresh();
  return r.removed;
}

export async function cancelJob(job: Pick<Job, "source" | "id">): Promise<void> {
  await api.post(`/jobs/${job.source}/${job.id}/cancel`);
  await refresh();
}

export async function retryJob(job: Pick<Job, "source" | "id">): Promise<void> {
  await api.post(`/jobs/${job.source}/${job.id}/retry`);
  await refresh();
}

export async function restartWorkers(): Promise<void> {
  await api.post("/jobs/workers/restart");
  await refresh();
}

export async function jobLog(job: Pick<Job, "source" | "id">): Promise<string> {
  const r = await api.get<{ lines: { at: string; level: string; text: string }[] }>(`/jobs/${job.source}/${job.id}/log`);
  return r.lines.map((l) => `${l.at} ${l.level === "info" ? "" : `[${l.level}] `}${l.text}`).join("\n");
}
