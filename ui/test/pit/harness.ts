// The stub of the core for the tests of the pit optimisation (Э21, 21.16): the answers are read from `core/tests/data/pit/contract_answers.json`
// (the answers of the core written by `core/tests/test_pit_spec.py`: the tests read the forms and the words, not the numbers; `surfaces_result` and `solid_result` are
// written again by the lane of the core of 21.21б-2)
// and from `params.json`. A request is answered by its path; the bodies sent are kept in `sent`; `force` makes the next request of a route answer
// with a given refusal. The scenario of the stub keeps its params like the core does: a `PUT` replaces them and raises the version.
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
import { bindPit } from "../../src/modules/pit/bind";
import { commands } from "../../src/modules/pit/commands";
import { pitStore, resetPit } from "../../src/modules/pit/state";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/pit/contract_answers.json"), "utf-8"));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const PARAMS: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/pit/params.json"), "utf-8"));

export interface Answer {
  status: number;
  body: Record<string, unknown>;
}

/** An answer of the file by its key. */
export const answer = (key: string): Answer => FILE[key] as Answer;

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export const ID = { model: 12, scenario: 17, other: 18, noScenario: 999, runJob: { source: "project", id: 3 }, checkJob: { source: "project", id: 2 }, dtm: 41, filter: 5 };

/** The model of the sample: the fields of `params.json` (`fixture.fields`) — grades AU, CU, the density SG, the categories ROCK and DOM. */
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

/** The canonical params of a case of `params.json`. */
export const caseParams = (name: string): Record<string, unknown> => PARAMS.cases[name].canonical as Record<string, unknown>;

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the params and the version of the scenario the stub keeps */
  scenario: { params: Record<string, unknown>; version: number; name: string; has_result: boolean };
  /** the second scenario of the stub (`ID.other`): another name and the params of the case `rocks_rules` */
  other: { params: Record<string, unknown>; version: number; name: string };
  /** the answer of `POST /pit/validate` */
  ready: boolean;
  /** the job of `pit.run`: the status and the result it answers with */
  job: { status: string; result: unknown; error: unknown };
  checkJob: { status: string; result: unknown; error: unknown };
  restore: () => void;
}

const jobRow = (id: { source: string; id: number }, kind: string, j: { status: string; result: unknown; error: unknown }) => ({
  ...id,
  kind,
  title: kind,
  queue: kind === "pit.run" ? "heavy" : "quick",
  status: j.status,
  progress: j.status === "done" ? 1 : 0.4,
  message: j.status === "done" ? "Готово" : "Оболочки 3/25",
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
    scenario: { params: caseParams("t8117"), version: 2, name: "Сценарий карьера", has_result: false },
    other: { params: caseParams("rocks_rules"), version: 5, name: "Второй сценарий" },
    ready: false,
    job: { status: "done", result: answer("run_result").body, error: null },
    checkJob: { status: "done", result: answer("check_result").body, error: null },
    restore: () => vi.unstubAllGlobals(),
  };
  const detail = () => ({ ...(answer("scenario_get").body as Record<string, unknown>), name: core.scenario.name, version: core.scenario.version, params: core.scenario.params, has_result: core.scenario.has_result });
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
      if (route === "GET /pit/defaults") return json(answer("defaults").body);
      if (route === "POST /pit/validate") return json((core.ready ? answer("validate_ready") : answer("validate_new")).body);
      if (route === "GET /pit/scenarios") return json(answer("scenarios_list").body);
      if (route === "POST /pit/scenarios") {
        core.scenario = { ...core.scenario, params: (body.params as Record<string, unknown>) ?? core.scenario.params, version: 1, name: typeof body.name === "string" ? body.name : "Сценарий карьера" };
        return json({ ...answer("scenario_post").body, name: core.scenario.name });
      }
      if (route === `GET /pit/scenarios/${ID.scenario}`) return json(detail());
      if (route === `PUT /pit/scenarios/${ID.scenario}`) {
        if (body.expected_version !== core.scenario.version) return json({ code: "version_conflict", message: "Сценарий изменён.", hint: "Откройте его заново.", version: core.scenario.version }, 409);
        if (body.params) core.scenario.params = body.params as Record<string, unknown>;
        if (typeof body.name === "string") core.scenario.name = body.name;
        core.scenario.version += 1;
        return json({ id: ID.scenario, version: core.scenario.version, name: core.scenario.name, params_changed: core.scenario.has_result });
      }
      if (route === `GET /pit/scenarios/${ID.other}`) return json({ ...detail(), id: ID.other, name: core.other.name, version: core.other.version, params: core.other.params, has_result: false });
      if (route === `PUT /pit/scenarios/${ID.other}`) {
        if (body.expected_version !== core.other.version) return json({ code: "version_conflict", message: "Сценарий изменён.", hint: "Откройте его заново.", version: core.other.version }, 409);
        if (body.params) core.other.params = body.params as Record<string, unknown>;
        core.other.version += 1;
        return json({ id: ID.other, version: core.other.version, name: core.other.name, params_changed: false });
      }
      if (route === `DELETE /pit/scenarios/${ID.scenario}`) return json(answer("scenario_delete").body);
      if (route === `POST /pit/scenarios/${ID.scenario}/run`) return json(ID.runJob);
      if (route === "POST /pit/check") return json(ID.checkJob);
      if (route === `POST /jobs/${ID.runJob.source}/${ID.runJob.id}/cancel`) {
        core.job = { status: "cancelled", result: null, error: null };
        return json({});
      }
      if (path === "/jobs") return json({ jobs: [jobRow(ID.runJob, "pit.run", core.job), jobRow(ID.checkJob, "pit.check", core.checkJob)], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${ID.runJob.source}/${ID.runJob.id}`) return json(jobRow(ID.runJob, "pit.run", core.job));
      if (path === `/jobs/${ID.checkJob.source}/${ID.checkJob.id}`) return json(jobRow(ID.checkJob, "pit.check", core.checkJob));
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
  if (!app.registry.get("pit.new")) app.registry.register(commands);
  resetPit();
  pitStore.set({ defaults: null });
  blocksStore.set({ list: [MODEL], loaded: true, error: null, details: { [ID.model]: MODEL }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [], loaded: true });
  return { app, unbind: bindPit(app) };
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
