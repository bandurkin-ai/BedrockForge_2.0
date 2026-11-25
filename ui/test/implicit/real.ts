// The REAL answers of the core for the tests of the tree and of «Свойства» of the implicit models (Э19, 19.23): `core/tests/data/implicit/contract_answers.json`
// (`models`, `model`, `model_stale`, `model_error`, `outputs`, `build_result`, `preview`, `preview_group`, `preview_no_contacts`, `delete_conflict` — written by
// `BF_REGEN_IMPLICIT=1 uv run pytest` of 19.16, not drawn from the types) and `params.json` (`cases.*.canonical` — what the core stores for a model). A request
// is answered by its path; the bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given answer.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { bindImplicit } from "../../src/modules/implicit/bind";
import { commands } from "../../src/modules/implicit/commands";
import { clearImplicit } from "../../src/modules/implicit/state";
import { forgetView } from "../../src/modules/implicit/viewState";
import { forgetDefaults, resetWizard } from "../../src/modules/implicit/wizardState";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/implicit/contract_answers.json"), "utf-8"));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const PARAMS: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/implicit/params.json"), "utf-8"));

/** The real answers by key (`{status, body}`). */
export const R = FILE as Record<string, { status: number; body: Record<string, unknown> }>;
/** The model «Литология» of the list and its details. */
export const MODEL_ID = R.model.body.id as number;
export const LIST = R.models.body as { models: Record<string, unknown>[] };
export const ERROR_ID = R.model_error.body.id as number;
export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export interface RealCore {
  sent: { method: string; path: string; body: Record<string, unknown> }[];
  /** what `GET /models` answers (the real list unless the test sets another) */
  list: Record<string, unknown>[];
  /** the details by model: the real ones of the models 6 (ready) and 13 (error); a test may add */
  details: Map<number, Record<string, unknown>>;
  force: (route: string, a: { status: number; body: unknown }) => void;
}

export function installReal(): RealCore {
  const sent: RealCore["sent"] = [];
  const forced = new Map<string, { status: number; body: unknown }>();
  const core: RealCore = {
    sent,
    list: structuredClone(LIST.models),
    details: new Map([
      [MODEL_ID, R.model.body],
      [ERROR_ID, R.model_error.body],
    ]),
    force: (route, a) => void forced.set(route, a),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      const route = `${method} ${path}`;
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        return json(f.body, f.status);
      }
      if (route === "GET /implicit/models") return json({ models: core.list });
      const one = /^GET \/implicit\/models\/(\d+)$/.exec(route);
      if (one) {
        const d = core.details.get(Number(one[1]));
        return d ? json(d) : json(R.model_not_found.body, 404);
      }
      if (/^POST \/implicit\/models\/\d+\/points\/preview$/.test(route)) return body.group_id === 4 ? json(R.preview_group.body) : json(R.preview.body);
      if (route === "GET /jobs/project/1") return json({ source: "project", id: 1, kind: "implicit.build", status: "done", progress: 1, message: null, error: null, result: R.build_result.body });
      if (route === "GET /geostat/trends") return json({ trends: [] });
      if (path.startsWith("/lineage/")) return json({ items: [] });
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}

/** The app of a test: the commands of the implicit modelling registered and bound, the project open. */
export function realApp(): App {
  windowStore.set({ windows: [] });
  dialogStore.set({ queue: [] });
  jobsStore.set({ jobs: [], workers: null, error: null });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 1 });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "П", projectReadOnly: false, selected: null });
  if (!app.registry.get("implicit.new")) app.registry.register(commands);
  clearImplicit();
  forgetView();
  resetWizard();
  forgetDefaults();
  bindImplicit(app);
  return app;
}

export function teardown(): void {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
  forgetView();
}
