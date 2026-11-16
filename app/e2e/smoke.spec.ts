// Smoke E2E (spikes Э0-б, Э0-в): launch the app, core answers with token and 401 without,
// 1M triangles render under software WebGL; a frame is saved as an artifact.
import { _electron as electron, expect, test, type ElectronApplication } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const ART = path.resolve(__dirname, "..", "..", "artifacts");

function launchOptions(extraEnv: Record<string, string>) {
  const exe = process.env.BF_APP_EXE; // packaged BedrockForge.exe on Windows CI
  const env = { ...process.env, BF_SOFTWARE_GL: "1", ...extraEnv } as Record<string, string>;
  const args = process.platform === "linux" ? ["--no-sandbox"] : [];
  return exe ? { executablePath: exe, args, env } : { args: [path.resolve(__dirname, ".."), ...args], env };
}

async function close(app: ElectronApplication): Promise<void> {
  await app.close().catch(() => undefined);
}

test("window opens, core answers with token, 401 without", async () => {
  const app = await electron.launch(launchOptions({}));
  try {
    const win = await app.firstWindow();
    // The first window is the splash; wait for the main window.
    await expect.poll(async () => app.windows().some((w) => w.url().endsWith("/index.html")), { timeout: 90_000 }).toBe(true);
    const page = app.windows().find((w) => w.url().endsWith("/index.html")) ?? win;
    await expect(page.getByTestId("core-status")).toContainText("работает", { timeout: 60_000 });
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

test("1M triangles under software WebGL2", async () => {
  const app = await electron.launch(launchOptions({ BF_SPIKE: "mesh" }));
  try {
    await expect.poll(async () => app.windows().some((w) => w.url().endsWith("/index.html")), { timeout: 90_000 }).toBe(true);
    const page = app.windows().find((w) => w.url().endsWith("/index.html"))!;
    await page.waitForFunction(() => window.__bfSpike !== undefined, undefined, { timeout: 120_000 });
    const result = await page.evaluate(() => window.__bfSpike);
    fs.mkdirSync(ART, { recursive: true });
    fs.writeFileSync(path.join(ART, "spike-mesh.json"), JSON.stringify(result, null, 2));
    await page.screenshot({ path: path.join(ART, "spike-mesh-1m.png") });
    expect(result && "triangles" in result ? result.triangles : result).toBe(1_000_000);
    expect(result && "webgl2" in result ? result.webgl2 : false).toBe(true);
  } finally {
    await close(app);
  }
});
