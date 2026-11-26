// The stub of the core for the tests of the planned holes (Э19б, 19.39): it answers with the REAL answers of the core — the keys `planned_*` of
// `core/tests/data/implicit/contract_answers.json` (written by `core/tests/test_implicit_contract.py`, BF_REGEN_IMPLICIT=1, on the scene of the tests of
// the core: the set 1 of 100 holes, the model «Литология»). A request is answered by its path; the bodies sent are kept in `sent`; `force` makes the next
// request of a route answer with a given answer. The list with the paths (`?trajectory=true`) is made of the real rows: the paths of `planned_post` and
// `planned_target` (the core gives the same two keys for the list without them).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { jobsStore } from "../src/modules/jobs/state";
import { projectStore } from "../src/modules/projects/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { implicitStore } from "../src/modules/implicit/state";
import { bindPlanned } from "../src/modules/drillholes/plannedBind";
import { commands } from "../src/modules/drillholes/plannedCommands";
import { clearPlanned, plannedViewStore, resetAll, resetWin } from "../src/modules/drillholes/plannedState";
import { stopPick } from "../src/modules/drillholes/plannedPick";

export interface Answer {
  status: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  body: any;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FILE: any = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/implicit/contract_answers.json"), "utf-8"));

/** The real answers by key. */
export const R = Object.fromEntries(Object.entries(FILE).filter(([k]) => k.startsWith("planned_"))) as Record<string, Answer>;

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

/** The holes of the real answers: «ПС-1» (a hole that turns, the set 1), «ПС-2 (через цель)». */
export const FIRST = R.planned_post.body as Answer["body"];
export const AIM = R.planned_target.body as Answer["body"];
export const SET = FIRST.meta.set_id as number;
export const MODEL = 6;
export const WIRE = 50;
export const HOLES = ["H001", "H002", "H003"];

export interface Core {
  sent: { method: string; path: string; query: string; body: Record<string, unknown> }[];
  force: (route: string, a: Answer) => void;
  /** the holes `GET /planned_holes` lists (the real ones unless a test changes them) */
  holes: Answer["body"][];
  restore: () => void;
}

export function installCore(): Core {
  const sent: Core["sent"] = [];
  const forced = new Map<string, Answer>();
  const core: Core = { sent, force: (route, a) => void forced.set(route, a), holes: [FIRST, AIM], restore: () => vi.unstubAllGlobals() };
  const answer = (a: Answer) => json(a.body, a.status);
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
        return answer(f);
      }
      if (route === "GET /planned_holes") return json({ planned_holes: core.holes.map((h) => (u.search.includes("trajectory=true") ? h : { ...h, trajectory: undefined, target_depth: undefined })) });
      if (route === "POST /planned_holes") {
        if (typeof body.azimuth === "number" && body.azimuth >= 360) return answer(R.planned_bad);
        const made = body.target ? AIM : FIRST;
        const row = { ...made, name: (body.name as string) ?? made.name, id: 20 + sent.length };
        core.holes.push(row); // the core lists what it made
        return json(row);
      }
      const one = /^(GET|PUT|DELETE) \/planned_holes\/(\d+)$/.exec(route);
      if (one) {
        const id = Number(one[2]);
        const row = core.holes.find((h) => h.id === id);
        if (!row) return json({ code: "planned_hole_not_found", message: "Проектная скважина не найдена.", hint: "Обновите дерево проекта." }, 404);
        if (one[1] === "DELETE") {
          core.holes = core.holes.filter((h) => h.id !== id);
          return answer(R.planned_delete);
        }
        if (one[1] === "PUT") return body.expected_version === row.version ? answer({ status: 200, body: { ...R.planned_put.body, id } }) : answer(R.planned_conflict);
        return json(row);
      }
      const ev = /^POST \/planned_holes\/(\d+)\/evaluate$/.exec(route);
      if (ev) {
        if (body.model_id !== undefined || body.mesh_ids !== undefined) return answer(R.planned_evaluate);
        return answer(body.set_id !== undefined ? R.planned_evaluate_set : R.planned_evaluate_bad);
      }
      if (/^POST \/planned_holes\/\d+\/compare$/.test(route)) return answer(body.hole === "нет такой" ? R.planned_compare_bad : R.planned_compare);
      if (/^POST \/planned_holes\/\d+\/export$/.test(route)) {
        if (String(body.path).includes("занято") && body.overwrite !== true) return answer(R.planned_export_exists);
        return answer(body.format === "dxf" ? R.planned_export_dxf : R.planned_export_csv);
      }
      if (route === "GET /drillholes/sets") return json({ sets: [{ id: SET, name: "Скважины", holes: 100 }] });
      if (route === `GET /viz/sets/${SET}/hole-names`) return json({ names: HOLES });
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  return core;
}

const wire = (id: number, name: string, closed: boolean) => ({ id, name, version: 1, color: null, point_count: 8, triangle_count: 12, extent: [0, 0, 0, 1, 1, 1], dtm: null, stats: { closed } });

/** The scene objects of the forecast: a closed wireframe and an open one. */
export function setScene(): void {
  sceneObjects.set({ files: [], rasters: [], wireframes: [wire(WIRE, "Рудное тело", true), wire(51, "Дыра", false)] as never, loaded: true, error: null });
  implicitStore.set({ rows: [{ id: MODEL, name: "Литология", version: 3, state: "ready", stale: false, stale_reason: null, source_lost: false, groups: 2, outputs: 4, group_list: [], output_list: [], stats: { points: 100, patches: 3, seconds: 1 } as never, created_at: null, updated_at: null }], loaded: true, error: null, details: {} });
}

/** The app of a test: the commands registered if the catalog has not done it, bound, the project open. */
export function newApp(): App {
  windowStore.set({ windows: [] });
  dialogStore.set({ queue: [] });
  jobsStore.set({ jobs: [], workers: null, error: null });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 1 });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "П", projectReadOnly: false, selected: null });
  if (!app.registry.get("planned.new")) app.registry.register(commands);
  stopPick();
  clearPlanned();
  plannedViewStore.set({ hidden: [], chosen: null });
  resetWin();
  resetAll();
  setScene();
  bindPlanned(app);
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
