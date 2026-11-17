// Э6.1 (ТЗ 7.35 Д-11, ADR 0008): the buttons of the 3D panel are 1.5 × larger (42×39, icons 24 px); the
// number fields keep their size; at 1024 px the panel fits the window in at most two rows.
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, shot, waitFrame } from "./helpers";

const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

test("3D panel: buttons 42×39, icons 24, fields as before, ≤ 2 rows at 1024 px", async () => {
  test.setTimeout(180_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 768);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);
    const c = (await page.evaluate(() => window.__bfScene!.collars(1)))[0];
    expect((await run(page, "section.set", { x0: c.x, y0: c.y, az: 45 })).ok).toBe(true);
    // the 3D window over the whole width: the tree and «Свойства» closed (Ctrl+B and the properties button)
    expect((await run(page, "panel.set", { panel: "project" })).ok).toBe(true);
    expect((await run(page, "panel.set", { panel: "properties" })).ok).toBe(true);
    await expect(page.getByLabel("Дерево данных")).toHaveCount(0);
    const bar = page.getByRole("toolbar", { name: "Панель 3D-окна" });
    await expect(bar.getByLabel("Азимут линии разреза, градусы")).toBeVisible();

    const sizes = await bar.evaluate((el) => {
      const r = (x: Element) => x.getBoundingClientRect();
      const buttons = [...el.querySelectorAll("button")].map((b) => ({ w: r(b).width, h: r(b).height, icon: b.querySelector("svg") ? r(b.querySelector("svg")!).width : null }));
      const input = el.querySelector("input[type=number]");
      return { buttons, input: input ? { w: r(input).width, h: r(input).height } : null, bar: { w: r(el).width, h: r(el).height }, win: window.innerWidth, scroll: el.scrollWidth - el.clientWidth };
    });
    expect(sizes.buttons.length).toBeGreaterThan(8);
    for (const b of sizes.buttons) {
      expect(Math.abs(b.w - 42)).toBeLessThanOrEqual(1);
      expect(Math.abs(b.h - 39)).toBeLessThanOrEqual(1);
      if (b.icon !== null) expect(b.icon).toBe(24);
    }
    expect(sizes.input!.w).toBeLessThanOrEqual(60); // 58 px field as before
    expect(sizes.input!.h).toBeLessThan(30);
    expect(sizes.bar.w).toBeLessThanOrEqual(sizes.win);
    expect(sizes.scroll).toBeLessThanOrEqual(0);
    expect(sizes.bar.h).toBeLessThanOrEqual(2 * 39 + 12); // two rows
    await page.screenshot({ path: shot("toolbar-1024.png") });
  } finally {
    await close(app);
  }
});
