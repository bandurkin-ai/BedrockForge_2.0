// Э6.1 (ТЗ 7.35 Д-04, ADR 0008): «Очистить окно» hides every object (any number: key hide_all), the camera
// stays; after a restart the window is still empty; switching one set on shows only it.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

test("«Очистить окно»: nothing drawn but grid, the same after a restart, one set back", async () => {
  test.setTimeout(240_000);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-clear-"));
  let { app, page } = await launch({ BF_E2E: "1" }, profile);
  try {
    await setWindowSize(app, 1400, 900);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const before = await state(page);
    const setId = before.sets[0];
    expect(before.stats.triangles + before.stats.points + before.stats.lines).toBeGreaterThan(0);

    // grid off: an empty frame is then only the gizmo
    expect((await run(page, "scene.style", { grid: false })).ok).toBe(true);
    await waitFrame(page);
    const drawn = await state(page);
    const r = await run(page, "scene.clear");
    expect(r.ok).toBe(true);
    await waitFrame(page);
    const cleared = await state(page);
    expect(cleared.stats.calls).toBeLessThan(drawn.stats.calls);
    expect(cleared.camera.position).toEqual(drawn.camera.position);
    await page.waitForTimeout(1500); // the view keys are written 400 ms after the last change
    await close(app);

    ({ app, page } = await launch({ BF_E2E: "1" }, profile));
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    expect((await state(page)).stats.calls).toBe(cleared.stats.calls);

    // the check box of the set in the tree shows it again (review Э6.1)
    const box = page.getByRole("checkbox", { name: /^Показать «.+» в 3D$/ }).first();
    await expect(box).not.toBeChecked();
    await box.check();
    expect(setId).toBeGreaterThan(0);
    await waitFrame(page);
    expect((await state(page)).stats.calls).toBeGreaterThan(cleared.stats.calls);
  } finally {
    await close(app);
  }
});
