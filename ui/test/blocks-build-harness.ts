// The stub of the core for the tests of the building of a block model (Э17 17.37): it answers with the REAL answers of the
// core (`core/tests/data/blocks/build_window_answers.json`, made by `core/tests/test_blocks_build_window_answers.py`
// through the routes `/blocks/prototype[/preview]`, `/blocks/fill`, `/blocks/{id}/code|regularize|rebuild` and the jobs
// `blocks.*`). A request is answered by the record whose path and body are the same (a canon of the JSON: the keys in order);
// `force` makes the next request of a route answer with a recorded refusal; a job runs as it was recorded, or is held
// «running» (a stage of its own) until the test lets it end.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { jobsStore } from "../src/modules/jobs/state";
import { blocksStore } from "../src/modules/blocks/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { bindBuild } from "../src/modules/blocks/build/bind";
import { commands } from "../src/modules/blocks/build/commands";
import { resetBuild } from "../src/modules/blocks/build/state";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import type { WireframeInfo } from "../src/modules/scene/objects";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const A: any = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/build_window_answers.json"), "utf-8"));
export const IDS = A.ids as { wire_a: number; wire_b: number; far: number; surface: number; crossing: number; prototype: number; fill: number; plain: number; split: number; oc: number; full: number };

export interface Answer {
  status: number;
  body: Record<string, unknown>;
}
export interface JobCase {
  path: string;
  request: Record<string, unknown>;
  job: { kind: string; title: string; queue: string; status: string; progress: number; message: string | null; error: unknown; result: unknown };
}
export interface RefusedCase extends Answer {
  path: string;
  request: Record<string, unknown>;
}

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
/** A JSON with the keys in order: two bodies are the same request when their canon is the same. */
export const canon = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));
/** The text of a number as the windows write it: the plain space of the thousands is a space, the no-break ones too. */
export const flat = (s: string): string => s.replace(/[  ]/g, " ");

const JOBS: JobCase[] = [...Object.values<JobCase>(A.fill.jobs), ...Object.values<JobCase>(A.code.jobs), ...Object.values<JobCase>(A.regularize.jobs), A.prototype.job, A.prototype.full, A.rebuild.job];
const PREVIEWS = Object.values<RefusedCase>(A.prototype.previews);
const ROUTE = /^\/blocks\/(prototype|fill|\d+\/code|\d+\/regularize|\d+\/rebuild)$/;
const norm = (path: string): string => path.replace(/\/blocks\/\d+\//, "/blocks/{id}/");

export interface Core {
  sent: { method: string; path: string; body: Record<string, unknown> }[];
  /** the next request of a route is refused so (a recorded refusal) */
  force: (a: Answer | RefusedCase | null) => void;
  /** the job runs «running» (stage `message`, progress) until `finish()` */
  hold: (message: string, progress?: number) => void;
  /** the held job ends as the record says */
  finish: () => void;
  /** the model fields of `GET /blocks/{id}/fields` */
  fields: Record<string, unknown> | null;
  cancelled: () => boolean;
  /** the job of the last request */
  last: () => JobCase | null;
  previews: () => number;
  restore: () => void;
}

const JOB = { source: "proj", id: 5 };

export function installCore(): Core {
  const sent: Core["sent"] = [];
  let forced: Answer | null = null;
  let current: JobCase | null = null;
  let held: { message: string; progress: number } | null = null;
  let cancelled = false;
  let previews = 0;
  const core: Core = {
    sent,
    force: (a) => (forced = a ? { status: a.status, body: a.body } : null),
    hold: (message, progress = 0.4) => (held = { message, progress }),
    finish: () => {
      held = null;
    },
    fields: null,
    cancelled: () => cancelled,
    last: () => current,
    previews: () => previews,
    restore: () => vi.unstubAllGlobals(),
  };
  const jobRow = () => {
    const j = current;
    if (!j) return null;
    const run = held !== null || cancelled;
    const status = cancelled ? "cancelled" : held ? "running" : j.job.status;
    return { ...JOB, kind: j.job.kind, title: j.job.title, queue: j.job.queue, status, progress: held ? held.progress : cancelled ? 0.3 : j.job.progress, message: held ? held.message : cancelled ? null : j.job.message, error: run ? null : j.job.error, params: null, result: run ? null : j.job.result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null };
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      const take = (): Response | null => {
        if (!forced) return null;
        const f = forced;
        forced = null;
        return json(f.body, f.status);
      };
      if (method === "POST" && path === "/blocks/prototype/preview") {
        previews += 1;
        const f = take();
        if (f) return f;
        const hit = PREVIEWS.find((p) => canon(p.request) === canon(body));
        return hit ? json(hit.body, hit.status) : json({ code: "unrecorded", message: "Нет записанного ответа", hint: canon(body) }, 599);
      }
      if (method === "POST" && ROUTE.test(path)) {
        const f = take();
        if (f) return f;
        const hit = JOBS.find((j) => norm(j.path) === norm(path) && canon(j.request) === canon(body));
        if (!hit) return json({ code: "unrecorded", message: "Нет записанного ответа", hint: `${path} ${canon(body)}` }, 599);
        current = hit;
        cancelled = false;
        return json(JOB);
      }
      if (path === "/jobs") return json({ jobs: jobRow() ? [jobRow()] : [], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}` && method === "GET") return json(jobRow());
      if (path === `/jobs/${JOB.source}/${JOB.id}/cancel`) {
        cancelled = true;
        return json({});
      }
      if (/^\/blocks\/\d+\/fields$/.test(path) && core.fields) return json(core.fields);
      if (path === "/blocks" && method === "GET") return json(A.blocks.body);
      const one = /^\/blocks\/(\d+)$/.exec(path);
      if (one && method === "GET") return A.models[one[1]] ? json(A.models[one[1]].body) : json((A.blocks.body.block_models as { id: number }[]).find((m) => m.id === Number(one[1])) ?? {}, 200);
      return json({ code: "not_found", message: "Нет", hint: path }, 404);
    }),
  );
  return core;
}

/** The app of a test: the commands of the building registered and bound, the project open, the models and the wireframes of the core's run. */
export function newApp(): App {
  windowStore.set({ windows: [] });
  resetBuild();
  jobsStore.set({ jobs: [], workers: null, error: null });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  if (!app.registry.get("blocks.prototype")) app.registry.register(commands); // the catalog names modules/blocks/build now
  bindBuild(app);
  blocksStore.set({ list: A.blocks.body.block_models as BlockModelInfo[], loaded: true, error: null, details: {}, detailErrors: {} });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: A.wireframes.body.wireframes as WireframeInfo[], files: [], loaded: true });
  return app;
}

/** The model as it was when the request was recorded (the list of the run is the end state: the versions moved). */
export function atVersion(id: number, version: number): void {
  blocksStore.set((s) => ({ list: s.list.map((m) => (m.id === id ? { ...m, version } : m)) }));
}

export function teardown(): void {
  vi.unstubAllGlobals();
  setCoreTarget(null);
}
