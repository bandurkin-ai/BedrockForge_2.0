import { _electron as electron, expect, test, type ElectronApplication, type Locator, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";

export const ART = path.resolve(__dirname, "..", "..", "artifacts");

/**
 * The numba cache of the launches without the .exe: one for every launch, as the packaged app keeps it in the
 * shared %LOCALAPPDATA% (the kernels compile once, not in every spec). NUMBA_CACHE_DIR of the run — another folder
 * (an empty one: the cold cache of a first start on Windows).
 */
const SHARED_NUMBA = process.env.NUMBA_CACHE_DIR ?? path.join(os.tmpdir(), "bf-e2e-numba");

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
    ...(process.env.BF_APP_EXE ? {} : { BF_LOCAL_DIR: path.join(data, "local"), NUMBA_CACHE_DIR: SHARED_NUMBA }),
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

/** A new empty layer of lines through the core (`POST /lines/layers`) → its id and version. */
export async function newLineLayer(page: Page, name: string): Promise<{ id: number; version: number }> {
  const made = await coreApi<{ id: number; version: number }>(page, "POST", "/lines/layers", { name });
  expect(made.status, JSON.stringify(made.body)).toBe(201);
  return { id: made.body.id, version: made.body.version };
}

/** A grid of open lines: `lines` rows of `vertices` points `step` m apart from (x0, y0), a gentle relief in Z. */
export interface GridLines {
  lines: number;
  vertices: number;
  x0: number;
  y0: number;
  step: number;
}

/**
 * A whole layer of `grid.lines × grid.vertices` vertices written through the core like `saveLines`, but made in the
 * page: a million vertices are not sent through the protocol of Playwright. → the new version.
 */
export async function saveGridLines(page: Page, layerId: number, baseVersion: number, grid: GridLines, note = "e2e"): Promise<number> {
  const r = await page.evaluate(
    async ([id, base, text, g]) => {
      const n = g.lines;
      const m = g.vertices;
      const xyz = new Float64Array(n * m * 3);
      for (let i = 0, k = 0; i < n; i++) {
        for (let j = 0; j < m; j++) {
          xyz[k++] = g.x0 + j * g.step;
          xyz[k++] = g.y0 + i * g.step + 0.25 * Math.sin(j / 7 + i);
          xyz[k++] = 300 + 10 * Math.sin(j / 50) + 5 * Math.cos(i / 40);
        }
      }
      const arrays: [string, string, ArrayBufferView][] = [
        ["src", "<i4", new Int32Array(n).fill(-1)],
        ["touched", "<u1", new Uint8Array(n).fill(1)],
        ["count", "<u4", new Uint32Array(n).fill(m)],
        ["closed", "<u1", new Uint8Array(n)],
        ["xyz", "<f8", xyz],
      ];
      // the same layout as `saveLines`: "BFA1", u32 header length, JSON, arrays at multiples of 8
      const a8 = (v: number) => Math.ceil(v / 8) * 8;
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
    [layerId, baseVersion, note, grid] as const,
  );
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  return r.body.version!;
}

/** Runs a command of the registry as a click would (the hook of the 3D window, BF_E2E=1; waits for the hook). */
export async function runCommand(page: Page, id: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; result: string }> {
  await expect.poll(() => page.evaluate(() => typeof window.__bfScene?.run === "function"), { timeout: 60_000, intervals: [100, 200, 400] }).toBe(true);
  return page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
}

/** The next system «Открыть» window (files or a folder) answers `paths` at once: no system dialog in a test. */
export async function answerOpenDialog(app: ElectronApplication, paths: string[]): Promise<void> {
  await app.evaluate(({ dialog }, f) => {
    dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: f })) as typeof dialog.showOpenDialog;
  }, paths);
}

/** The next system «Сохранить как» window answers `file`. */
export async function answerSaveDialog(app: ElectronApplication, file: string): Promise<void> {
  await app.evaluate(({ dialog }, f) => {
    dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
  }, file);
}

/** «Показать в папке» stops opening Explorer: the paths asked for are kept, `shownInFolder` reads them. */
export async function stubShowInFolder(app: ElectronApplication): Promise<void> {
  await app.evaluate(({ shell }) => {
    const g = globalThis as unknown as { __bfShown: string[] };
    g.__bfShown = [];
    shell.showItemInFolder = (p: string) => void g.__bfShown.push(p);
  });
}

export async function shownInFolder(app: ElectronApplication): Promise<string[]> {
  return app.evaluate(() => (globalThis as unknown as { __bfShown?: string[] }).__bfShown ?? []);
}

/** The demo project open (as most specs start), its first frame drawn; `folded` — the ribbon folded (a window of 1024 × 640). */
export async function openDemo(page: Page, folded = true): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
  await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
  if (folded) expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  await waitFrame(page);
}

/** A regular block model for `writeOmfBlocks`: the corner, the cell size and counts, number fields of every cell (NaN — empty). */
export interface OmfBlocks {
  name: string;
  corner: [number, number, number];
  size: [number, number, number];
  counts: [number, number, number];
  /** values in the order of OMF (Fortran: x fastest, then y, then z) */
  fields: { name: string; values: number[] }[];
}

/**
 * A small OMF v1 file of one block model (`VolumeElement`, axes +X +Y +Z), written as `formats/omf_write.py` does:
 * the zlib blocks, the JSON, its offset in the header of 60 bytes. For the import through the core without the
 * Python of the core (the .exe on CI has none). → the path.
 */
export function writeOmfBlocks(file: string, m: OmfBlocks): string {
  const now = new Date().toISOString();
  const objects: Record<string, Record<string, unknown>> = {};
  const add = (cls: string, fields: Record<string, unknown>) => {
    const uid = randomUUID();
    objects[uid] = { date_created: now, date_modified: now, ...fields, __class__: cls };
    return uid;
  };
  const blocks: Buffer[] = [];
  let at = 60;
  const block = (values: number[]) => {
    const raw = Buffer.from(new Float64Array(values).buffer);
    const z = zlib.deflateSync(raw, { level: 3 });
    blocks.push(z);
    const out = { start: at, dtype: "<f8", length: z.length };
    at += z.length;
    return out;
  };
  const n = m.counts[0] * m.counts[1] * m.counts[2];
  const data = m.fields.map((f) => {
    expect(f.values.length, `the field ${f.name}`).toBe(n);
    return add("ScalarData", { name: f.name, description: "", location: "cells", array: add("ScalarArray", { array: block(f.values) }) });
  });
  const tensor = (k: number) => Array.from({ length: m.counts[k] }, () => m.size[k]);
  const geometry = add("VolumeGridGeometry", { origin: m.corner, tensor_u: tensor(0), tensor_v: tensor(1), tensor_w: tensor(2), axis_u: [1, 0, 0], axis_v: [0, 1, 0], axis_w: [0, 0, 1] });
  const element = add("VolumeElement", { name: m.name, description: "", data, color: [128, 128, 128], subtype: "volume", geometry });
  const project = randomUUID();
  objects[project] = { date_created: now, date_modified: now, name: m.name, description: "", author: "", revision: "", date: now, units: "m", elements: [element], origin: [0, 0, 0], __class__: "Project" };
  const head = Buffer.alloc(60);
  Buffer.from([0x84, 0x83, 0x82, 0x81]).copy(head, 0);
  Buffer.from("OMF-v0.9.0", "ascii").copy(head, 4);
  Buffer.from(project.replace(/-/g, ""), "hex").copy(head, 36);
  head.writeBigUInt64LE(BigInt(at), 52);
  fs.writeFileSync(file, Buffer.concat([head, ...blocks, Buffer.from(JSON.stringify(objects), "utf8")]));
  return file;
}

/** Files into the project through the core (`POST /import/objects`, the job of «Импорт файлов») → the job, done. */
export async function importFiles(page: Page, files: string[], timeout = 120_000): Promise<CoreJob> {
  const r = await coreApi<{ source: string; id: number }>(page, "POST", "/import/objects", { files });
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  const job = await waitJob(page, r.body.source, r.body.id, undefined, timeout);
  expect(job.status, JSON.stringify(job.error)).toBe("done");
  return job;
}

/** A free point of the 3D window: the canvas is the topmost element there (no window, panel or label over it). */
export async function onCanvas(page: Page, x: number, y: number): Promise<boolean> {
  return page.evaluate(([px, py]) => document.elementFromPoint(px, py)?.tagName === "CANVAS", [x, y] as const);
}

/** A drag of the title bar of a window by (dx, dy), as a hand does it. */
export async function moveBy(page: Page, win: Locator, dx: number, dy: number): Promise<void> {
  const b = (await win.locator(":scope > div").first().boundingBox())!;
  await page.mouse.move(b.x + 40, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + 40 + dx, b.y + b.height / 2 + dy, { steps: 4 });
  await page.mouse.up();
}

/** The window `width` px wide at the right edge of the page (the 3D or the tree beside it): its grip, then its bar. */
export async function placeRight(page: Page, win: Locator, width: number): Promise<void> {
  const grip = win.locator(":scope > div").last();
  const g = (await grip.boundingBox())!;
  const w0 = (await win.boundingBox())!.width;
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
  await page.mouse.down();
  await page.mouse.move(g.x + g.width / 2 - (w0 - width), g.y + g.height / 2, { steps: 4 });
  await page.mouse.up();
  const vw = await page.evaluate(() => window.innerWidth);
  await moveBy(page, win, vw - width - (await win.boundingBox())!.x, 0);
  const now = (await win.boundingBox())!;
  expect(Math.round(now.width)).toBe(width);
  expect(now.x + now.width).toBeGreaterThan(vw - 4);
}
