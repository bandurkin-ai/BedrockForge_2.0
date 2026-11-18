// Э6.1 (ТЗ 7.35 Д-09, ADR 0008): «+» in «Виды» saves the view; after turning the view and hiding the set a click
// on «Вид 1» brings back the camera and the visibility at once; the view survives a restart.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
const near = (a: number[], b: number[]) => a.every((x, i) => Math.abs(x - b[i]) < 1e-6);

test("saved views: save, change, apply, after a restart", async () => {
  test.setTimeout(240_000);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-views-"));
  let { app, page } = await launch({ BF_E2E: "1" }, profile);
  try {
    await setWindowSize(app, 1400, 900);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    // «Виды» is the first section
    await expect(tree.locator("[data-tree-row][aria-level='1']").first()).toHaveAttribute("aria-label", "Виды");

    const saved = await state(page);
    await tree.getByRole("button", { name: "Сохранить текущий вид" }).click();
    const row = tree.getByRole("treeitem", { name: "Вид 1" });
    await expect(row).toBeVisible();

    // another view: from the top, the set hidden
    const setId = saved.sets[0];
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "layer.show", { layer: "set", id: setId, visible: false })).ok).toBe(true);
    await waitFrame(page);
    const changed = await state(page);
    expect(near(changed.camera.position, saved.camera.position)).toBe(false);
    expect(changed.stats.calls).toBeLessThan(saved.stats.calls);

    const t0 = Date.now();
    await row.getByText("Вид 1", { exact: true }).click();
    await expect.poll(async () => near((await state(page)).camera.position, saved.camera.position), { timeout: 10_000 }).toBe(true);
    await waitFrame(page);
    const applied = await state(page);
    console.log(`views.apply до кадра: ${Date.now() - t0} мс (программный рендер)`);
    expect(applied.stats.calls).toBe(saved.stats.calls);
    expect(near(applied.camera.target, saved.camera.target)).toBe(true);
    await page.waitForTimeout(1000);
    await close(app);

    ({ app, page } = await launch({ BF_E2E: "1" }, profile));
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await expect(page.getByRole("tree", { name: "Разделы и объекты проекта" }).getByRole("treeitem", { name: "Вид 1" })).toBeVisible();
  } finally {
    await close(app);
  }
});
