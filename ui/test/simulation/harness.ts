// The stub of the core for the tests of the simulation (Э18б, 18.35): it answers with the real answers of the routes
// (`core/tests/data/simulate/answers.json`, written by `BF_REGEN_SIMULATE=1 uv run pytest -q tests/test_simulation_api.py` of 18.32 — the
// tests read the file, they do not copy it: every number and id below comes from `S`) and, for the neighbours (the defaults of the data of the
// form, the sets and the tables of the holes, the jobs), with the stub of the estimation (`../estimation/harness.ts`, the answers of
// `contract_answers.json` of the same synthetic project). A request is answered by its path; the bodies sent are kept in `sent`; `force` makes
// the next request of a route answer with a given refusal.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import type { App } from "../../src/shell/app";
import { blocksStore } from "../../src/modules/blocks/state";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { commands } from "../../src/modules/simulation/commands";
import { bindSimulation } from "../../src/modules/simulation/bind";
import { resetPost } from "../../src/modules/simulation/postState";
import { runReset } from "../../src/modules/simulation/runState";
import { clearSimulations } from "../../src/modules/simulation/state";
import { chooseSummary } from "../../src/modules/simulation/summaryState";
import { resetWizard } from "../../src/modules/simulation/wizardState";
import { FIELD, ID, MODEL, installCore, json, newApp as newEstimationApp, teardown, type Answer, type Core } from "../estimation/harness";

export { FIELD, ID, MODEL, teardown, json };
export type { Answer, Core };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const S: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/simulate/answers.json"), "utf-8"));

/** The rows the core made: SGS, SIS by thresholds, co-SGS (their `GET /simulations/{id}`), a categorical SIS (built below), the row a body made. */
export const SID = {
  sgs: S.simulation.body.id as number,
  body: S.run_body_result.body.simulation_id as number,
  sis: S.sis_row.body.id as number,
  cat: S.sis_cat_post.body.id as number,
  co: S.cosgs_row.body.id as number,
  none: 999,
};
/** The stored models of variogram of the project of the answers: in normal scores (`vy`), of the field (`vz`), of the indicators. */
export const VID = {
  vz: ID.vario,
  vy: S.simulation.body.meta.variogram.model_id as number,
  ind: (S.sis_row.body.meta.variograms as { model_id: number }[]).map((v) => v.model_id),
  /** the model in normal scores with a back transform (the stub of this file) */
  back: 20,
};
export const THRESHOLDS: number[] = S.sis_row.body.meta.method.ik.thresholds;
export const MODEL_VERSION: number = S.post_result.body.version - 1;
export const SG: string = S.cosgs_row.body.meta.secondary.field;

/** The block model of the answers: the domain ZONE, the grade AU and the secondary variable SG (density). */
export const SIM_MODEL: BlockModelInfo = {
  ...MODEL,
  id: S.simulation.body.meta.model_id,
  version: MODEL_VERSION,
  fields: [...MODEL.fields, { name: SG, kind: "number", unit: "г/см³" }],
};

const CAT_LABELS = ["Окисленные", "Переходные", "Первичные"];
const CAT_CODES: number[] = S.sis_cat_summary.body.summary.all.codes;
export const LITH = "LITH";

/** The categorical row: the core answers `POST` with its number only; its form is that of the SIS by thresholds with the categories and the median model. */
const CAT_INFO = {
  ...S.sis_row.body,
  id: SID.cat,
  name: S.sis_cat_post.body.name,
  meta: {
    ...S.sis_row.body.meta,
    name: S.sis_cat_post.body.name,
    samples: { ...S.sis_row.body.meta.samples, field: LITH },
    method: { ik: { thresholds: null, categories: CAT_CODES, labels: CAT_LABELS, models: "median" } },
    variograms: null,
    variogram: { ...S.simulation.body.meta.variogram, model_id: ID.vario },
    last_run: { ...S.sis_row.body.meta.last_run, realizations: 4, summary: S.sis_cat_summary.body.summary },
  },
};
export const CAT_ROW = CAT_INFO;

/** The row of the list `GET /simulations` of a stored row (the form of `simulations`). */
function listRow(info: { id: number; name: string; version: number; meta: Record<string, any> }) {
  const m = info.meta;
  const base = S.simulations.body.simulations[0];
  return {
    ...base,
    id: info.id,
    name: info.name,
    kind: m.kind,
    model_id: m.model_id,
    label: m.target.label,
    field: m.samples.field,
    n_real: m.n_real,
    seed: m.seed,
    version: info.version,
    last_run: m.last_run ? { at: m.last_run.at, nodes: m.last_run.nodes, realizations: m.last_run.realizations } : null,
  };
}

export const INFOS: Record<number, any> = { [SID.sgs]: S.simulation.body, [SID.sis]: S.sis_row.body, [SID.cat]: CAT_INFO, [SID.co]: S.cosgs_row.body };
export const LIST = { simulations: [SID.sgs, SID.sis, SID.cat, SID.co].map((id) => listRow(INFOS[id])) };

const vrow = (id: number, name: string, transform: string, field = FIELD) => ({ id, name, set_id: ID.set, table: ID.table, field, transform, type: "semivariogram", color: null, sill: 1, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: null, updated_at: null });
const IND_T = ["0.7", "1", "1.5"];
export const VARIO_ROWS = [
  vrow(VID.vz, "AU · модель", "none"),
  vrow(VID.vy, "AU · Y сим", "nscore"),
  ...VID.ind.map((id, i) => vrow(id, `AU · индикатор ${IND_T[i]}`, `indicator:${IND_T[i]}`)),
  vrow(VID.back, "AU · Y с обратным", "nscore"),
  vrow(VID.back + 1, "AU · Y без обратного", "nscore"),
];

export interface SimCore extends Core {
  /** the answer of `GET /simulations` */
  list: unknown;
  /** the result of the job of `POST /simulations/{id}/post` (the run keeps `core.job`) */
  postJob: { status: string; result: unknown; error: unknown };
}

const JOB = ID.job;

export function installSimCore(): SimCore {
  const core = installCore() as SimCore;
  const delegate = globalThis.fetch;
  const forced = new Map<string, Answer>();
  core.force = (route, a) => void forced.set(route, a);
  core.list = LIST;
  core.job = { status: "done", result: S.run_result.body, error: null };
  core.postJob = { status: "done", result: S.post_result.body, error: null };
  let jobIsPost = false;
  // every job of a test has its own number: the list of «Задания» keeps the finished ones, a new job must not meet the old one
  let jobId = JOB.id;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const route = `${method} ${path}`;
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        core.sent.push({ method, path, query: u.search, body });
        return json(f.body, f.status);
      }
      const sim = path.startsWith("/geostat/simulat");
      const jobs = path === "/jobs" || path.startsWith("/jobs/");
      const vario = path.startsWith("/geostat/variography/models");
      if (!sim && !jobs && !vario) return delegate(url, init);
      if (!jobs) core.sent.push({ method, path, query: u.search, body });
      if (route === "GET /geostat/simulations") return json(core.list);
      if (route === "POST /geostat/simulations") return json(S.simulation_post.body);
      if (route === "POST /geostat/simulate/run") {
        jobIsPost = false;
        return json({ source: JOB.source, id: ++jobId });
      }
      const one = /^(GET|PUT|DELETE) \/geostat\/simulations\/(\d+)$/.exec(route);
      if (one) {
        const id = Number(one[2]);
        const info = INFOS[id];
        if (one[1] === "GET") return info ? json(info) : json({ code: "simulation_not_found", message: `Моделирование №${id} не найдено.`, hint: "Выберите из списка." }, 404);
        if (one[1] === "PUT") return json({ id, name: String(body.name ?? info?.name ?? ""), version: Number(body.expected_version) + 1 });
        return json(S.simulation_delete.body);
      }
      const sum = /^GET \/geostat\/simulations\/(\d+)\/summary$/.exec(route);
      if (sum) {
        const id = Number(sum[1]);
        if (id === SID.cat) return json(S.sis_cat_summary.body);
        if (id === SID.sgs) return json(S.summary.body);
        return json(S.summary_empty.body, 409);
      }
      if (/^POST \/geostat\/simulations\/\d+\/post$/.test(route)) {
        jobIsPost = true;
        return json({ source: JOB.source, id: ++jobId });
      }
      if (vario) {
        if (route === "GET /geostat/variography/models") return json({ models: VARIO_ROWS });
        if (route === `GET /geostat/variography/models/${VID.back}`) return json({ id: VID.back, name: "AU · Y с обратным", meta: { back: { model: { c0: 0, structures: [] }, share: 0.97 } }, version: 1 });
        return json({ id: Number(path.split("/").pop()), name: "модель", meta: { back: null }, version: 1 });
      }
      // the jobs: the list and the job — the run's or the post-processing's result
      const r = jobIsPost ? core.postJob : core.job;
      const job = { ...JOB, id: jobId, kind: jobIsPost ? "simulate.post" : "simulate.run", title: "Моделирование", queue: "heavy", status: r.status, progress: 1, message: null, error: r.error, params: null, result: r.result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null };
      if (path === "/jobs") return json({ jobs: [job], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      return json(job);
    }),
  );
  return core;
}

/** The app of a test: the commands registered if the catalog has not done it, bound, the project open, the block model with the secondary field. */
export function newApp(): App {
  const app = newEstimationApp();
  if (!app.registry.get("simulate.open")) app.registry.register(commands);
  clearSimulations();
  resetWizard();
  runReset();
  resetPost();
  chooseSummary(null);
  bindSimulation(app);
  blocksStore.set({ list: [SIM_MODEL], loaded: true, error: null, details: { [SIM_MODEL.id]: SIM_MODEL }, detailErrors: {} });
  return app;
}

export const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
export async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}
export const requests = (core: Core, route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
export const flat = (s: string): string => s.replace(/[  ]/g, " ");
