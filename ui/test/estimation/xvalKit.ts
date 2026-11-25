// The stub of the core for the tests of the cross-validation (Э18, 18.23) over the harness of the estimation: the table is the real
// answer of the core (`core/tests/data/estimate/contract_answers.json`: `xval` — the page, `xval_submit`, `xval_result`), grown
// to the 147 rows its summary speaks of (the eight real rows go round; the last row is a sample that was not estimated, as the core
// writes it: gaps are null). The pages are cut as the core cuts them (`page`, `size`), `404 xval_not_found` while there is no table.
import { vi } from "vitest";
import { A, json, type Core } from "./harness";

export const XV = A.xval.body;
export const SUBMIT = A.xval_submit.body as { source: string; id: number };
/** The row of the contract the answers are of. */
export const ROW = A.xval.body.estimation_id as number;
export const TOTAL = XV.total as number;

/** The 147 rows: `i` is the place in the table, the values are the real ones. */
export function table(): Record<string, unknown>[] {
  const real = XV.rows as Record<string, unknown>[];
  const rows: Record<string, unknown>[] = Array.from({ length: TOTAL }, (_, k) => ({ ...real[k % real.length], i: k, row_id: 1000 + k }));
  rows[TOTAL - 1] = { ...rows[TOTAL - 1], hole: "С-99", true: 0.5, est: null, error: null, kv: null, z_error: null, pass: null, ns: 0, status: 2 };
  return rows;
}

export interface XvalCore {
  /** null — no calculation (404 `xval_not_found`) */
  table: Record<string, unknown>[] | null;
  at: string;
  stale: boolean;
  /** the pages are not larger than this (the core would give 5000) */
  clamp: number | null;
  /** the time of the calculation seen by the pages after the first (a calculation made again while the table is read) */
  atLater: string | null;
  /** the next read of a page is refused with this answer */
  fail: { status: number; body: Record<string, unknown> } | null;
  job: { status: string; result: unknown; error: unknown; progress: number; message: string | null };
}

export function installXval(core: Core): XvalCore {
  const kit: XvalCore = { table: table(), at: XV.at, stale: false, clamp: null, atLater: null, fail: null, job: { status: "done", result: A.xval_result.body, error: null, progress: 1, message: null } };
  const base = globalThis.fetch;
  const record = (method: string, u: URL, init?: RequestInit) => core.sent.push({ method, path: u.pathname.replace("/api/v1", ""), query: u.search, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {} });
  const jobOf = () => ({ ...SUBMIT, kind: "estimate.xval", title: "Перекрёстная проверка", queue: "heavy", status: kit.job.status, progress: kit.job.progress, message: kit.job.message, error: kit.job.error, params: null, result: kit.job.result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const page = path.match(/^\/geostat\/estimations\/(\d+)\/xval$/);
      if (method === "GET" && page) {
        record(method, u, init);
        if (kit.fail) {
          const f = kit.fail;
          kit.fail = null;
          return json(f.body, f.status);
        }
        if (!kit.table) return json({ code: "xval_not_found", message: "Перекрёстной проверки для этих параметров нет.", hint: "Выполните перекрёстную проверку." }, 404);
        const n = Number(u.searchParams.get("page"));
        const size = Math.min(Number(u.searchParams.get("size")), kit.clamp ?? 5000);
        if (n > 1 && kit.atLater) {
          kit.at = kit.atLater; // calculated again once: the pages after the first are of the new calculation
          kit.atLater = null;
        }
        return json({ ...XV, estimation_id: Number(page[1]), at: kit.at, stale: kit.stale, total: kit.table.length, page: n, pages: Math.ceil(kit.table.length / size), size, rows: kit.table.slice((n - 1) * size, n * size) });
      }
      if (method === "POST" && path === "/geostat/estimate/xval") {
        record(method, u, init);
        return json(SUBMIT);
      }
      if (path === "/jobs") return json({ jobs: [jobOf()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${SUBMIT.source}/${SUBMIT.id}`) return json(jobOf());
      if (method === "POST" && path === `/jobs/${SUBMIT.source}/${SUBMIT.id}/cancel`) {
        record(method, u, init);
        kit.job.status = "cancelled";
        return json({});
      }
      return base(url, init);
    }),
  );
  return kit;
}
