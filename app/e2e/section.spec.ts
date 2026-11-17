// Э6 acceptance of sections (ТЗ 7.14, ADR 0007 §4, §9): S and two clicks draw a line with an orange rubber
// band; after the second click clipping is on and the view looks at the plane (Z up, from the viewer's side);
// `]` moves the line across itself by exactly «от + до»; the line and the clipping are kept in the project
// view settings. Waiting is by «кадр готов» of the scene, not by time.
import { expect, test, type Page } from "@playwright/test";
import { close, launch, shot, waitFrame } from "./helpers";

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

/** The section key as the core keeps it. */
const stored = (page: Page) =>
  page.evaluate(async () => {
    const c = window.bf!.core()!;
    const r = await fetch(`${c.baseUrl}/api/v1/view`, { headers: { "X-BF-Token": c.token } });
    const v = (await r.json()).values.section?.value ?? null;
    return v as { line: { x0: number; y0: number; az: number; length: number | null; towards: number; away: number } | null; clip: boolean } | null;
  });

test("section: S + two clicks → clipping and the plane view; ] moves exactly towards + away; kept in the project", async () => {
  test.setTimeout(240_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    await page.keyboard.press("t");
    await waitFrame(page);
    const before = await page.evaluate(() => window.__bfScene!.pixels());

    const box = (await page.getByTestId("scene-host").boundingBox())!;
    await page.keyboard.press("s");
    await expect(page.getByText("Укажите начало профиля")).toBeVisible();
    await page.mouse.click(box.x + box.width * 0.25, box.y + box.height * 0.55);
    await expect(page.getByText("Укажите конец профиля")).toBeVisible();
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5, { steps: 4 }); // the rubber band
    await waitFrame(page);
    await page.screenshot({ path: shot("section-rubber.png") });
    await page.mouse.click(box.x + box.width * 0.75, box.y + box.height * 0.45);
    await waitFrame(page);

    const s = await state(page);
    expect(s.section).not.toBeNull();
    expect(s.section!.length).toBeNull();
    expect(s.clipPlanes).toBe(2); // clipping on: the window «от / до»
    const az = (s.section!.az * Math.PI) / 180;
    const normal = [Math.cos(az), -Math.sin(az)];
    // in the plane: horizontal view from the viewer's side, Z up
    expect(Math.abs(s.camera.dir[2])).toBeLessThan(1e-9);
    expect(s.camera.dir[0] * normal[0] + s.camera.dir[1] * normal[1]).toBeGreaterThan(0.999999);
    expect(s.camera.up).toEqual([0, 0, 1]);
    const clipped = await page.evaluate(() => window.__bfScene!.pixels());
    expect(clipped!.nonBackground).toBeGreaterThan(0);
    expect(clipped!.nonBackground).not.toBe(before!.nonBackground);
    await page.screenshot({ path: shot("section-clipped.png") });

    // kept in the project (written 400 ms after the change)
    await expect.poll(async () => (await stored(page))?.line ?? null, { timeout: 15_000 }).toEqual(s.section);
    expect((await stored(page))?.clip).toBe(true);

    // ]: the next profile, exactly one window (towards + away) away from the viewer
    const line0 = s.section!;
    await page.keyboard.press("]");
    await expect.poll(async () => (await state(page)).section!.x0).not.toBe(line0.x0);
    await waitFrame(page);
    const line1 = (await state(page)).section!;
    const step = line0.towards + line0.away;
    const dx = line1.x0 - line0.x0;
    const dy = line1.y0 - line0.y0;
    expect(Math.abs(dx * normal[0] + dy * normal[1] + step)).toBeLessThan(1e-9); // −(от + до) along the normal
    expect(Math.abs(dx * Math.sin(az) + dy * Math.cos(az))).toBeLessThan(1e-9); // not along the line
    expect(line1.az).toBe(line0.az);
    await expect.poll(async () => (await stored(page))?.line ?? null, { timeout: 15_000 }).toEqual(line1);

    // C turns clipping off without turning the view; the section stays in the project
    const dir = (await state(page)).camera.dir;
    await page.keyboard.press("c");
    await waitFrame(page);
    const off = await state(page);
    expect(off.clipPlanes).toBe(0);
    for (let i = 0; i < 3; i++) expect(off.camera.dir[i]).toBeCloseTo(dir[i], 9);
    await expect.poll(async () => (await stored(page))?.clip, { timeout: 15_000 }).toBe(false);
  } finally {
    await close(app);
  }
});
