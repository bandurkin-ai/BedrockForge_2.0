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
