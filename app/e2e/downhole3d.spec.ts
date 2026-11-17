// Э6 acceptance of the layer of displays along holes in 3D (ТЗ 7.16, ADR 0007 §7, §9): the draft of the form
// «AU» is drawn at once (more triangles and draw calls), the legend shows its layer with the intervals in the
// classes and opens the form; after «Сохранить» and closing the form the saved display stays drawn; hiding
// `set:N:intervals` removes it. Waiting is by «кадр готов» of the scene (BF_E2E=1).
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

test("displays along holes in 3D: draft AU drawn at once, legend, saved display, hidden by set:N:intervals", async () => {
  test.setTimeout(240_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1500, 950);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const before = await state(page);
    const setId = before.sets[0];
    expect(before.displays.legend).toEqual([]);

    // the draft of the form: drawn on the holes before «Сохранить»
    expect((await run(page, "holes.show_data", { field: "AU" })).ok).toBe(true);
    const form = page.getByRole("form", { name: /Отображение: Содержания/ });
    await expect(form).toBeVisible();
    const legend = page.getByTestId("downhole-legend");
    await expect(legend).toContainText("AU", { timeout: 60_000 });
    await expect(legend).toContainText("не сохранено");
    await waitFrame(page);
    const drawn = await state(page);
    expect(drawn.stats.triangles).toBeGreaterThan(before.stats.triangles + 100);
    expect(drawn.stats.calls).toBeGreaterThan(before.stats.calls);
    const shown = drawn.displays.legend[0];
    expect(shown.holes).toBeGreaterThan(10);
    expect(shown.intervals).toBeGreaterThan(100);
    await page.screenshot({ path: shot("downhole-3d-draft.png") });

    // «Сохранить»: the display is saved and switched on; closing the form leaves the saved one drawn
    await form.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByRole("dialog", { name: /AU · столбцы/ })).toBeVisible();
    await page.getByRole("dialog", { name: /AU · столбцы/ }).getByRole("button", { name: "Закрыть" }).click();
    await expect(page.getByRole("dialog", { name: /AU · столбцы/ })).toHaveCount(0);
    await expect(legend).toContainText("AU · столбцы");
    await expect(legend).not.toContainText("не сохранено");
    await waitFrame(page);
    const saved = await state(page);
    expect(saved.stats.triangles).toBe(drawn.stats.triangles);

    // a click on the layer in the legend opens the form of the saved display
    await legend.getByRole("button", { name: /AU/ }).first().click();
    await expect(page.getByRole("dialog", { name: /AU · столбцы/ })).toBeVisible();
    await page.getByRole("dialog", { name: /AU · столбцы/ }).getByRole("button", { name: "Закрыть" }).click();

    // set:N:intervals hidden: the display leaves the view
    expect((await run(page, "layer.show", { layer: "intervals", id: setId, visible: false })).ok).toBe(true);
    await expect(legend).toHaveCount(0);
    await waitFrame(page);
    const hidden = await state(page);
    expect(hidden.stats.triangles).toBe(before.stats.triangles);
    expect(hidden.displays.legend).toEqual([]);
    await page.screenshot({ path: shot("downhole-3d-hidden.png") });
  } finally {
    await close(app);
  }
});
