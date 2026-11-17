// Э6 acceptance of the 3D window (ТЗ 7.13, ADR 0007 §9) under software WebGL2: the demo project draws a
// frame (pixels read back); T looks straight down keeping the centre and the scale; the head of Z on the
// gizmo turns to the top view; a click on a collar selects the hole and shows its card; a lost context shows
// «Графика перезапускается…» and after the restore the scene is drawn again. Waiting is by «кадр готов».
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

test("3D: frame, T, gizmo Z → top, click on a collar → hole card, lost context → restored", async () => {
  test.setTimeout(240_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const first = await state(page);
    expect(first.sets.length).toBeGreaterThan(0);
    expect(first.segments).toBeGreaterThan(0);
    const pixels = await page.evaluate(() => window.__bfScene!.pixels());
    expect(pixels!.nonBackground).toBeGreaterThan(1000);
    await page.screenshot({ path: shot("scene-demo.png") });

    // T: straight down about the centre of the view, the scale kept (ТЗ 7.13 acceptance)
    await page.keyboard.press("t");
    await waitFrame(page);
    const top = await state(page);
    expect(top.camera.dir[2]).toBeGreaterThan(0.999);
    for (let i = 0; i < 3; i++) expect(top.camera.target[i]).toBeCloseTo(first.camera.target[i], 6);
    expect(top.camera.half).toBeCloseTo(first.camera.half, 6);

    // X: from the side, then the head of Z on the gizmo turns the view smoothly to the top
    await page.keyboard.press("x");
    await waitFrame(page);
    const side = await state(page);
    expect(side.camera.dir[1]).toBeLessThan(-0.999);
    await page.getByTestId("axes-gizmo").locator('[data-axis="z"] circle[data-sign="1"]').click();
    await waitFrame(page);
    const turned = await state(page);
    expect(turned.camera.dir[2]).toBeGreaterThan(0.999);
    expect(turned.camera.half).toBeCloseTo(side.camera.half, 6); // the scale is kept by a turn

    // a click on a collar: the most isolated one on the screen, so the nearest within 8 px is that one
    await page.keyboard.press("f");
    await waitFrame(page);
    const collars = await page.evaluate(() => window.__bfScene!.collars(2000));
    expect(collars.length).toBeGreaterThan(5);
    const iso = collars
      .map((c) => ({ c, d: Math.min(...collars.filter((o) => o !== c).map((o) => Math.hypot(o.x - c.x, o.y - c.y))) }))
      .sort((a, b) => b.d - a.d)[0].c;
    await page.mouse.click(iso.x, iso.y);
    await expect(page.getByRole("contentinfo")).toContainText(`Выбрана: ${iso.name}`);
    await expect(page.getByLabel(`Скважина ${iso.name}`)).toBeVisible({ timeout: 15_000 });
    const picked = await state(page);
    fs.writeFileSync(shot("scene-pick.json"), JSON.stringify({ hole: iso.name, pickMs: picked.pickMs, frames: picked.frames, stats: picked.stats }, null, 2));
    expect(picked.pickMs).toBeLessThan(100);
    await page.screenshot({ path: shot("scene-selected.png") });

    // Esc clears the selection
    await page.keyboard.press("Escape");
    await expect(page.getByRole("contentinfo")).not.toContainText("Выбрана:");

    // a lost context: the banner, then the restore draws the scene again from the core
    await page.evaluate(() => {
      const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="scene-host"] canvas')!;
      window.__bfLose = canvas.getContext("webgl2")!.getExtension("WEBGL_lose_context");
      window.__bfLose!.loseContext();
    });
    await expect(page.getByTestId("scene-lost")).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__bfScene!.state().lost)).toBe(true);
    await page.evaluate(() => window.__bfLose!.restoreContext());
    await expect(page.getByTestId("scene-lost")).toBeHidden({ timeout: 30_000 });
    await waitFrame(page);
    const again = await state(page);
    expect(again.frames).toBeGreaterThan(turned.frames);
    expect(again.sets).toEqual(first.sets);
    expect(again.segments).toBe(first.segments);
    const after = await page.evaluate(() => window.__bfScene!.pixels());
    expect(after!.nonBackground).toBeGreaterThan(1000);
    await page.screenshot({ path: shot("scene-restored.png") });
  } finally {
    await close(app);
  }
});
