// The stub of the core for the tests of the graph of dependencies (Э18, 18.Г3): it answers with the answers of the REAL core
// (`core/tests/data/lineage/answers.json`, made by `BF_REGEN_LINEAGE=1 uv run pytest tests/test_lineage_window_answers.py` — a
// chain of stub objects through the routes `/lineage/*`, and a result of composites → its table → a model of variography).
// A route is answered by its path and the node asked; the bodies sent are kept in `sent`; `force` makes the next request of a
// route answer with a given refusal. The states the tree shows are one of the recorded `status_*` answers (`world`).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { bindLineage } from "../../src/modules/lineage/bind";
import { commands } from "../../src/modules/lineage/commands";
import { clearLineage } from "../../src/modules/lineage/state";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const A: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/lineage/answers.json"), "utf-8"));
/** the chain of stub objects: S0 → N1 → {N2 → N3 → {N4, Отчёт}, M} */
export const S = A.stub;
/** the real results: «Пробы» → result «Композиты 2 м» → its table → the model of variography «Модель AU» */
export const R = A.real;

export interface Answer {
  status: number;
  body: unknown;
}

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

export interface Sent {
  method: string;
  path: string;
  query: string;
  body: Record<string, unknown>;
}

/** The job the 202 of `POST /lineage/rebuild` names (the core masks the project id of the recorded answer). */
export const JOB = { source: "proj", id: 5 };

export interface Core {
  sent: Sent[];
  /** the next request of a route (`POST /lineage/rebuild`) answers with this */
  force: (route: string, a: Answer) => void;
  /** the start (not the dry run) of a rebuild answers with this 409 until `drop_lost` is sent */
  lost: Answer | null;
  /** the recorded answers by name: `status_stale` — what `POST /lineage/status` answers to the nodes it lists */
  world: { status: string; impact: Record<string, string>; origin: Record<string, string>; plan: string; started: string; job: string };
  restore: () => void;
}

/** A recorded answer by name: `dry_run` is of the chain of stubs, `real:dry_run` — of the real results. */
const recorded = (name: string): Answer => (name.startsWith("real:") ? R[name.slice(5)] : S[name]);

const idOf = (n: { kind: string; id: number }) => `${n.kind}:${n.id}`;

export function installCore(): Core {
  const sent: Sent[] = [];
  const forced = new Map<string, Answer>();
  const core: Core = {
    sent,
    force: (route, a) => void forced.set(route, a),
    lost: null,
    world: {
      status: "status_current",
      impact: { "object:stub:2": "impact_n1", "object:stub:1": "impact_s0", "object:stub:6": "impact_leaf" },
      origin: { "object:stub:3": "origin_n2_stale", "object:stub:1": "origin_s0" },
      plan: "dry_run",
      started: "rebuild_started",
      job: "job_done",
    },
    restore: () => vi.unstubAllGlobals(),
  };
  const byNode = (table: Record<string, string>, node: string) => (S[table[node]] ?? null) as Answer | null;
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
      if (route === "POST /lineage/status") {
        // the states of the nodes asked, from the recorded answer (a node it does not know is «deleted», as the core says)
        const known = new Map<string, unknown>();
        for (const part of [S, R]) for (const i of (part[core.world.status]?.body.items ?? []) as { kind: string; id: number }[]) known.set(idOf(i), i);
        const items = ((body.nodes ?? []) as { kind: string; id: number }[]).map((n) => known.get(idOf(n)) ?? { ...n, sub: "", state: "deleted", name: "", reason: null, changed: false });
        return json({ items });
      }
      const node = `${u.searchParams.get("kind")}:${u.searchParams.get("id")}`;
      if (route === "GET /lineage/impact") {
        const a = byNode(core.world.impact, node);
        return a ? json(a.body, a.status) : json(S.impact_missing.body, 404);
      }
      if (route === "GET /lineage/origin") {
        const a = byNode(core.world.origin, node);
        return a ? json(a.body, a.status) : json(S.origin_missing.body, 404);
      }
      if (route === "POST /lineage/pin") return json(body.pinned ? S.pin_n1.body : S.unpin_n1.body);
      if (route === "POST /lineage/rebuild") {
        if (body.dry_run) return json(recorded(core.world.plan).body);
        if (core.lost && body.drop_lost !== true) return json(core.lost.body, core.lost.status);
        const a = recorded(core.world.started);
        return json({ ...(a.body as object), job_id: JOB.id, id: JOB.id, source: JOB.source }, a.status);
      }
      if (path === "/jobs") return json({ jobs: [jobRow()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}`) return json(jobRow());
      return json({ code: "not_found", message: "Нет", hint: route }, 404);
    }),
  );
  const jobRow = () => {
    const j = recorded(core.world.job) as unknown as Record<string, unknown>;
    return { source: JOB.source, id: JOB.id, progress: 1, params: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null, ...j };
  };
  return core;
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
  if (!app.registry.get("lineage.status")) app.registry.register(commands);
  clearLineage();
  bindLineage(app);
  return app;
}

export function teardown(): void {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  clearLineage();
  projectStore.set({ current: null, version: 0 });
}

export const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

export async function waitFor(cond: () => boolean, ms = 3000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}

/** Answers the head of the queue of dialogs (the confirm of a command) and returns its message. */
export function answerDialog(ok: boolean): { title?: string; message: string; okLabel?: string } {
  const d = dialogStore.get().queue[0];
  if (!d || d.kind !== "confirm") throw new Error("no confirm in the queue");
  d.resolve(ok);
  dialogStore.set({ queue: dialogStore.get().queue.slice(1) });
  return d.options;
}
