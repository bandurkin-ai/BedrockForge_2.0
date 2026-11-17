// Э4 acceptance on the real window (ТЗ 7.10): the «Таблица» panel — tabs, sorting, an edit with a decimal
// comma, a refused empty required field, the journal and undo; the set in the data tree; the tools menu.
import { expect, test } from "@playwright/test";
import { close, launch, shot } from "./helpers";

test("«Таблица»: sort, edit 12,5 → 12.5, empty required field refused, journal undo, tools menu", async () => {
  test.setTimeout(180_000);
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 30_000 });
    // a set made through the core API: an empty set, collars and samples by hand
    const setId = await page.evaluate(async () => {
      const c = window.bf!.core()!;
      const call = async (method: string, url: string, body?: unknown) => {
        const r = await fetch(`${c.baseUrl}/api/v1${url}`, { method, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
        return r.json();
      };
      const set = await call("POST", "/drillholes/sets", { name: "Набор Э4" });
      for (const [hole, x] of [["B2", 20], ["A1", 10]] as const) await call("POST", `/drillholes/sets/${set.id}/tables/collar/rows`, { values: { hole, x, y: 5, z: 100, td: 30 } });
      return set.id as number;
    });
    await expect(page.getByRole("treeitem", { name: /Набор Э4/ })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("treeitem", { name: /Набор Э4/ }).click();
    const panel = page.getByLabel("Нижняя панель");
    await expect(panel.getByRole("tab", { name: /Устья/ })).toHaveAttribute("aria-selected", "true");
    const grid = panel.getByTestId("table-grid");
    await expect(grid.getByRole("row")).toHaveCount(3); // header + 2
    await grid.getByRole("columnheader", { name: /Скважина/ }).getByRole("button").first().click();
    await expect(grid.getByRole("row").nth(1)).toContainText("A1");

    // edit X with a decimal comma
    await grid.getByRole("row").nth(1).getByRole("gridcell").nth(1).click();
    await grid.getByLabel("Правка X").fill("12,5");
    await page.keyboard.press("Enter");
    await expect(grid.getByRole("row").nth(1)).toContainText("12,5");
    const x = await page.evaluate(async (sid) => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/drillholes/sets/${sid}/tables/collar?sort=hole`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()).rows[0].x;
    }, setId);
    expect(x).toBe(12.5);

    // an empty TD (required) is refused with a message
    await grid.getByRole("row").nth(1).getByRole("gridcell").nth(4).click();
    await grid.getByLabel("Правка Глубина").fill("");
    await page.keyboard.press("Enter");
    await expect(panel.getByRole("alert")).toContainText("обязательное поле");
    await page.keyboard.press("Escape");

    // journal: undo the X edit
    await panel.getByRole("button", { name: "Журнал правок" }).click();
    const log = panel.getByLabel("Журнал правок", { exact: true });
    await expect(log).toContainText("X: 10 → 12,5");
    await log.getByRole("button", { name: "Отменить" }).first().click();
    await expect(grid.getByRole("row").nth(1)).toContainText("10");
    await expect(log).toContainText("отменено");
    await page.screenshot({ path: shot("table-panel.png") });

    // the tools menu of the panel
    await panel.getByRole("button", { name: "Инструменты ▾" }).click();
    await expect(panel.getByRole("menuitem", { name: "Вычисляемое поле…" })).toBeVisible();
  } finally {
    await close(app);
  }
});
