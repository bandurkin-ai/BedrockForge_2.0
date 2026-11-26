// The stub of the core for the tests of the implicit modelling (Э19, 19.22): the answers the core wrote through `spec.py` are read from
// `core/tests/data/implicit/contract_answers.json` (`defaults`, `bad_param`, `refusals` — the file is a draft for the rest of the keys until
// 19.16 writes the real answers, so nothing else of it is used) and from `params.json` (`cases.domains.canonical` — what the core stores for
// a model); the other forms are made here from the types of the contract (docs/progress/e19-api.md §3–§5). A request is answered by its
// path; the bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given refusal.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { sceneObjects } from "../../src/modules/scene/objects";
import { bindImplicit } from "../../src/modules/implicit/bind";
import { commands } from "../../src/modules/implicit/commands";
import { clearImplicit } from "../../src/modules/implicit/state";
import { forgetDefaults, resetWizard } from "../../src/modules/implicit/wizardState";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/implicit/contract_answers.json"), "utf-8"));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const PARAMS: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/implicit/params.json"), "utf-8"));

/** The answers `spec.py` wrote: the defaults and the ranges, a refusal of a parameter, the refusals of §7. */
export const A = { defaults: FILE.defaults, bad_param: FILE.bad_param, refusals: FILE.refusals } as {
  defaults: { status: number; body: { params: Record<string, unknown>; ranges: Record<string, { lo: number | null; hi: number | null; lo_open: boolean; integer: boolean; unit: string }> } };
  bad_param: { status: number; body: Record<string, unknown> };
  refusals: Record<string, { status: number; body: Record<string, unknown> }>;
};
/** What the core keeps of the model «Литология» (`spec.parse_model(body).canonical()`). */
export const CANONICAL: Record<string, unknown> = PARAMS.cases.domains.canonical;

export interface Answer {
  status: number;
  body: Record<string, unknown>;
}

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export const ID = { model: 7, model2: 8, noModel: 999, set: 3, table: "12", job: { source: "jobs", id: 112 } as { source: string; id: number }, wire: 50, openWire: 51, dtm: 41, layer: 77 };
export const NAME = CANONICAL.name as string;
export const FIELD = "LITH";

const row = (id: number, name: string, built: boolean, version = 3) => ({
  id,
  name,
  version,
  state: "ready",
  stale: false,
  stale_reason: null,
  source_lost: false,
  groups: 3,
  outputs: built ? 7 : 0,
  stats: built ? { points: 41230, patches: 57, seconds: 41.7 } : null,
  created_at: "2026-11-25T12:00:00Z",
  updated_at: "2026-11-25T12:05:00Z",
});

const output = (id: number, kind: string, group: number | null, name: string) => ({ wireframe_id: id, kind, group, wing: null, threshold: null, name, volume: kind === "body" ? 1250000 : null, area: 81234.5, triangles: 162004 });

/** The result of the job `implicit.build` as §6 describes it. */
export const buildResult = (patch: Record<string, unknown> = {}) => ({
  model_id: ID.model,
  version: 4,
  state: "ready",
  outputs: [output(301, "surface", 1, `${NAME} · Дайка · поверхность`), output(302, "body", 1, `${NAME} · Дайка`), output(305, "body", null, `${NAME} · Прочее`)],
  stats: { points: 41230, patches: 57, max_residual: 3.2e-9, cond_max: 2.4e7, eta_max: 0, seconds: 41.7, reused: { patches: 0, bricks: 0 }, by_group: [] },
  reused: { patches: 0, bricks: 0 },
  journal: [],
  events: [],
  ...patch,
});

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the model list: what `GET /models` answers */
  models: ReturnType<typeof row>[];
  /** the job of `POST …/build` and `…/update` */
  job: { status: string; result: unknown; error: unknown };
  /** the version the core stores for the model 7 (a `PUT` with another one is a 409) */
  version: number;
  restore: () => void;
}

export function installCore(): Core {
  const sent: Core["sent"] = [];
  const forced = new Map<string, Answer>();
  const core: Core = { sent, force: (route, a) => void forced.set(route, a), models: [row(ID.model, NAME, true)], job: { status: "done", result: buildResult(), error: null }, version: 3, restore: () => vi.unstubAllGlobals() };
  const take = (route: string): Response | null => {
    const f = forced.get(route);
    if (!f) return null;
    forced.delete(route);
    return json(f.body, f.status);
  };
  const jobRow = { ...ID.job, kind: "implicit.build", title: "Неявная модель", queue: "heavy", progress: 1, message: null, params: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null };
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
      if (route === "GET /implicit/defaults") return json(A.defaults.body);
      if (route === "GET /implicit/models") return json({ models: core.models });
      const one = /^GET \/implicit\/models\/(\d+)$/.exec(route);
      if (one) {
        const id = Number(one[1]);
        const r = core.models.find((m) => m.id === id);
        if (!r) return json(A.refusals.model_not_found.body, 404);
        return json({ id, name: r.name, version: id === ID.model ? core.version : r.version, meta: { ...CANONICAL, name: r.name }, state: r.state, error: null, stale: false, stale_reason: null, source_lost: false, outputs: [], created_at: r.created_at, updated_at: r.updated_at });
      }
      if (route === "POST /implicit/models") {
        const name = (body.name as string) || "Неявная модель";
        core.models.push(row(ID.model2, name, false, 1));
        return json({ id: ID.model2, name, version: 1 });
      }
      const put = /^PUT \/implicit\/models\/(\d+)$/.exec(route);
      if (put) {
        if (body.expected_version !== core.version) return json({ version: core.version, code: "version_conflict", message: "Модель изменена другим окном.", hint: "Перечитайте модель или сохраните как новую." }, 409);
        core.version += 1;
        return json({ id: Number(put[1]), name: (body.name as string) || NAME, version: core.version });
      }
      const grp = /^POST \/implicit\/models\/(\d+)\/groups$/.exec(route);
      if (grp) return json({ id: Number(grp[1]), version: core.version + 1, group: { id: 4, ...(body.group as object), relation: "conformable", faults: [], params: {} } });
      if (/^POST \/implicit\/models\/\d+\/(build|update)$/.test(route)) return json(ID.job);
      if (path === "/jobs") return json({ jobs: [{ ...jobRow, status: core.job.status, error: core.job.error, result: core.job.result }], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${ID.job.source}/${ID.job.id}`) return json({ ...jobRow, status: core.job.status, error: core.job.error, result: core.job.result });
      if (route === "GET /drillholes/sets") return json({ sets: [{ id: ID.set, name: "Скважины", holes: 20 }] });
      if (route === `GET /drillholes/sets/${ID.set}/tables`) return json({ tables: [{ table: ID.table, kind: "interval", name: "Литология", rows: 400, rev: 2 }, { table: "13", kind: "assay", name: "Пробы", rows: 900, rev: 1 }] });
      if (route === `GET /drillholes/sets/${ID.set}/tables/${ID.table}`) return json({ columns: [{ key: FIELD, name: FIELD, kind: "text", unit: null }, { key: "AU", name: "AU", kind: "number", unit: "г/т" }] });
      if (route === "GET /intercepts/codes") return json({ codes: ["AL", "DY", "SS", "SSc"], total: 4, truncated: false });
      if (route === "GET /geostat/trends") return json({ trends: [{ id: 9, name: "Общий тренд", kind: "global" }, { id: 10, name: "Складка", kind: "structural" }] });
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}

const wire = (id: number, name: string, closed: boolean | undefined, extent: number[] | null, dtm = false) => ({ id, name, version: 1, color: null, point_count: 8, triangle_count: 12, extent, dtm: dtm ? ({ on: true } as never) : null, ...(closed === undefined ? {} : { stats: { closed } }) });

/** The scene objects the wizard offers: a closed solid, an open one, a DTM, a layer of lines. */
export function setScene(): void {
  sceneObjects.set({
    files: [{ id: 1, name: "Стринги", folder: null, version: 1, format: null, label: null, filename: null, crs_name: null, layers: [{ id: ID.layer, file_id: 1, name: "Контакт дайки", version: 1, color: null, points_only: false, line_count: 3, point_count: 30, length: 120, closed_count: 0, closed: "none", extent: null }] } as never],
    rasters: [],
    wireframes: [wire(ID.wire, "Рудное тело", true, [100, 200, -50, 500, 600, 50]), wire(ID.openWire, "Дыра", false, [0, 0, 0, 1, 1, 1]), wire(ID.dtm, "Рельеф", undefined, [0, 0, 0, 1000, 800, 120], true)],
    loaded: true,
    error: null,
  });
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
  if (!app.registry.get("implicit.new")) app.registry.register(commands);
  clearImplicit();
  resetWizard();
  forgetDefaults();
  setScene();
  bindImplicit(app);
  return app;
}

export function teardown(): void {
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
