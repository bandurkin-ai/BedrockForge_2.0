// What the tests of the fields of a block model (`blocks-fields-*.test.ts`; Э17 17.36) share: the REAL answers of the core
// (`core/tests/data/blocks/fields_window_answers.json`, made by `core/tests/test_blocks_fields_window_answers.py` through
// the routes of the calculator, the density, the categories, the schemes and the properties of the fields), a stub of the
// core that answers with them (a route that is not stubbed answers 404 — nothing is invented), the app with the commands.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { projectStore } from "../src/modules/projects/state";
import type { ProjectInfo } from "../src/modules/projects/state";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { jobsStore } from "../src/modules/jobs/state";
import { blocksStore } from "../src/modules/blocks/state";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { bindFields } from "../src/modules/blocks/fields/bind";
import { commands } from "../src/modules/blocks/fields/commands";
import { resetFields } from "../src/modules/blocks/fields/state";

export const A: any = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/fields_window_answers.json"), "utf-8"));
export const IDS = A.ids as { model: number; filter: number; wireframe: number };

export const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

/** A JSON with the keys in order: two bodies are the same request when their canon is the same. */
export const canon = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));

/** The body without the keys (the version the interface read is the model's at the moment, the fixture's is the run's). */
export const without = (b: Record<string, unknown>, ...keys: string[]): Record<string, unknown> => Object.fromEntries(Object.entries(b).filter(([k]) => !keys.includes(k)));

export interface Sent {
  method: string;
  path: string;
  /** the query of the address, without «?» («expected_version=3») */
  query: string;
  body: Record<string, unknown>;
}

type Route = (method: string, path: string, body: Record<string, unknown>) => Response | undefined;

export const stub = {
  sent: [] as Sent[],
  /** the version of the model the stub reports (`GET /blocks`) */
  version: 1,
  routes: [] as Route[],
  jobs: new Map<number, Record<string, unknown>>(),
  next: 5,
  /** `GET /blocks/{id}/fields` and `GET /blocks/schemes` answer with what the test puts here */
  fields: null as unknown,
  schemes: null as unknown,
};

export const posts = (re: RegExp) => stub.sent.filter((r) => r.method === "POST" && re.test(r.path));

/** A job of the core as `GET /jobs/{source}/{id}` writes it, made from a recorded case (`job: {status, message, error, result}`). */
export function startJob(c: { job: Record<string, any> }, status?: string): Response {
  const id = stub.next++;
  const st = status ?? c.job.status;
  stub.jobs.set(id, { source: "proj", id, ...c.job, status: st, progress: st === "done" ? 1 : 0.4, params: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null, result: st === "done" ? c.job.result : null });
  return json({ source: "proj", id });
}

/** A recorded case as the stub answers it: the refusal as it was, a job started. */
export const answer = (c: { status: number; body?: unknown; job?: any }): Response => (c.status === 200 && c.job ? startJob(c as { job: Record<string, any> }) : json(c.body, c.status));

export function resetStub(): void {
  stub.sent.length = 0;
  stub.version = 1;
  stub.routes = [];
  stub.jobs.clear();
  stub.next = 5;
  stub.fields = A.fields_before.body;
  stub.schemes = A.schemes.list.body;
}

/** Answers a dialog of the shell (the question in our own window): true — the first button. */
export async function answerDialog(ok: boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBeGreaterThan(0));
  const d = dialogStore.get().queue[0];
  const text = "options" in d ? `${(d.options as { title?: string }).title ?? ""}|${(d.options as { message: string }).message}` : "";
  if (d.kind === "confirm") d.resolve(ok);
  else if (d.kind === "message") d.resolve();
  else d.resolve(null);
  closeDialog(d.id);
  return text;
}

/** The app with the commands of the fields bound, the model and the library as the core listed them, a project open for writing. */
export function install(): App {
  resetStub();
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  windowStore.set({ windows: [] });
  dialogStore.set({ queue: [] });
  resetFields();
  jobsStore.set({ jobs: [], workers: null, error: null });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const href = new URL(url);
      const path = href.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      stub.sent.push({ method, path, query: href.search.replace(/^\?/, ""), body });
      for (const r of stub.routes) {
        const x = r(method, path, body);
        if (x) return x;
      }
      if (method === "GET" && path === "/blocks") return json({ block_models: A.blocks.body.block_models.map((b: any) => ({ ...b, version: stub.version })) });
      const one = /^\/blocks\/(\d+)$/.exec(path);
      if (method === "GET" && one && Number(one[1]) === IDS.model) return json({ ...A.model_before.body, version: stub.version });
      if (method === "GET" && path === "/blocks/schemes") return json(stub.schemes);
      if (method === "GET" && /^\/blocks\/\d+\/fields$/.test(path)) return json(stub.fields);
      if (method === "GET" && path === "/jobs") return json({ jobs: [...stub.jobs.values()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      const job = /^\/jobs\/proj\/(\d+)$/.exec(path);
      if (method === "GET" && job && stub.jobs.has(Number(job[1]))) return json(stub.jobs.get(Number(job[1])));
      const cancel = /^\/jobs\/proj\/(\d+)\/cancel$/.exec(path);
      if (cancel && method === "POST") {
        const j = stub.jobs.get(Number(cancel[1]));
        if (j) stub.jobs.set(Number(cancel[1]), { ...j, status: "cancelled" });
        return json({});
      }
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  const app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  // until the orchestrator names `modules/blocks/fields/commands.ts` in shell/catalog.ts and `bindFields` in shell/app.ts
  for (const c of commands) (app.registry as unknown as { commands: Map<string, unknown> }).commands.delete(c.id);
  app.registry.register(commands);
  bindFields(app);
  blocksStore.set({ list: A.blocks.body.block_models, loaded: true, error: null, details: { [IDS.model]: A.model_before.body }, detailErrors: {} });
  filtersStore.set({ rows: A.filters.body.filters, loaded: true, selected: null, edit: null });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [], loaded: true });
  return app;
}

export function uninstall(): void {
  projectStore.set({ current: null });
  vi.unstubAllGlobals();
  setCoreTarget(null);
}
