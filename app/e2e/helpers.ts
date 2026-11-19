import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const ART = path.resolve(__dirname, "..", "..", "artifacts");

/** A fresh profile per launch unless `userData` is given (settings survive between launches then). */
export function launchOptions(extraEnv: Record<string, string> = {}, userData?: string) {
  const exe = process.env.BF_APP_EXE; // packaged BedrockForge.exe on Windows CI
  const data = userData ?? fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-"));
  // Projects and the instance cache live next to the profile of the test (a fresh folder per launch unless shared).
  const env = {
    ...process.env,
    BF_SOFTWARE_GL: "1",
    BF_USER_DATA: data,
    BF_PROJECTS_DIR: path.join(data, "Проекты"),
    ...(process.env.BF_APP_EXE ? {} : { BF_LOCAL_DIR: path.join(data, "local") }),
    ...extraEnv,
  } as Record<string, string>;
  const args = process.platform === "linux" ? ["--no-sandbox"] : [];
  return exe ? { executablePath: exe, args, env } : { args: [path.resolve(__dirname, ".."), ...args], env };
}

export async function mainWindow(app: ElectronApplication): Promise<Page> {
  await expect.poll(async () => app.windows().some((w) => w.url().endsWith("/index.html")), { timeout: 90_000 }).toBe(true);
  return app.windows().find((w) => w.url().endsWith("/index.html"))!;
}

export async function launch(extraEnv: Record<string, string> = {}, userData?: string): Promise<{ app: ElectronApplication; page: Page }> {
  const app = await electron.launch(launchOptions(extraEnv, userData));
  const page = await mainWindow(app);
  return { app, page };
}

let closed = 0;
/** Closes the app; the last state is kept as artifacts/end-*.png (a failing assertion is still in flight here). */
export async function close(app: ElectronApplication): Promise<void> {
  try {
    const info = test.info();
    const page = app.windows().find((w) => w.url().endsWith("/index.html"));
    if (page) {
      const slug = info.title.replace(/[^\p{L}\p{N}]+/gu, "-").slice(0, 50);
      await page.screenshot({ path: shot(`end-${++closed}-${slug}.png`), timeout: 5000 });
    }
  } catch {
    /* diagnostics never fail a test */
  }
  await app.close().catch(() => undefined);
}

export async function setWindowSize(app: ElectronApplication, width: number, height: number): Promise<void> {
  await app.evaluate(({ BrowserWindow }, [w, h]) => {
    const win = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().endsWith("/index.html"))!;
    win.unmaximize();
    win.setContentSize(w, h); // the page size: the Windows frame is not counted
  }, [width, height] as const);
}

export function shot(name: string): string {
  fs.mkdirSync(ART, { recursive: true });
  return path.join(ART, name);
}

/** Waits for «кадр готов» of the 3D window (BF_E2E=1): everything loaded and the last change drawn. */
export async function waitFrame(page: Page, timeout = 90_000): Promise<void> {
  // Not waitForFunction: under the strict CSP of app:// it needs eval.
  await expect.poll(() => page.evaluate(() => (window.__bfScene ? window.__bfScene.ready() : false)), { timeout, intervals: [100, 200, 400] }).toBe(true);
}

/**
 * The demo project as the specs written before its Э7 parts need it: the holes of «Скважины 2025» (with the
 * trench «К-01») and nothing else. The lines, the survey points, the plan and the DTM of the demo (core
 * `demo/geo.py`) are deleted through the core — the interface follows the events of the core — so such a spec
 * counts, hides and clicks only what it loads itself.
 */
export async function holesOnlyDemo(page: Page): Promise<void> {
  const failed = await page.evaluate(async () => {
    const c = window.bf!.core()!;
    const call = async (method: string, url: string) => {
      const r = await fetch(`${c.baseUrl}/api/v1${url}`, { method, headers: { "X-BF-Token": c.token } });
      return { status: r.status, body: (await r.json()) as Record<string, { id: number }[]> };
    };
    const out: string[] = [];
    // the DTM goes first: a raster that lies on a DTM is not deleted
    for (const [url, key] of [["/wireframes", "wireframes"], ["/rasters", "rasters"], ["/lines/files", "files"]]) {
      for (const o of (await call("GET", url)).body[key]) {
        const r = await call("DELETE", `${url}/${o.id}`);
        if (r.status !== 200) out.push(`DELETE ${url}/${o.id}: ${r.status} ${JSON.stringify(r.body)}`);
      }
    }
    return out;
  });
  expect(failed).toEqual([]);
  // with the hook of the 3D window (BF_E2E=1): nothing of them is left in the scene
  const left = () =>
    page.evaluate(() => {
      const s = window.__bfScene?.state();
      return s ? s.lines.layers + s.rasters.count + s.wireframes.layers : 0;
    });
  await expect.poll(left, { timeout: 60_000, intervals: [100, 200, 400] }).toBe(0);
}

/** A JSON call of the core straight from the page (the token of the session); `body` — JSON. */
export async function coreApi<T = Record<string, unknown>>(page: Page, method: string, url: string, body?: unknown): Promise<{ status: number; body: T }> {
  return page.evaluate(
    async ([m, u, b]) => {
      const c = window.bf!.core()!;
      const headers: Record<string, string> = { "X-BF-Token": c.token };
      if (b !== undefined) headers["Content-Type"] = "application/json";
      const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m, headers, body: b === undefined ? undefined : JSON.stringify(b) });
      return { status: r.status, body: (await r.json()) as never };
    },
    [method, url, body] as const,
  );
}

/** A line of a save: its vertices; a closed one is sent in the storage form (its first vertex repeated). */
export interface SavedLine {
  points: number[][];
  closed?: boolean;
}

/**
 * A save of a whole layer of lines straight through the core (`PUT /lines/layers/{id}/geometry`, the BFA1 body of
 * ui/src/edit/bfa1.ts): every line is new (the lines of `baseVersion` not listed are deleted). → the new version.
 */
export async function saveLines(page: Page, layerId: number, baseVersion: number, lines: SavedLine[], note = "e2e"): Promise<number> {
  const rows = lines.map((l) => (l.closed ? [...l.points, l.points[0]] : l.points));
  const r = await page.evaluate(
    async ([id, base, text, ls, shut]) => {
      const arrays: [string, string, ArrayBufferView][] = [
        ["src", "<i4", new Int32Array(ls.map(() => -1))],
        ["touched", "<u1", new Uint8Array(ls.map(() => 1))],
        ["count", "<u4", new Uint32Array(ls.map((p) => p.length))],
        ["closed", "<u1", new Uint8Array(shut.map((s) => (s ? 1 : 0)))],
        ["xyz", "<f8", new Float64Array(ls.flat(2))],
      ];
      // "BFA1", u32 header length, JSON, arrays at multiples of 8 (the offsets are in the header)
      const a8 = (n: number) => Math.ceil(n / 8) * 8;
      let start = 0;
      let body: Uint8Array | null = null;
      while (!body) {
        let at = start;
        const table = arrays.map(([name, dtype, data]) => {
          const row = [name, dtype, (data as unknown as { length: number }).length, at];
          at = a8(at + data.byteLength);
          return row;
        });
        const head = new TextEncoder().encode(JSON.stringify({ base_version: base, note: text, new_attrs: {}, arrays: table }));
        const need = a8(8 + head.length);
        if (need > start) {
          start = need;
          continue;
        }
        body = new Uint8Array(Math.max(at, need));
        body.set([0x42, 0x46, 0x41, 0x31]);
        new DataView(body.buffer).setUint32(4, head.length, true);
        body.set(head, 8);
        arrays.forEach(([, , data], k) => body!.set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), table[k][3] as number));
      }
      const c = window.bf!.core()!;
      const res = await fetch(`${c.baseUrl}/api/v1/lines/layers/${id}/geometry`, { method: "PUT", headers: { "X-BF-Token": c.token, "Content-Type": "application/octet-stream" }, body });
      return { status: res.status, body: (await res.json()) as { version?: number } };
    },
    [layerId, baseVersion, note, rows, lines.map((l) => !!l.closed)] as const,
  );
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  return r.body.version!;
}

export type CoreJob = { source: string; id: number; kind: string; status: string; progress?: number; result?: Record<string, unknown> | null; error?: { code: string; message: string } | null };

/** Waits until the job reaches one of `statuses` (by default — its end) and returns it. */
export async function waitJob(page: Page, source: string, id: number, statuses = ["done", "failed", "cancelled"], timeout = 120_000): Promise<CoreJob> {
  let last: CoreJob | null = null;
  await expect
    .poll(
      async () => {
        last = (await coreApi<CoreJob>(page, "GET", `/jobs/${source}/${id}`)).body;
        return statuses.includes(last.status);
      },
      { timeout, intervals: [200, 400, 800] },
    )
    .toBe(true);
  return last!;
}
