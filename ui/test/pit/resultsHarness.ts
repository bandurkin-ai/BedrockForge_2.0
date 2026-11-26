// The stub of the core for the results of the pit optimisation (Э21, 21.19): it stands over the stub of `harness.ts` and answers the three routes of the results —
// `POST /pit/scenarios/{id}/surfaces`, `POST /pit/scenarios/{id}/solid` (the jobs `pit.surfaces`, `pit.solid`) and `POST /pit/compare` — with the answers of
// `core/tests/data/pit/contract_answers.json` (`surfaces_result`, `solid_result`, `compare_result`); the jobs are in the list of `/jobs` beside those of the first stub.
import { vi } from "vitest";
import { answer, json, ID, type Answer, type Core } from "./harness";

export const FIRST_JOB = 5;

export interface JobAnswer {
  status: string;
  result: unknown;
  error: unknown;
}

export interface ResultsCore {
  sent: { method: string; path: string; body: Record<string, unknown> }[];
  jobs: { surfaces: JobAnswer; solid: JobAnswer };
  /** the answer of `POST /pit/compare` */
  compare: Answer;
  /** the next request of a route (`POST /pit/compare`) is answered with this refusal */
  force: (route: string, a: Answer) => void;
}

const row = (id: { source: string; id: number }, kind: string, j: JobAnswer) => ({
  ...id,
  kind,
  title: kind,
  queue: "quick",
  status: j.status,
  progress: j.status === "done" ? 1 : 0.4,
  message: j.status === "done" ? "Готово" : "Оболочки 1/3",
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

/** Call it after `installCore()`; `core` is the first stub (the scenario, the validation). */
export function installResults(_core: Core): ResultsCore {
  const inner = globalThis.fetch;
  const forced = new Map<string, Answer>();
  const sent: ResultsCore["sent"] = [];
  // every start is a job of its own (a new number, the answer of the moment): a list that kept an old «done» would answer for the next start
  const started = new Map<number, { kind: string; ans: JobAnswer }>();
  let next = FIRST_JOB;
  const start = (kind: "surfaces" | "solid") => {
    const id = next++;
    started.set(id, { kind: `pit.${kind}`, ans: { ...r.jobs[kind] } });
    return { source: "project", id };
  };
  const r: ResultsCore = {
    sent,
    jobs: { surfaces: { status: "done", result: answer("surfaces_result").body, error: null }, solid: { status: "done", result: answer("solid_result").body, error: null } },
    compare: answer("compare_result"),
    force: (route, a) => void forced.set(route, a),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      const route = `${method} ${path}`;
      const mine = path.startsWith("/pit/scenarios/") && /\/(surfaces|solid)$/.test(path) ? true : route === "POST /pit/compare";
      if (mine) sent.push({ method, path, body });
      const f = forced.get(route);
      if (mine && f) {
        forced.delete(route);
        return json(f.body, f.status);
      }
      if (route === `POST /pit/scenarios/${ID.scenario}/surfaces`) return json(start("surfaces"));
      if (route === `POST /pit/scenarios/${ID.scenario}/solid`) return json(start("solid"));
      if (route === "POST /pit/compare") return json(r.compare.body, r.compare.status);
      const one = /^\/jobs\/project\/(\d+)$/.exec(path);
      const job = one ? started.get(Number(one[1])) : undefined;
      if (one && job) return json(row({ source: "project", id: Number(one[1]) }, job.kind, job.ans));
      if (path === "/jobs") {
        const res = await inner(url, init);
        const list = (await res.json()) as { jobs: unknown[] };
        return json({ ...list, jobs: [...list.jobs, ...[...started].map(([id, j]) => row({ source: "project", id }, j.kind, j.ans))] });
      }
      return inner(url, init);
    }),
  );
  return r;
}
