// Smoke E2E (spike Э0-в, scene of Э6): launch the app, core answers with token and 401 without, the 3D window
// of the demo project draws under software WebGL; a frame is saved as an artifact.
import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { ART, close, launch, launchOptions, waitFrame } from "./helpers";

test("window opens, core answers with token, 401 without", async () => {
  const t0 = Date.now();
  const app = await electron.launch(launchOptions({}));
  try {
    const win = await app.firstWindow();
    // The first window is the splash; wait for the main window.
    await expect.poll(async () => app.windows().some((w) => w.url().endsWith("/index.html")), { timeout: 90_000 }).toBe(true);
    const page = app.windows().find((w) => w.url().endsWith("/index.html")) ?? win;
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    const startupMs = Date.now() - t0; // ТЗ 5.3: ≤ 6 s cold, ≤ 3 s warm on the acceptance PC
    fs.mkdirSync(ART, { recursive: true });
    fs.writeFileSync(path.join(ART, "startup.json"), JSON.stringify({ startupMs, platform: process.platform, packaged: Boolean(process.env.BF_APP_EXE) }));
    const codes = await page.evaluate(async () => {
      const core = window.bf!.core()!;
      const ok = await fetch(`${core.baseUrl}/api/v1/health`, { headers: { "X-BF-Token": core.token } });
      const no = await fetch(`${core.baseUrl}/api/v1/health`);
      return { ok: ok.status, no: no.status, title: document.title };
    });
    expect(codes).toEqual({ ok: 200, no: 401, title: "BedrockForge by Mikhail Bandurkin" });
    fs.mkdirSync(ART, { recursive: true });
    await page.screenshot({ path: path.join(ART, "smoke-window.png") });
  } finally {
    await close(app);
  }
});

test("the 3D window draws a frame under software WebGL2", async () => {
  // Replaces the spike Э0-б: the scene of the demo project is a lazy chunk on WebGL2 (ADR 0004).
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const state = await page.evaluate(() => window.__bfScene!.state());
    const pixels = await page.evaluate(() => window.__bfScene!.pixels());
    fs.mkdirSync(ART, { recursive: true });
    fs.writeFileSync(path.join(ART, "scene-frame.json"), JSON.stringify({ gpu: state.gpu, frames: state.frames, stats: state.stats, pixels }, null, 2));
    await page.screenshot({ path: path.join(ART, "scene-frame.png") });
    expect(state.gpu.renderer).not.toBe("");
    expect(pixels?.nonBackground ?? 0).toBeGreaterThan(100);
  } finally {
    await close(app);
  }
});
