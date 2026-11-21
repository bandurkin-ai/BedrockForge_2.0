// Э15, подшаг 15.18 (ТЗ 8.5; ADR 0021 §9–11): true thickness and ore intercepts end to end on the demo.
//  1. Ctrl+K «Истинная мощность» → the window: «Пробы», global α 30°, Aп 120° → the preview of a hole (the column
//     coloured by TT / LENGTH, the rows, TT_SRC «глобальная») → «Применить» → the job → TT, TT_FLAG, TT_SRC in
//     «Пробы» (TT of every row with a trajectory, never 0 in place of empty).
//  2. Ctrl+K «Рудные пересечения» → the window: AU, c = 0 is refused in the window (c > 0, as the core), c = 1 → the
//     preview (accepted intercepts with LABEL, the summary) → «Применить» → the table «Рудные пересечения» in the
//     bottom panel, the rows `tt` and `intercepts` in «Обработка проб» of the tree, the display «Рудные пересечения»
//     switched on in 3D with a label per intercept.
// One spec, a window of 1024 × 640 with the ribbon folded (as composites.spec); the limits on the .exe are ×3.
import { expect, test, type Page } from "@playwright/test";
import { close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);
const SET = "Скважины 2025";
const RUN_NAME = "Рудные пересечения";

type Tab = { table: string; name: string; kind: string; origin?: { result_id: number; op: string; stale: boolean } | null };
type Result = { id: number; op: string; name: string; table_id: number | null; params: Record<string, unknown>; report: Record<string, unknown>; stale: boolean };
type Rows = { columns: { key: string; name: string }[]; rows: Record<string, unknown>[] };

const tabs = async (page: Page, setId: number) => Object.fromEntries((await coreApi<{ tables: Tab[] }>(page, "GET", `/drillholes/sets/${setId}/tables`)).body.tables.map((t) => [t.name, t]));
const results = async (page: Page, setId: number) => (await coreApi<{ results: Result[] }>(page, "GET", `/intercepts/results?set_id=${setId}`)).body.results;

async function rowsOf(page: Page, setId: number, table: string): Promise<Rows> {
  const rows: Record<string, unknown>[] = [];
  for (;;) {
    const r = await coreApi<Rows>(page, "GET", `/drillholes/sets/${setId}/tables/${table}?limit=1000&offset=${rows.length}`);
    expect(r.status, JSON.stringify(r.body)).toBe(200);
    rows.push(...r.body.rows);
    if (r.body.rows.length < 1000) return { ...r.body, rows };
  }
}
const column = (got: Rows, name: string) => {
  const key = got.columns.find((c) => c.name === name)?.key;
  return key === undefined ? null : got.rows.map((r) => r[key]);
};

/** Ctrl+K → the command by its title → Enter (the palette closes). */
async function palette(page: Page, query: string): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(query);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

/** The window of a tool: its form (`testId`), then its job. */
const toolWindow = (page: Page, testId: string) => page.getByRole("dialog").filter({ has: page.getByTestId(testId).or(page.getByTestId("composites-job")) });

test("true thickness and ore intercepts: Ctrl+K, the previews, TT in «Пробы», the table in the tree, labels in 3D", async () => {
  test.setTimeout(LIMIT(180_000));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640);
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: LIMIT(90_000) });
    expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
    await waitFrame(page);
    const started = Date.now();

    const sets = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets;
    const setId = sets.find((s) => s.name === SET)!.id;
    const samplesKey = (await tabs(page, setId))["Пробы"].table;
    expect(await results(page, setId)).toEqual([]);

    // ---- 1. «Истинная мощность»: Ctrl+K → global 30/120 → the preview → «Применить» → TT in «Пробы» ----------
    await palette(page, "Истинная мощность");
    const tt = toolWindow(page, "intercepts-tt");
    await expect(tt).toBeVisible();
    await expect(tt.getByLabel("Таблица", { exact: true })).toHaveValue(samplesKey);
    await expect(tt.getByTestId("composites-apply")).toBeDisabled(); // no α, Aп yet
    await tt.getByLabel("Угол падения α", { exact: true }).fill("30");
    await tt.getByLabel("Азимут падения Aп", { exact: true }).fill("120");
    await expect(tt.getByTestId("intercepts-tt-rows")).toBeVisible({ timeout: 30_000 });
    await expect(tt.getByTestId("intercepts-tt-preview")).toContainText("глобальная");
    await page.screenshot({ path: shot("intercepts-tt-preview.png") });
    await tt.getByTestId("composites-apply").click();
    await expect(tt.getByTestId("composites-job-status")).toHaveAttribute("data-status", "done", { timeout: LIMIT(60_000) });
    const samples = await rowsOf(page, setId, samplesKey);
    const ttCol = column(samples, "TT")!;
    const src = column(samples, "TT_SRC")!;
    expect(ttCol).not.toBeNull();
    expect(column(samples, "TT_FLAG")).not.toBeNull();
    const filled = ttCol.filter((v) => typeof v === "number");
    expect(filled.length).toBeGreaterThan(1000);
    expect(filled.every((v) => (v as number) > 0)).toBe(true); // a TT is a thickness, never 0 in place of empty
    expect(src.filter((s) => s === "глобальная").length).toBe(filled.length);
    const ttResult = (await results(page, setId)).find((r) => r.op === "tt")!;
    expect(ttResult.params).toMatchObject({ table: samplesKey, orientation: [{ source: "global", alpha: 30, dipdir: 120 }] });
    await tt.getByRole("button", { name: "Закрыть", exact: true }).last().click();
    await expect(tt).toHaveCount(0);
    console.log(`tt: ${((Date.now() - started) / 1000).toFixed(1)} s`);

    // ---- 2. «Рудные пересечения»: Ctrl+K → c = 0 refused, c = 1 → the preview → «Применить» -----------------
    await palette(page, "Рудные пересечения");
    const run = toolWindow(page, "intercepts-run");
    await expect(run).toBeVisible();
    await expect(run.getByLabel("Таблица", { exact: true })).toHaveValue(samplesKey);
    await expect(run.getByLabel("Поле", { exact: true })).toHaveValue("AU");
    const cutoff = run.getByTestId("composites-param-cutoff").getByRole("textbox");
    await cutoff.fill("0");
    await expect(run.getByTestId("composites-param-cutoff")).toContainText("должно быть больше нуля");
    await expect(run.getByTestId("composites-apply")).toBeDisabled();
    await cutoff.fill("1");
    // the orientation is off by default in this window (TT empty): switched on, the same global 30/120
    await run.getByRole("checkbox", { name: /Считать истинную мощность/ }).check();
    await run.getByLabel("Угол падения α", { exact: true }).fill("30");
    await run.getByLabel("Азимут падения Aп", { exact: true }).fill("120");
    // the hole of the preview: one with intercepts by c = 1 (the first ore sample of «Пробы»)
    const holeKey = samples.columns.find((c) => c.name === "Скважина")?.key ?? "hole";
    const au = column(samples, "AU")!;
    const oreHole = String(samples.rows[au.findIndex((g) => typeof g === "number" && g >= 2)][holeKey]);
    await run.getByLabel("Скважина предпросмотра").selectOption({ label: oreHole });
    await expect(run.getByTestId("intercepts-run-rows")).toBeVisible({ timeout: 30_000 });
    await expect(run.getByTestId("intercepts-run-rows")).toContainText(/\d+,\d м @ \d+,\d\d г\/т \(ИМ \d+,\d м\)/);
    await expect(run.getByTestId("intercepts-run-summary")).toContainText("Σ TT, м");
    await page.screenshot({ path: shot("intercepts-run-preview.png") });
    await run.getByTestId("composites-apply").click();
    await expect(run.getByTestId("composites-job-status")).toHaveAttribute("data-status", "done", { timeout: LIMIT(60_000) });
    await expect(run.getByTestId("composites-job-result")).toHaveText(`Готово: «${RUN_NAME}»`);

    // the table: in the tabs of the set, opened in the bottom panel, its rows with LABEL
    const runTab = (await tabs(page, setId))[RUN_NAME];
    const runResult = (await results(page, setId)).find((r) => r.op === "intercepts")!;
    expect(runTab.origin).toMatchObject({ result_id: runResult.id, op: "intercepts", stale: false });
    expect(runResult.params).toMatchObject({ table: samplesKey, field: "AU", cutoff: 1 });
    const runRows = await rowsOf(page, setId, runTab.table);
    const labels = column(runRows, "LABEL")!.filter((l) => typeof l === "string" && l !== "");
    expect(labels.length).toBeGreaterThan(10);
    expect(labels.every((l) => /ИМ \d+,\d м/.test(String(l)))).toBe(true); // TT in every label: the orientation is global
    const panel = page.getByLabel("Нижняя панель");
    await expect(panel.getByRole("tab", { name: new RegExp(RUN_NAME) })).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });
    await expect(panel.getByTestId("table-grid")).toHaveAttribute("aria-rowcount", String(runRows.rows.length), { timeout: 30_000 });

    await run.getByRole("button", { name: "Закрыть", exact: true }).last().click();
    await expect(run).toHaveCount(0);
    // the tree: «Обработка проб» of the set with both results
    const group = page.getByRole("group", { name: "Обработка проб: результаты" });
    await expect(group.getByRole("treeitem", { name: new RegExp(`^${RUN_NAME}`) })).toBeVisible({ timeout: 30_000 });
    await expect(group.getByRole("treeitem", { name: /^Истинная мощность/ })).toBeVisible();

    // 3D: the display «Рудные пересечения» switched on, a label per intercept
    const legendOf = async () => (await page.evaluate(() => window.__bfScene!.state().displays.legend)).find((d) => d.name === RUN_NAME);
    await expect.poll(async () => (await legendOf())?.labels ?? 0, { timeout: 60_000, intervals: [200, 400, 800] }).toBe(labels.length);
    await waitFrame(page);
    await page.screenshot({ path: shot("intercepts-3d.png") });
    const shown = (await legendOf())!;
    console.log(`intercepts display: holes ${shown.holes}, intervals ${shown.intervals}, labels ${shown.labels} of ${labels.length}`);

    const seconds = (Date.now() - started) / 1000;
    console.log(`intercepts.spec: ${seconds.toFixed(1)} s after the demo`);
    expect(seconds).toBeLessThan(LIMIT(40));
  } finally {
    await close(app);
  }
});
