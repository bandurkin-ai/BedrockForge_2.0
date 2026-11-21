// Э13, подшаг 13.34 (ТЗ 7.25, 7.28, 8.3; ADR 0019 §2, §8, §11): the charts of a set end to end on the demo (the demo
// itself is not changed: the lesson of Э12 — a cold numba).
//  1. The menu of the set «Скважины 2025» → «Гистограмма»: the window, the chart saved at once in the set → «Графики».
//  2. Another field — the name of the chart (and the title of the window) follows it; a name given by hand
//     («Переименовать…» of the tree) stays when the field changes again.
//  3. Т7.28-1: a sample edited in «Таблица» → the chart opened again from the tree has another mean (the data are not
//     stored with a chart: computed at every opening).
//  4. Т7.25-3: PNG of the chart — twice its size.
//  5. Т8.3-3: «Рассеяние» (the ribbon «Анализ»), a lasso over the points → the layer of the selection in 3D has as
//     many segments as samples are selected.
// A window of 1024 × 640 with the ribbon folded; the data and the waits for lists — through the core; the limits on
// the .exe are ×3.
import { expect, test, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { answerSaveDialog, close, coreApi, launch, openDemo, placeRight, setWindowSize, shot } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);
const SET = "Скважины 2025";

type Chart = { id: number; name: string; kind: string; version: number; auto_name: boolean; settings: Record<string, unknown> };
type Sel = { selected: boolean; n?: number; segments: number | null; rows: number | null };

const charts = async (page: Page, setId: number) => (await coreApi<{ charts: Chart[] }>(page, "GET", `/charts?set_id=${setId}`)).body.charts;
const chartWin = (page: Page, kind: string) => page.getByRole("dialog").filter({ has: page.getByTestId("chart-window") }).filter({ has: page.getByRole("img", { name: kind }) });
const selection = (page: Page) => page.evaluate(() => (window as unknown as { __bfSelection: { state(): Sel } }).__bfSelection.state());

/** «Среднее» of the statistics of a histogram window (the first column — with the weight). */
const meanOf = (win: Locator) => win.getByTestId("histogram-stats").locator("tr", { has: win.page().locator("th", { hasText: /^Среднее$/ }) }).locator("td").first().innerText();

/** The width and height of a PNG file (its IHDR). */
function pngSize(file: string): { width: number; height: number } {
  const b = fs.readFileSync(file);
  expect(b.subarray(1, 4).toString("ascii")).toBe("PNG");
  return { width: b.readUInt32BE(16), height: b.readUInt32BE(20) };
}

test("charts: «Гистограмма» from the menu of a set → «Графики»; the name follows the field, a name by hand stays; an edited sample — another mean (Т7.28-1); PNG twice the chart (Т7.25-3); a lasso on the scatter — as many segments in 3D (Т8.3-3)", async () => {
  test.setTimeout(LIMIT(120_000));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-charts-"));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await openDemo(page);
    const setId = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets.find((s) => s.name === SET)!.id;
    expect(await charts(page, setId)).toEqual([]);

    // ---- 1. the menu of the set → «Гистограмма»: the window, the chart in «Графики» ---------------------------------
    const setRow = page.getByTestId(`tree-set-${setId}`);
    await setRow.scrollIntoViewIfNeeded();
    await setRow.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Гистограмма", exact: true }).click();
    const hist = chartWin(page, "Гистограмма");
    await expect(hist).toHaveCount(1, { timeout: 15_000 });
    await placeRight(page, hist, 560); // the tree stays in sight (the place is remembered for the kind)
    await expect.poll(async () => (await charts(page, setId)).length, { timeout: 15_000 }).toBe(1);
    let row = (await charts(page, setId))[0];
    expect(row.kind).toBe("histogram");
    expect(row.auto_name).toBe(true);
    await expect(page.getByTestId(`tree-chart-${row.id}`)).toHaveText(row.name, { timeout: 10_000 });
    await expect(hist).toHaveAttribute("aria-label", `${row.name} · ${SET}`);
    await expect(hist.getByTestId("histogram-stats")).toBeVisible({ timeout: 30_000 });
    const first = (row.settings as { field: string }).field;
    expect(row.name).toContain(first);

    // ---- 2. another field — another name; a name by hand stays --------------------------------------------------------
    const field = hist.getByLabel("Поле", { exact: true });
    const other = (await field.locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value))).find((v) => v !== first && v !== "")!;
    await field.selectOption(other);
    await expect.poll(async () => (await charts(page, setId))[0].name, { timeout: 10_000 }).toContain(other);
    row = (await charts(page, setId))[0];
    expect(row.name).not.toContain(first);
    await expect(hist).toHaveAttribute("aria-label", `${row.name} · ${SET}`);
    await expect(page.getByTestId(`tree-chart-${row.id}`)).toHaveText(row.name);
    // «Переименовать…» of the tree: our own dialog
    await page.getByTestId(`tree-chart-${row.id}`).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Переименовать…" }).click();
    const ask = page.getByRole("dialog", { name: "Переименовать график" });
    await ask.getByRole("textbox").fill("Золото по пробам");
    await ask.getByRole("textbox").press("Enter");
    await expect.poll(async () => (await charts(page, setId))[0].name, { timeout: 10_000 }).toBe("Золото по пробам");
    await field.selectOption(first);
    await expect.poll(async () => ((await charts(page, setId))[0].settings as { field: string }).field, { timeout: 10_000 }).toBe(first);
    row = (await charts(page, setId))[0];
    expect(row.name).toBe("Золото по пробам");
    expect(row.auto_name).toBe(false);
    await expect(hist).toHaveAttribute("aria-label", `Золото по пробам · ${SET}`);
    await expect(hist.getByTestId("histogram-stats")).toBeVisible();
    await expect.poll(async () => (await hist.getByLabel("Поле", { exact: true }).inputValue())).toBe(first);
    const meanBefore = await meanOf(hist);
    await hist.getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(hist).toHaveCount(0);

    // ---- 3. Т7.28-1: a sample edited in «Таблица» → opened again — another mean ---------------------------------------
    await setRow.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Пробы", exact: true }).click();
    const panel = page.getByLabel("Нижняя панель");
    const grid = panel.getByTestId("table-grid");
    await expect(grid.getByRole("row").nth(1)).toBeVisible({ timeout: 30_000 });
    const headers = await grid.getByRole("columnheader").allInnerTexts();
    const col = headers.findIndex((h) => h.split(/[\s,]/)[0] === first);
    expect(col, `no column «${first}» in ${JSON.stringify(headers)}`).toBeGreaterThanOrEqual(0);
    await grid.getByRole("row").nth(1).getByRole("gridcell").nth(col).click();
    await grid.getByLabel(`Правка ${first}`).fill("950");
    await page.keyboard.press("Enter");
    await expect(grid.getByRole("row").nth(1).getByRole("gridcell").nth(col)).toHaveText("950", { timeout: 10_000 });
    await page.getByTestId(`tree-chart-${row.id}`).click();
    await expect(hist).toHaveCount(1, { timeout: 15_000 });
    await expect(hist.getByTestId("histogram-stats")).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => meanOf(hist), { timeout: 15_000 }).not.toBe(meanBefore);
    await expect(hist).toHaveAttribute("aria-label", `Золото по пробам · ${SET}`);
    await page.screenshot({ path: shot("charts-histogram.png") });

    // ---- 4. Т7.25-3: PNG twice the chart --------------------------------------------------------------------------------
    const png = path.join(dir, "Гистограмма.png");
    await answerSaveDialog(app, png);
    await hist.getByRole("button", { name: "PNG", exact: true }).click();
    await expect(hist.getByText(`Сохранено: ${png}`)).toBeVisible({ timeout: 15_000 });
    const svg = hist.getByRole("img", { name: /Гистограмма/ }).first();
    const box = await svg.evaluate((el) => ({ width: (el as SVGSVGElement).viewBox.baseVal.width, height: (el as SVGSVGElement).viewBox.baseVal.height }));
    expect(box.width).toBeGreaterThan(100);
    expect(pngSize(png)).toEqual({ width: 2 * box.width, height: 2 * box.height });
    await hist.getByRole("button", { name: "Закрыть", exact: true }).click();

    // ---- 5. Т8.3-3: «Рассеяние», a lasso → as many segments in 3D as samples selected -----------------------------------
    await page.getByRole("tab", { name: "Анализ" }).click();
    await page.getByLabel("Лента").getByRole("button", { name: "Рассеяние", exact: true }).first().click();
    const scatter = chartWin(page, "Рассеяние");
    await expect(scatter).toHaveCount(1, { timeout: 15_000 });
    const plot = scatter.locator('g[data-tool="lasso"] > rect').first();
    await expect(plot).toBeVisible({ timeout: 30_000 });
    await plot.scrollIntoViewIfNeeded();
    const p = (await plot.boundingBox())!;
    const at = (fx: number, fy: number) => ({ x: p.x + fx * p.width, y: p.y + fy * p.height });
    const ring = [at(0.01, 0.99), at(0.01, 0.4), at(0.25, 0.3), at(0.45, 0.7), at(0.4, 0.99)];
    await page.mouse.move(ring[0].x, ring[0].y);
    await page.mouse.down();
    for (const q of ring.slice(1)) await page.mouse.move(q.x, q.y, { steps: 5 });
    await page.mouse.move(ring[0].x, ring[0].y, { steps: 5 });
    await page.mouse.up();
    await expect(scatter.getByTestId("scatter-selected")).toContainText(/Выделено [\d\s ]+ проб/, { timeout: 15_000 });
    const shown = Number((await scatter.getByTestId("scatter-selected").innerText()).replace(/[^\d]/g, ""));
    expect(shown).toBeGreaterThan(0);
    await expect.poll(async () => (await selection(page)).n, { timeout: 15_000 }).toBe(shown);
    await expect.poll(async () => (await selection(page)).segments, { timeout: 30_000 }).toBe(shown);
    await page.screenshot({ path: shot("charts-lasso.png") });
  } finally {
    await close(app);
  }
});
