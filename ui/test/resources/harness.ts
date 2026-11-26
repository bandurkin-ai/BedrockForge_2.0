// The stub of the core for the tests of the resource report (Э20, 20.12): the answers are read from `core/tests/data/resources/contract_answers.json`
// (`defaults`, `bad_param`, `group_numeric`, `refusals` are the real ones, written by `spec.py`; the other keys are samples of the forms of
// docs/progress/e20-api.md until 20.7 writes the real answers — the tests read the forms, not the numbers of the samples) and from `params.json`.
// A request is answered by its path; the bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given refusal.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { filtersStore } from "../../src/modules/blocks/filters/state";
import { blocksStore } from "../../src/modules/blocks/state";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { sceneObjects } from "../../src/modules/scene/objects";
import { bindResources } from "../../src/modules/resources/bind";
import { commands } from "../../src/modules/resources/commands";
import { resetResources, resourcesStore } from "../../src/modules/resources/state";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/resources/contract_answers.json"), "utf-8"));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const PARAMS: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/resources/params.json"), "utf-8"));

export interface Answer {
  status: number;
  body: Record<string, unknown>;
}

/** An answer of the file by its key. */
export const answer = (key: string): Answer => FILE[key] as Answer;

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export const ID = { model: 12, report: 2, noReport: 999, reportJob: { source: "jobs", id: 41 }, exportJob: { source: "jobs", id: 42 }, filter: 5, dtm: 41, wire: 50 };

/** The model of the sample: the fields of `params.json` (`fixture.fields`) — grades AU, CU, the density SG, the categories CLASS and DOM. */
export const MODEL = {
  id: ID.model,
  name: "Рудное тело",
  folder: null,
  version: 4,
  extent: null,
  crs: null,
  grid: {},
  cells: 8,
  fields: PARAMS.fixture.fields,
  source: null,
  warnings: [],
  size: null,
  created_at: "",
  updated_at: "",
} as unknown as BlockModelInfo;

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the job of `POST /resources/reports`: the status and the result it answers with */
  job: { status: string; result: unknown; error: unknown };
  /** the job of the export */
  exportJob: { status: string; result: unknown; error: unknown };
  restore: () => void;
}

const jobRow = (id: { source: string; id: number }, kind: string, j: { status: string; result: unknown; error: unknown }) => ({
  ...id,
  kind,
  title: kind,
  queue: "quick",
  status: j.status,
  progress: j.status === "done" ? 1 : 0.4,
  message: j.status === "done" ? "Готово" : "Расчёт",
  error: j.error,
  result: j.result,
  params: null,
  attempts: 1,
  max_attempts: 1,
  cancel_requested: false,
  created_at: "",
  started_at: null,
  finished_at: null,
  why_waiting: null,
});

export function installCore(): Core {
  const sent: Core["sent"] = [];
  const forced = new Map<string, Answer>();
  const core: Core = {
    sent,
    force: (route, a) => void forced.set(route, a),
    job: { status: "done", result: answer("report_result").body, error: null },
    exportJob: { status: "done", result: answer("export_result").body, error: null },
    restore: () => vi.unstubAllGlobals(),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, query: u.search, body });
      const route = `${method} ${path}`;
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        return json(f.body, f.status);
      }
      if (route === "GET /resources/defaults") return json(answer("defaults").body);
      if (route === "GET /resources/templates") return json(answer("templates").body);
      if (route === "GET /resources/reports") return json(answer("reports_list").body);
      if (route === "POST /resources/reports") {
        // the sample of the result is a saved report: the preview (`save: false`) has no number
        const r = core.job.result as Record<string, unknown> | null;
        if (r && "saved" in r) core.job = { ...core.job, result: { ...r, saved: body.save === true, report_id: body.save === true ? ID.report : null, name: typeof body.name === "string" ? body.name : r.name } };
        return json(ID.reportJob);
      }
      if (route === `GET /resources/reports/${ID.report}`) return json(answer("report_get").body);
      if (route === `DELETE /resources/reports/${ID.report}`) return json(answer("report_delete").body);
      if (route === `POST /resources/reports/${ID.report}/export`) return json(ID.exportJob);
      if (route === `POST /resources/reports/${ID.report}/drill`) return json(answer("drill").body);
      if (route === "POST /resources/compare") return json(answer("compare_reports").body);
      if (route === `POST /jobs/${ID.reportJob.source}/${ID.reportJob.id}/cancel`) {
        core.job = { status: "cancelled", result: null, error: null };
        return json({});
      }
      if (path === "/jobs") return json({ jobs: [jobRow(ID.reportJob, "resources.report", core.job), jobRow(ID.exportJob, "resources.export", core.exportJob)], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${ID.reportJob.source}/${ID.reportJob.id}`) return json(jobRow(ID.reportJob, "resources.report", core.job));
      if (path === `/jobs/${ID.exportJob.source}/${ID.exportJob.id}`) return json(jobRow(ID.exportJob, "resources.export", core.exportJob));
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}

/** The app of a test: the commands registered if the catalog has not done it, bound, the project open, the model of the sample in the project. */
export function newApp(): { app: App; unbind: () => void } {
  windowStore.set({ windows: [] });
  dialogStore.set({ queue: [] });
  jobsStore.set({ jobs: [], workers: null, error: null });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 1 });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "П", projectReadOnly: false, selected: null });
  if (!app.registry.get("resources.report")) app.registry.register(commands);
  resetResources();
  resourcesStore.set({ defaults: null, reportsLoaded: false });
  blocksStore.set({ list: [MODEL], loaded: true, error: null, details: { [ID.model]: MODEL }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [], loaded: true });
  return { app, unbind: bindResources(app) };
}

export function teardown(unbind?: () => void): void {
  unbind?.();
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
}

export const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
export async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}
