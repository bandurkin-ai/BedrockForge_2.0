// The stub of the core for the tests of the estimation (Э18, 18.19, 18.20): it answers with the real answers of the routes
// (`core/tests/data/estimate/contract_answers.json`, written by `BF_REGEN_ESTIMATE=1 uv run pytest -q tests/test_estimate_api_answers.py`
// of 18.13 — the tests read the file, they do not copy it: every number and id below comes from `A`) and with small fixtures of the
// neighbours (the sets and the tables of the holes, the codes of a field, the stored models of variogram, the block models). A request
// is answered by its path; the bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given refusal.
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

/** The ids and names of the real answers: the model, the set, the tables, the model of variogram, the rows, the plan, the job. */
const D = A.defaults.body;
export const ID = {
  model: D.model.id as number,
  set: D.samples.set_id as number,
  table: D.samples.table as string,
  domTable: D.samples.domain.table as string,
  vario: D.variogram.id as number,
  /** the stored models of variogram in normal scores: without and with the back transform */
  varioY: (D.variogram.id as number) + 1,
  varioBack: (D.variogram.id as number) + 2,
  row: A.estimation.body.id as number,
  row2: A.estimations.body.estimations[1].id as number,
  /** rows the core has not: of the same model, of another model, nowhere */
  row3: (A.estimations.body.estimations[1].id as number) + 1,
  row4: (A.estimations.body.estimations[1].id as number) + 2,
  none: 999,
  plan: A.plan.body.id as number,
  plan1: (A.plan.body.id as number) + 1,
  plan2: (A.plan.body.id as number) + 2,
  otherModel: (D.model.id as number) + 1,
  noModel: 999,
  noPlan: 998,
  job: A.run_submit.body as { source: string; id: number },
};
export const FIELD: string = D.samples.field;
export const MODEL_NAME: string = D.model.name;
/** The version the run was asked with (the version of the model of the answer of `defaults`). */
export const MODEL_VERSION: number = D.model.version;
export const RUN_VERSION: number = A.run_result.body.version;

export const MODEL: BlockModelInfo = {
  id: ID.model,
  name: MODEL_NAME,
  folder: null,
  version: MODEL_VERSION,
  extent: null,
  crs: null,
  grid: { corner: [0, 0, 0], counts: [10, 10, 8], size: D.model.size, order: "F", variable: false },
  cells: D.model.rows,
  fields: [
    { name: "ZONE", kind: "category", unit: null, labels: D.model.domain_fields[0].labels },
    { name: FIELD, kind: "number", unit: D.samples.unit },
  ],
  source: null,
  warnings: [],
  size: null,
  created_at: "",
  updated_at: "",
};

/** A model with a variable step (not offered) and a prototype without blocks (not offered). */
export const VARIABLE: BlockModelInfo = { ...MODEL, id: ID.otherModel, name: "Переменный шаг", grid: { ...MODEL.grid, size: undefined, variable: true } };
export const EMPTY_PROTO: BlockModelInfo = { ...MODEL, id: ID.otherModel + 1, name: "Прототип", cells: 0 };

const vrow = (id: number, name: string, transform: string) => ({ id, name, set_id: ID.set, table: ID.table, field: FIELD, transform, type: "semivariogram", color: null, sill: 1, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null });
/** The stored models of variogram: Z (the one of the answer of `defaults`), Y without the back transform, Y with it. */
export const VARIO_ROWS = [vrow(ID.vario, D.variogram.name, "none"), vrow(ID.varioY, `${FIELD} · Y`, "nscore"), vrow(ID.varioBack, `${FIELD} · Y назад`, "nscore")];

const JOB = ID.job;

/** The plan of the answers, as `GET /estimation-plans` lists it. */
export const PLANS = { plans: [{ id: ID.plan, name: A.plan.body.name, model_id: ID.model, items: A.plan.body.items.map((i: { id: number }) => i.id), version: A.plan.body.version, created_at: "", updated_at: "" }] };

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the result of the job of `POST /estimate/run` */
  job: { status: string; result: unknown; error: unknown };
  /** the answers of `GET /estimations` and `GET /estimation-plans` (the samples of the contract until a test sets its own) */
  estimations: unknown;
  plans: unknown;
  restore: () => void;
}

export function installCore(): Core {
  const sent: Core["sent"] = [];
  const forced = new Map<string, Answer>();
  const core: Core = { sent, force: (route, a) => void forced.set(route, a), job: { status: "done", result: A.run_result.body, error: null }, estimations: A.estimations.body, plans: PLANS, restore: () => vi.unstubAllGlobals() };
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
      if (route === "POST /geostat/estimate/preview") return json(A.preview.body);
      if (route === "GET /geostat/estimations") return json(core.estimations);
      const one = /^GET \/geostat\/estimations\/(\d+)$/.exec(route);
      if (one) return json(Number(one[1]) === ID.row ? A.estimation.body : { ...A.estimation.body, id: Number(one[1]), meta: { ...A.estimation.body.meta, name: `Строка ${one[1]}` } });
      if (route === "POST /geostat/estimations") return json(A.estimation_post.body);
      if (route === `PUT /geostat/estimations/${ID.row}`) return json({ ...A.estimation_post.body, version: A.estimation.body.version + 1 });
      if (route === `DELETE /geostat/estimations/${ID.row}`) return json({ deleted: ID.row, plans: [ID.plan] });
      if (route === "GET /geostat/estimation-plans") return json(core.plans);
      if (route === `GET /geostat/estimation-plans/${ID.plan}`) return json(A.plan.body);
      if (route === "POST /geostat/estimation-plans") return json({ id: ID.plan + 1, name: body.name ?? A.plan.body.name, version: 1 });
      if (route === `PUT /geostat/estimation-plans/${ID.plan}`) return json({ id: ID.plan, name: body.name ?? A.plan.body.name, version: A.plan.body.version + 1 });
      if (route === `DELETE /geostat/estimation-plans/${ID.plan}`) return json({ deleted: ID.plan });
      if (route === "POST /geostat/estimate/run") return json(JOB);
      if (path === "/jobs") return json({ jobs: [{ ...JOB, kind: "estimate.run", title: "Оценка", queue: "heavy", status: core.job.status, progress: 1, message: null, error: core.job.error, params: null, result: core.job.result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null }], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}`) return json({ ...JOB, kind: "estimate.run", status: core.job.status, progress: 1, message: null, error: core.job.error, result: core.job.result });
      if (route === "GET /drillholes/sets") return json({ sets: [{ id: ID.set, name: "Скважины", holes: 20 }] });
      if (route === `GET /drillholes/sets/${ID.set}/tables`) return json({ tables: [{ table: ID.table, kind: "assay", name: D.samples.table_name, rows: D.samples.counts.rows, rev: 5 }, { table: ID.domTable, kind: "interval", name: "Литология", rows: 400, rev: 2 }] });
      if (route === `GET /drillholes/sets/${ID.set}/tables/${ID.table}`) return json({ columns: [{ key: FIELD, name: FIELD, kind: "number", unit: D.samples.unit }, { key: `${FIELD}_CUT`, name: `${FIELD}_CUT`, kind: "number", unit: D.samples.unit }, { key: "DECL_W", name: "DECL_W", kind: "number", unit: null }] });
      if (route === `GET /drillholes/sets/${ID.set}/tables/${ID.domTable}`) return json({ columns: [{ key: "ZONE", name: "ZONE", kind: "text", unit: null }] });
      if (route === "GET /intercepts/codes") return json({ codes: D.model.domain_fields[0].labels, total: 3, truncated: false });
      if (route === "GET /geostat/variography/models") return json({ models: VARIO_ROWS });
      if (route === `GET /geostat/variography/models/${ID.varioY}`) return json({ id: ID.varioY, name: `${FIELD} · Y`, meta: { back: null }, version: 1 });
      if (route === `GET /geostat/variography/models/${ID.varioBack}`) return json({ id: ID.varioBack, name: `${FIELD} · Y назад`, meta: { back: { model: { c0: 0, structures: [] }, share: 0.97 } }, version: 1 });
      if (route === "GET /geostat/trends") return json({ trends: [] });
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
