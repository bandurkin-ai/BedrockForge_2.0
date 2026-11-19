/**
 * Operations as jobs (ТЗ 7.23; ADR 0015 §6): the list of operations of the core (`GET /operations`, read once a
 * session), the start — `POST /operations {op, params, name?, folder?}` and «Перестроить» —
 * `POST /wireframes/{id}/rebuild`, `POST /lines/layers/{id}/rebuild` — and what follows a job of an operation:
 * the wireframes made are checked in one job `wireframe.check` (the operation builds no levels of detail — the
 * window queues the check, as the wizard of an import does; `importing/afterImport.ts`), and the report of an
 * operation started in this session opens in its own window. A job is handled once, whether its end is seen by
 * the list of jobs or by the window that started it; only the jobs of the open project count. No React.
 */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { openWindow } from "../../shell/windows";
import { ofOpenProject } from "../importing/afterImport";
import { jobsStore, onJobFinished, refresh, type Job } from "../jobs/state";
import { activeCheck, startChecks } from "../wireframes/checks";
import type { Fix, OpSpec } from "./opForms";

/** The jobs of an operation: the quick queue and, for an input over 2 million triangles, the shared one. */
export const OP_JOBS = ["geometry.operation", "geometry.operation.heavy"];
/** «Построить ЦТМ» and its «Перестроить». */
export const DTM_JOB = "model.dtm";

export const operationsStore = new Store<{ list: OpSpec[] | null; error: string | null }>({ list: null, error: null });

let loading: Promise<OpSpec[]> | null = null;

/** The operations of the core; read once (the list is the same while the core runs). */
export function loadOperations(): Promise<OpSpec[]> {
  const have = operationsStore.get().list;
  if (have) return Promise.resolve(have);
  loading ??= api
    .get<{ operations: OpSpec[] }>("/operations")
    .then((r) => {
      operationsStore.set({ list: r.operations, error: null });
      return r.operations;
    })
    .catch((e: unknown) => {
      operationsStore.set({ error: e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e) });
      throw e;
    })
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** A source as the check names it (`registry.precheck`). */
export interface OpSource {
  role: string;
  kind: string;
  id: number | null;
  name: string;
  version?: number;
  lines?: number[] | null;
}

/** `POST /operations/check`: what will run. */
export interface CheckSummary {
  op: string;
  title: string;
  job: string;
  queue: "quick" | "heavy";
  /** triangles of the input */
  size: number;
  sources: OpSource[];
  /** a large input goes to the shared queue */
  warning: string | null;
}

/** A started job of an operation. */
export interface Started extends CheckSummary {
  source: string;
  id: number;
  rebuild_of?: number;
}

export interface OperationBody {
  op: string;
  params: Record<string, unknown>;
  name?: string | null;
  folder?: string | null;
}

/** A refusal of the core: the text with its hint, and «Исправить каркас» for an open wireframe. */
export function refusalOf(e: unknown): { text: string; fix: Fix | null } {
  if (e instanceof CoreError) {
    const fix = e.body?.fix as Partial<Fix> | undefined;
    const ok = !!fix && fix.op === "repair" && typeof fix.wireframe_id === "number";
    return { text: e.full, fix: ok ? { op: "repair", wireframe_id: fix.wireframe_id! } : null };
  }
  return { text: e instanceof Error ? e.message : String(e), fix: null };
}

export const checkOperation = (body: Pick<OperationBody, "op" | "params">): Promise<CheckSummary> => api.post<CheckSummary>("/operations/check", { op: body.op, params: body.params });

/** The jobs started in this session: their reports open when they are done. */
const tracked = new Map<string, Started>();
/** The last start, for the window that ran `model.operation` through the registry. */
export const startsStore = new Store<{ seq: number; last: Started | null }>({ seq: 0, last: null });

const keyOf = (j: Pick<Job, "source" | "id">): string => `${j.source}:${j.id}`;

function track(started: Started): Started {
  tracked.set(keyOf(started), started);
  startsStore.set((s) => ({ seq: s.seq + 1, last: started }));
  return started;
}

export async function startOperation(body: OperationBody): Promise<Started> {
  const name = body.name?.trim().replace(/\s+/g, " ");
  const sent = { op: body.op, params: body.params, ...(name ? { name } : {}), ...(body.folder ? { folder: body.folder } : {}) };
  return track(await api.post<Started>("/operations", sent));
}

/** What is rebuilt: a wireframe (or a DTM) or a layer of lines made by an operation. */
export type RebuildTarget = { wireframe_id: number } | { layer_id: number };

export async function startRebuild(target: RebuildTarget): Promise<Started> {
  const path = "wireframe_id" in target ? `/wireframes/${target.wireframe_id}/rebuild` : `/lines/layers/${target.layer_id}/rebuild`;
  return track(await api.post<Started>(path));
}

/** The wireframes a finished job of an operation made; [] — none, or it did not end well. */
export function resultWireframes(job: Pick<Job, "kind" | "status" | "result">): number[] {
  if (!OP_JOBS.includes(job.kind) || job.status !== "done") return [];
  const ids = job.result?.wireframe_ids;
  return Array.isArray(ids) ? [...new Set(ids.filter((v): v is number => Number.isInteger(v) && v > 0))] : [];
}

/** The result of a job of an operation (`ops/job.py`). */
export interface OpResult {
  op?: string;
  title?: string;
  name?: string;
  wireframe_ids?: number[];
  layer_ids?: number[];
  file_id?: number | null;
  report?: Record<string, unknown>;
  warnings?: string[];
  volumes?: (number | null)[];
  rebuild_of?: number | null;
}

/** The props of the window «Отчёт операции». */
export interface ReportProps {
  op: string;
  title: string;
  name: string;
  report: Record<string, unknown>;
  warnings: string[];
  wireframeIds: number[];
  layerIds: number[];
  volumes: (number | null)[];
  rebuildOf: number | null;
}

export function reportProps(job: Pick<Job, "title" | "result">, started?: Pick<Started, "op" | "title">): ReportProps {
  const r = (job.result ?? {}) as OpResult;
  return {
    op: r.op ?? started?.op ?? "",
    title: r.title ?? started?.title ?? job.title,
    name: r.name ?? "",
    report: r.report ?? {},
    warnings: r.warnings ?? [],
    wireframeIds: r.wireframe_ids ?? [],
    layerIds: r.layer_ids ?? [],
    volumes: r.volumes ?? [],
    rebuildOf: r.rebuild_of ?? null,
  };
}

export function openReport(props: ReportProps): string {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  return openWindow("model-report", `Отчёт: ${props.title}`, "form", props as unknown as Record<string, unknown>, vw, vh);
}

const handled = new Set<string>();
type Finished = Pick<Job, "source" | "id" | "kind" | "status" | "result" | "title" | "finished_at">;

export interface AfterDeps {
  startMany?: (ids: number[]) => Promise<unknown>;
  showReport?: (props: ReportProps) => unknown;
}

/** After a job of an operation: its wireframes are checked (the ones not being checked already), and the report
 * of a job started in this session opens. Returns what was done; a job is handled once. */
export async function afterOperation(job: Finished, deps: AfterDeps = {}): Promise<{ checked: number; report: boolean }> {
  const none = { checked: 0, report: false };
  if (![...OP_JOBS, DTM_JOB].includes(job.kind) || job.status !== "done" || !ofOpenProject(job)) return none;
  const key = `${keyOf(job)}:${job.finished_at ?? ""}`;
  if (handled.has(key)) return none;
  handled.add(key);
  const started = tracked.get(keyOf(job));
  tracked.delete(keyOf(job));
  const ids = resultWireframes(job).filter((id) => !activeCheck(jobsStore.get().jobs, id));
  let checked = 0;
  if (ids.length) {
    try {
      await (deps.startMany ?? startChecks)(ids.slice(0, 2000));
      checked = Math.min(ids.length, 2000);
      void refresh();
    } catch {
      // the wireframes were deleted meanwhile: «Проверить» of «Свойства» stays
    }
  }
  const report = !!started && job.kind !== DTM_JOB;
  if (report) (deps.showReport ?? openReport)(reportProps(job, started));
  return { checked, report };
}

/** Every job of an operation that ends while the interface runs (also a retry from «Задания»). */
export function watchOperations(): () => void {
  return onJobFinished((job) => void afterOperation(job));
}

/** The window or the command that started the job: it may have ended before the list of jobs first saw it. */
export async function afterStart(started: Pick<Started, "source" | "id">): Promise<void> {
  await refresh();
  const done = jobsStore.get().jobs.find((j) => j.source === started.source && j.id === started.id && j.status === "done");
  if (done) await afterOperation(done);
}

/** «очередь: быстрая» · «общая очередь» */
export const queueText = (queue: string): string => (queue === "heavy" ? "общая очередь (вход больше 2 млн треугольников)" : "быстрая очередь");

/** For the tests. */
export function forgetOperations(): void {
  handled.clear();
  tracked.clear();
  startsStore.set({ seq: 0, last: null });
  operationsStore.set({ list: null, error: null });
}
