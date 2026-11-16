// Core lifecycle (ТЗ 6.4, 7.0.2, 7.0.14; ADR 0002): restart after a crash on the same port, the window keeps working;
// no core processes are left after the app exits.
import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import { close, launch } from "./helpers";

function corePids(): number[] {
  try {
    const out =
      process.platform === "win32"
        ? execSync(`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name='bf-core.exe'\\" | Select-Object -ExpandProperty ProcessId"`, { encoding: "utf8" })
        : execSync("pgrep -f '[-]m bf_core --port|[b]f-core --port' || true", { encoding: "utf8" }); // [x] keeps pgrep from matching its own shell
    return [...out.matchAll(/(\d+)/g)].map((m) => Number(m[1]));
  } catch {
    return [];
  }
}

test("the core is restarted after a crash on the same port; nothing is left after exit", async () => {
  const before = new Set(corePids());
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    const first = await page.evaluate(() => window.bf!.core()!);
    // Simulated crash: the core stops without the shell asking for it.
    await page.evaluate(async (c) => {
      await fetch(`${c.baseUrl}/api/v1/shutdown`, { method: "POST", headers: { "X-BF-Token": c.token } });
    }, first);
    await expect(page.getByRole("dialog", { name: "Ядро" })).toContainText("Вычислительное ядро перезапущено", { timeout: 60_000 });
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 30_000 });
    const status = await page.evaluate(async (c) => (await fetch(`${c.baseUrl}/api/v1/health`, { headers: { "X-BF-Token": c.token } })).status, first);
    expect(status).toBe(200);
    expect((await page.evaluate(() => window.bf!.core()!)).baseUrl).toBe(first.baseUrl);
  } finally {
    await close(app);
  }
  await expect.poll(() => corePids().filter((p) => !before.has(p)), { timeout: 20_000 }).toEqual([]);
});
