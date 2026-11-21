// Э12, подшаг 12.32 (ТЗ 8.2, 7.35 Д-26; ADR 0018 §6–11): sample processing end to end on the demo, which has no
// results of «Обработка проб» (on a cold numba cache their kernels made the demo too slow): the spec makes them.
//  1. «Композиты»: «Пробы», L = 2 → the preview on a hole (the samples and the composites side by side, the summary
//     «до/после» of the hole and the set) → «Применить» → the job → «Назад к параметрам» → «Применить» on the same
//     table → «Заменить?» → the table «Композиты 2 м» of the new result in the tabs of the set; its rows through
//     the core.
//  2. Т8.2-20 (E2E): «Рудные интервалы (ГКЗ)» Сб 1, Мрт 4, Мпп 4 → «Применить»; then other conditions → the
//     preview coloured by class with the labels and «Протокол» → «Применить» → «Заменить?» → the table «Рудные
//     интервалы» opened in the bottom panel, ORE_CLS of «Пробы» by the new conditions, the display «Рудные интервалы»
//     in 3D with the labels of the intervals (the legend of displays of the scene hook).
//  3. «Урезка»: the curves of the analysis; the threshold is not written until «Применить» → AU_CUT, AU_CUT_F.
// One spec (CI economy of Э12): a window of 1024 × 640 with the ribbon folded; the data and the waits for lists —
// through the core; the limits on the .exe are ×3.
import { expect, test, type Page } from "@playwright/test";
import { close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);
const SET = "Скважины 2025";

type Tab = { table: string; name: string; kind: string; origin?: { result_id: number; op: string; stale: boolean } | null };
type Result = { id: number; op: string; name: string; table_id: number | null; params: Record<string, unknown>; stale: boolean; created_at: string };
type Rows = { columns: { key: string; name: string }[]; rows: Record<string, unknown>[]; total?: number };

const tabs = async (page: Page, setId: number) => {
  const r = await coreApi<{ tables: Tab[] }>(page, "GET", `/drillholes/sets/${setId}/tables`);
  return Object.fromEntries(r.body.tables.map((t) => [t.name, t]));
};
const results = async (page: Page, setId: number) => (await coreApi<{ results: Result[] }>(page, "GET", `/composites/results?set_id=${setId}`)).body.results;
/** The results of an operation of the set, the latest first (by `created_at`: SQLite gives a freed id again). */
const ofOp = async (page: Page, setId: number, op: string) => (await results(page, setId)).filter((r) => r.op === op).sort((a, b) => at(b) - at(a));
const latest = async (page: Page, setId: number, op: string) => (await ofOp(page, setId, op))[0];
const at = (r: Result) => Date.parse(r.created_at);

/** Every row of a table of the set through the API of the «Таблица» panel (pages of 1 000). */
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

/** The window of a tool: its form (`testId`), then its job (one tool window is open at a time). */
const toolWindow = (page: Page, testId: string) => page.getByRole("dialog").filter({ has: page.getByTestId(testId).or(page.getByTestId("composites-job")) });

/** «Применить» of a new table → the job of the window is done. */
async function applyNew(win: ReturnType<typeof toolWindow>, name: string, timeout: number): Promise<void> {
  await win.getByTestId("composites-apply").click();
  await expect(win.getByTestId("composites-job-status")).toHaveAttribute("data-status", "done", { timeout });
  await expect(win.getByTestId("composites-job-result")).toHaveText(`Готово: «${name}»`);
}

/** «Применить» of a table the set has → «Заменить?» → «Заменить» → the job of the window is done. */
async function applyReplacing(page: Page, win: ReturnType<typeof toolWindow>, name: string, timeout: number): Promise<void> {
  await win.getByTestId("composites-apply").click();
  const ask = page.getByRole("dialog", { name: "Заменить?" });
  await expect(ask).toContainText(`«${name}»`);
  await ask.getByRole("button", { name: "Заменить", exact: true }).click();
  await expect(win.getByTestId("composites-job-status")).toHaveAttribute("data-status", "done", { timeout });
  await expect(win.getByTestId("composites-job-result")).toHaveText(`Готово: «${name}»`);
}

/** The job view → the form with the same values. */
const back = (win: ReturnType<typeof toolWindow>) => win.getByRole("button", { name: "Назад к параметрам" }).click();

test("sample processing: composites L = 2 with the preview, ore intervals of Д-26 (Т8.2-20) with the protocol and labels in 3D, the top cut", async () => {
  test.setTimeout(LIMIT(180_000));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    const loaded = Date.now();
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: LIMIT(90_000) });
    console.log(`demo: ${((Date.now() - loaded) / 1000).toFixed(1)} s`);
    expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
    await waitFrame(page);
    const started = Date.now();

    // ---- the demo: no results of «Обработка проб» -----------------------------------------------------------
    const sets = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets;
    const setId = sets.find((s) => s.name === SET)!.id;
    const demoTabs = await tabs(page, setId);
    expect(await results(page, setId)).toEqual([]);
    expect([demoTabs["Композиты 2 м"], demoTabs["Рудные интервалы"]]).toEqual([undefined, undefined]);
    const samplesKey = demoTabs["Пробы"].table;

    // ---- 1. «Композиты»: «Пробы», L = 2 → the preview → «Применить», again → «Заменить?» → the tabs of the set -
    expect((await runCommand(page, "composite.run")).ok).toBe(true);
    const comp = toolWindow(page, "composites-composite");
    await expect(comp).toBeVisible();
    await expect(comp.getByLabel("Таблица", { exact: true })).toHaveValue(samplesKey);
    const fields = comp.getByRole("group", { name: "Поля" });
    await expect(fields.getByRole("checkbox", { name: "AU" })).toBeChecked();
    await comp.getByTestId("composites-param-length").getByRole("textbox").fill("2");
    await expect(comp.getByTestId("composites-samples")).toBeVisible({ timeout: 30_000 });
    await expect(comp.getByTestId("composites-result")).toBeVisible();
    const summaries = comp.getByTestId("composites-summary");
    await expect(summaries).toHaveCount(2, { timeout: 30_000 }); // the hole and the set
    await expect(summaries.last()).toContainText("Набор");
    await expect(comp.getByTestId("composites-reason")).toContainText("«Композиты 2 м»");
    await page.screenshot({ path: shot("composites-preview.png") });
    await applyNew(comp, "Композиты 2 м", LIMIT(60_000));
    const first = await latest(page, setId, "length");
    expect(first.params).toMatchObject({ table: samplesKey, method: "length", length: 2, fields: ["AU", "CU"] });
    const firstTab = (await tabs(page, setId))["Композиты 2 м"];
    expect(firstTab.origin).toMatchObject({ result_id: first.id, op: "length", stale: false });
    const firstRows = await rowsOf(page, setId, firstTab.table);
    expect(firstRows.rows.length).toBeGreaterThan(3000);
    // the same table again: «Заменить?»
    await back(comp);
    await expect(comp.getByTestId("composites-param-length").getByRole("textbox")).toHaveValue("2");
    await applyReplacing(page, comp, "Композиты 2 м", LIMIT(60_000));
    const madeAll = await ofOp(page, setId, "length");
    expect(madeAll).toHaveLength(1); // the result of the replaced table is gone
    const made = madeAll[0];
    expect(at(made)).toBeGreaterThan(at(first));
    expect(made.params).toMatchObject({ table: samplesKey, method: "length", length: 2, fields: ["AU", "CU"] });
    const allTabs = await tabs(page, setId);
    expect(Object.keys(allTabs).filter((n) => n.startsWith("Композиты"))).toEqual(["Композиты 2 м"]); // replaced, not « (2)»
    const compTab = allTabs["Композиты 2 м"];
    expect(compTab.origin).toMatchObject({ result_id: made.id, op: "length", stale: false });
    const compRows = await rowsOf(page, setId, compTab.table);
    expect(compRows.rows.length).toBe(firstRows.rows.length); // the same samples, the same composites
    expect(column(compRows, "LENGTH")!.every((l) => typeof l === "number" && l > 0)).toBe(true);
    await comp.getByRole("button", { name: "Закрыть", exact: true }).last().click();
    await expect(comp).toHaveCount(0);
    // the tabs of the set: «Композиты 2 м» opens with its rows
    await page.getByRole("treeitem", { name: new RegExp(SET) }).first().dblclick();
    const panel = page.getByLabel("Нижняя панель");
    await panel.getByRole("tab", { name: /Композиты 2 м/ }).click();
    await expect(panel.getByRole("tab", { name: /Композиты 2 м/ })).toHaveAttribute("aria-selected", "true");
    await expect(panel.getByTestId("table-grid")).toHaveAttribute("aria-rowcount", String(compRows.rows.length), { timeout: 30_000 });
    console.log(`composites: ${((Date.now() - started) / 1000).toFixed(1)} s`);

    // ---- 2. Т8.2-20: «Рудные интервалы (ГКЗ)» → the preview, «Протокол» → «Применить» → table, ORE_CLS, 3D -----
    const legendOf = async () => (await page.evaluate(() => window.__bfScene!.state().displays.legend)).find((d) => d.name === "Рудные интервалы");
    expect(await legendOf()).toBeUndefined(); // no display yet
    expect(column(await rowsOf(page, setId, samplesKey), "ORE_CLS")).toBeNull(); // no fields of ore intervals yet
    expect((await runCommand(page, "composite.ore_intervals")).ok).toBe(true);
    const ore = toolWindow(page, "composites-ore");
    await expect(ore).toBeVisible();
    await expect(ore.getByLabel("Таблица", { exact: true })).toHaveValue(samplesKey);
    await expect(ore.getByLabel("Поле", { exact: true })).toHaveValue("AU");
    const conditions = async (cutoff: string, mMin: string, mWaste: string) => {
      for (const [key, v] of [["cutoff", cutoff], ["m_min", mMin], ["m_waste_max", mWaste]] as const) await ore.getByTestId(`composites-param-${key}`).getByRole("textbox").fill(v);
    };
    // the first ore intervals: Сб 1 г/т, Мрт 4 м, Мпп 4 м — a new table, ORE_ID / ORE_CLS of «Пробы»
    await conditions("1", "4", "4");
    await applyNew(ore, "Рудные интервалы", LIMIT(60_000));
    const oreFirst = await latest(page, setId, "ore");
    expect(at(oreFirst)).toBeGreaterThan(at(made));
    expect(oreFirst.params).toMatchObject({ table: samplesKey, field: "AU", cutoff: 1, m_min: 4, m_waste_max: 4 });
    const samples0 = await rowsOf(page, setId, samplesKey);
    const holeKey = samples0.columns.find((c) => c.name === "Скважина")?.key ?? "hole";
    const cls0 = column(samples0, "ORE_CLS")!;
    expect(cls0.filter((c) => c === "ore").length).toBeGreaterThan(100);
    // the hole of the preview: one with ore by the first conditions
    const oreHole = String(samples0.rows[cls0.findIndex((c) => c === "ore")][holeKey]);
    // other conditions on the same table: Сб 0,8 г/т, Мрт 2 м, Мпп 3 м
    await back(ore);
    await conditions("0,8", "2", "3");
    await ore.getByLabel("Скважина предпросмотра").selectOption({ label: oreHole });
    const classes = ore.getByTestId("ore-classes");
    await expect(classes.locator("rect[data-value='ore']").first()).toBeAttached({ timeout: 30_000 }); // coloured by class
    await expect(ore.getByTestId("ore-final").locator("rect[data-value='ore']").first()).toBeAttached();
    await expect(ore.getByTestId("ore-final").locator("text").filter({ hasText: /^\d+,\d м @ \d+,\d\d г\/т \(МС \d+,\d\)$/ }).first()).toBeAttached(); // LABEL
    await expect(ore.getByTestId("ore-legend")).toBeVisible();
    await page.screenshot({ path: shot("composites-ore-preview.png") });
    await ore.getByTestId("ore-tab-protocol").click();
    await expect(ore.getByTestId("ore-protocol-grid")).toBeVisible({ timeout: 30_000 });
    await expect(ore.getByTestId("ore-protocol")).toContainText(/IN/);
    await page.screenshot({ path: shot("composites-ore-protocol.png") });
    await ore.getByTestId("ore-tab-column").click();
    await applyReplacing(page, ore, "Рудные интервалы", LIMIT(60_000));
    const oreAll = await ofOp(page, setId, "ore");
    expect(oreAll).toHaveLength(1); // the result of the replaced table is gone
    const oreMade = oreAll[0];
    expect(at(oreMade)).toBeGreaterThan(at(oreFirst));
    expect(oreMade.params).toMatchObject({ table: samplesKey, field: "AU", cutoff: 0.8, m_min: 2, m_waste_max: 3 });
    // the table «Рудные интервалы» opens in the bottom panel by itself
    await expect(panel.getByRole("tab", { name: /Рудные интервалы/ })).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });
    const oreTab = (await tabs(page, setId))["Рудные интервалы"];
    expect(oreTab.origin).toMatchObject({ result_id: oreMade.id, op: "ore", stale: false });
    const oreRows = await rowsOf(page, setId, oreTab.table);
    const cls = column(oreRows, "CLASS")!;
    const labels = column(oreRows, "LABEL")!.filter((l) => typeof l === "string" && l !== "");
    expect(cls.filter((c) => c === "ore").length).toBeGreaterThan(10);
    expect(labels.length).toBe(cls.filter((c) => c === "ore").length);
    // ORE_CLS of «Пробы» by the new conditions (Сб 0,8 < 1, Мрт 2 < 4: more ore samples than the first run made)
    const cls1 = column(await rowsOf(page, setId, samplesKey), "ORE_CLS")!;
    expect(cls1.length).toBe(cls0.length);
    expect(cls1.filter((c) => c === "ore").length).toBeGreaterThan(cls0.filter((c) => c === "ore").length);
    // 3D: the display «Рудные интервалы» switched on (one: it follows the replaced table), the intervals of the new
    // table and their labels drawn
    await expect.poll(async () => (await legendOf())?.labels ?? 0, { timeout: 60_000, intervals: [200, 400, 800] }).toBe(labels.length);
    await waitFrame(page);
    const shown = (await legendOf())!;
    console.log(`ore display: holes ${shown.holes}, intervals ${shown.intervals}, labels ${shown.labels} of ${labels.length}`);
    expect(shown.labels).toBe(labels.length);
    expect(shown.intervals).toBe(cls.filter((c) => c !== "frame").length); // «frame» hidden by default (решение 20)
    await ore.getByRole("button", { name: "Закрыть", exact: true }).last().click();
    await expect(ore).toHaveCount(0);
    await waitFrame(page);
    await page.screenshot({ path: shot("composites-ore-3d.png") });
    console.log(`ore: ${((Date.now() - started) / 1000).toFixed(1)} s`);

    // ---- 3. «Урезка»: the analysis; the threshold is written only by «Применить» -------------------------------
    const fieldsOf = async () => (await rowsOf(page, setId, samplesKey)).columns.map((c) => c.name);
    expect((await runCommand(page, "composite.topcut")).ok).toBe(true);
    const cut = toolWindow(page, "composites-topcut");
    await expect(cut).toBeVisible();
    await expect(cut.getByLabel("Поле", { exact: true })).toHaveValue("AU");
    await expect(cut.getByTestId("topcut-curves")).toBeVisible({ timeout: 30_000 });
    await expect(cut.getByTestId("topcut-deciles")).toBeVisible();
    await expect(cut.getByTestId("composites-param-percentile").getByRole("textbox")).toHaveValue("99");
    await cut.getByTestId("composites-param-percentile").getByRole("textbox").fill("99,5");
    await expect(cut.getByTestId("composites-summary").first()).toBeVisible({ timeout: 30_000 }); // the preview
    expect(await fieldsOf()).not.toContain("AU_CUT"); // the analysis and the preview write nothing
    await page.screenshot({ path: shot("composites-topcut.png") });
    await cut.getByTestId("composites-apply").click();
    await expect(cut.getByTestId("composites-job-status")).toHaveAttribute("data-status", "done", { timeout: LIMIT(60_000) });
    expect(await fieldsOf()).toEqual(expect.arrayContaining(["AU_CUT", "AU_CUT_F"]));
    const cutMade = await latest(page, setId, "topcut");
    expect(cutMade.params).toMatchObject({ table: samplesKey, field: "AU", percentile: 99.5 });
    await cut.getByRole("button", { name: "Закрыть", exact: true }).last().click();

    const seconds = (Date.now() - started) / 1000;
    console.log(`composites.spec: ${seconds.toFixed(1)} s after the demo`);
    expect(seconds).toBeLessThan(LIMIT(90));
  } finally {
    await close(app);
  }
});
