// Э6.1 (ТЗ 7.35 Д-03, ADR 0008): rows of the data tree expand only by the arrow; a click on the name selects
// (the set becomes active: «Набор: …» in the status bar) and does not expand; the keyboard follows WAI-ARIA.
import { expect, test } from "@playwright/test";
import { close, launch } from "./helpers";

test("tree: the arrow expands, a click on the name selects, keyboard ↑/↓/←", async () => {
  test.setTimeout(180_000);
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
    const holes = tree.getByTestId("tree-section-holes");
    const arrow = holes.getByRole("button", { name: /«Скважины»/ });
    await expect(arrow).toHaveAttribute("aria-expanded", "true");

    // a click on the name: nothing collapses
    await holes.getByText("Скважины", { exact: true }).click();
    await expect(arrow).toHaveAttribute("aria-expanded", "true");
    const set = tree.getByRole("treeitem", { name: /Скважины 2025/ });
    await expect(set).toBeVisible({ timeout: 30_000 });

    // the arrow collapses and expands
    await arrow.click();
    await expect(arrow).toHaveAttribute("aria-expanded", "false");
    await expect(set).toHaveCount(0);
    await arrow.click();
    await expect(arrow).toHaveAttribute("aria-expanded", "true");

    // a click on a set: active set, the bottom panel is not opened, the row is not expanded
    await set.getByText("Скважины 2025", { exact: true }).click();
    await expect(page.getByRole("contentinfo")).toContainText("Набор: Скважины 2025");
    await expect(set).toHaveAttribute("aria-selected", "true");
    await expect(page.getByLabel("Нижняя панель")).toHaveCount(0);

    // keyboard: ↑ to the section, ← collapses it, → expands it again
    await set.focus();
    await page.keyboard.press("ArrowUp");
    await expect(holes).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(arrow).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("ArrowRight");
    await expect(arrow).toHaveAttribute("aria-expanded", "true");
  } finally {
    await close(app);
  }
});
