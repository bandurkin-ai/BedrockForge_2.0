import { _electron as electron, expect, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const ART = path.resolve(__dirname, "..", "..", "artifacts");

/** A fresh profile per launch unless `userData` is given (settings survive between launches then). */
export function launchOptions(extraEnv: Record<string, string> = {}, userData?: string) {
  const exe = process.env.BF_APP_EXE; // packaged BedrockForge.exe on Windows CI
  const data = userData ?? fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-"));
  const env = { ...process.env, BF_SOFTWARE_GL: "1", BF_USER_DATA: data, ...extraEnv } as Record<string, string>;
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

export async function close(app: ElectronApplication): Promise<void> {
  await app.close().catch(() => undefined);
}

export async function setWindowSize(app: ElectronApplication, width: number, height: number): Promise<void> {
  await app.evaluate(({ BrowserWindow }, [w, h]) => {
    const win = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().endsWith("/index.html"))!;
    win.unmaximize();
    win.setSize(w, h);
  }, [width, height] as const);
}

export function shot(name: string): string {
  fs.mkdirSync(ART, { recursive: true });
  return path.join(ART, name);
}
