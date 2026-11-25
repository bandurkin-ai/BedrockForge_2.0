// The stub of the core for the tests of the estimation (Э18, 18.19): it answers with the samples of the contract
// (`core/tests/data/estimate/contract_answers.json`; BF_REGEN_ESTIMATE=1 of 18.13 replaces them with the answers of the real
// routes — the tests read the file, they do not copy it) and with small fixtures of the neighbours (the sets and the tables of
// the holes, the codes of a field, the stored models of variogram, the block models). A request is answered by its path; the
// bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given refusal.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { blocksStore } from "../../src/modules/blocks/state";
import { filtersStore } from "../../src/modules/blocks/filters/state";
import { projectStore } from "../../src/modules/projects/state";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { bindEstimation } from "../../src/modules/estimation/bind";
import { commands } from "../../src/modules/estimation/commands";
import { clearEstimations } from "../../src/modules/estimation/state";
import { resetWizard } from "../../src/modules/estimation/wizardState";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const A: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/estimate/contract_answers.json"), "utf-8"));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const PARAMS: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/estimate/params.json"), "utf-8"));

export interface Answer {
  status: number;
  body: Record<string, unknown>;
}

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export const MODEL: BlockModelInfo = {
  id: 7,
  name: "Модель",
  folder: null,
  version: 4,
  extent: null,
  crs: null,
  grid: { corner: [0, 0, 0], counts: [10, 10, 10], size: [10, 10, 5], order: "F", variable: false },
  cells: 1200,
  fields: [
    { name: "ZONE", kind: "category", unit: null, labels: ["окисл", "перех", "перв"] },
    { name: "AU", kind: "number", unit: "г/т" },
  ],
  source: null,
  warnings: [],
  size: null,
  created_at: "",
  updated_at: "",
};

/** A model with a variable step (not offered) and a prototype without blocks (not offered). */
export const VARIABLE: BlockModelInfo = { ...MODEL, id: 8, name: "Переменный шаг", grid: { ...MODEL.grid, size: undefined, variable: true } };
export const EMPTY_PROTO: BlockModelInfo = { ...MODEL, id: 9, name: "Прототип", cells: 0 };

/** The stored models of variogram: Z, Y without the back transform, Y with it. */
export const VARIO_ROWS = [
  { id: 21, name: "AU · окисл", set_id: 3, table: "12", field: "AU", transform: "none", type: "semivariogram", color: null, sill: 1, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null },
  { id: 22, name: "AU · Y", set_id: 3, table: "12", field: "AU", transform: "nscore", type: "semivariogram", color: null, sill: 1, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null },
  { id: 23, name: "AU · Y назад", set_id: 3, table: "12", field: "AU", transform: "nscore", type: "semivariogram", color: null, sill: 1, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null },
];

const JOB = { source: "proj", id: 5 };

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the result of the job of `POST /estimate/run` */
  job: { status: string; result: unknown; error: unknown };
  restore: () => void;
}

export function installCore(): Core {
  const sent: Core["sent"] = [];
  const forced = new Map<string, Answer>();
  const core: Core = { sent, force: (route, a) => void forced.set(route, a), job: { status: "done", result: A.run_result.body, error: null }, restore: () => vi.unstubAllGlobals() };
  const take = (route: string): Response | null => {
    const f = forced.get(route);
    if (!f) return null;
    forced.delete(route);
    return json(f.body, f.status);
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
      const f = take(route);
      if (f) return f;
      if (route === "GET /geostat/estimate/defaults") return json(A.defaults.body);
      if (route === "GET /geostat/estimations") return json(A.estimations.body);
      if (route === "GET /geostat/estimations/41") return json(A.estimation.body);
      if (route === "POST /geostat/estimations") return json(A.estimation_post.body);
      if (route === "PUT /geostat/estimations/41") return json({ ...A.estimation_post.body, version: 3 });
      if (route === "DELETE /geostat/estimations/41") return json({ deleted: 41, plans: [51] });
      if (route === "GET /geostat/estimation-plans") return json({ plans: [{ id: 51, name: "План · Модель", model_id: 7, items: [41, 42], version: 1, created_at: "", updated_at: "" }] });
      if (route === "POST /geostat/estimate/run") return json(JOB);
      if (path === "/jobs") return json({ jobs: [{ ...JOB, kind: "estimate.run", title: "Оценка", queue: "heavy", status: core.job.status, progress: 1, message: null, error: core.job.error, params: null, result: core.job.result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null }], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}`) return json({ ...JOB, kind: "estimate.run", status: core.job.status, progress: 1, message: null, error: core.job.error, result: core.job.result });
      if (route === "GET /drillholes/sets") return json({ sets: [{ id: 3, name: "Скважины", holes: 20 }] });
      if (route === "GET /drillholes/sets/3/tables") return json({ tables: [{ table: "12", kind: "assay", name: "Композиты", rows: 1196, rev: 5 }, { table: "14", kind: "interval", name: "Литология", rows: 400, rev: 2 }] });
      if (route === "GET /drillholes/sets/3/tables/12") return json({ columns: [{ key: "AU", name: "AU", kind: "number", unit: "г/т" }, { key: "AU_CUT", name: "AU_CUT", kind: "number", unit: "г/т" }, { key: "DECL_W", name: "DECL_W", kind: "number", unit: null }] });
      if (route === "GET /drillholes/sets/3/tables/14") return json({ columns: [{ key: "ZONE", name: "ZONE", kind: "text", unit: null }] });
      if (route === "GET /intercepts/codes") return json({ codes: ["окисл", "перех", "перв"], total: 3, truncated: false });
      if (route === "GET /geostat/variography/models") return json({ models: VARIO_ROWS });
      if (route === "GET /geostat/variography/models/22") return json({ id: 22, name: "AU · Y", meta: { back: null }, version: 1 });
      if (route === "GET /geostat/variography/models/23") return json({ id: 23, name: "AU · Y назад", meta: { back: { model: { c0: 0, structures: [] }, share: 0.97 } }, version: 1 });
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}

/** The app of a test: the commands registered if the catalog has not done it, bound, the project open, the models of the project. */
export function newApp(): App {
  windowStore.set({ windows: [] });
  dialogStore.set({ queue: [] });
  jobsStore.set({ jobs: [], workers: null, error: null });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 1 });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "П", projectReadOnly: false, selected: null });
  if (!app.registry.get("estimate.open")) app.registry.register(commands);
  clearEstimations();
  resetWizard();
  bindEstimation(app);
  blocksStore.set({ list: [MODEL, VARIABLE, EMPTY_PROTO], loaded: true, error: null, details: { 7: MODEL }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  return app;
}

export function teardown(): void {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
}
