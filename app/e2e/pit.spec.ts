// Э21, подшаг 21.20 (ТЗ 8.11 «Оптимизация карьера», 8.11.5; ADR 0028): the pit optimisation end to end on a synthetic project made through the core (no real data,
// no mocks — only the real answers of the core): a block model 30 × 30 × 12 (cells 10 × 10 × 5 m; AU of an ellipsoidal body with a few gaps, the density DENS) and a
// rolling relief as a DTM built from points (`pitWorld.ts`; the helpers — `pitKit.ts`). One app, one project, the scenarios in a row (each ≤ 60 s, the waits are on
// events and states, never on the pixels; every number on the screen is compared with the answer of the core, NPV and the sums also with their own formula).
// This file — the way of one scenario; `pitCompare.spec.ts` — the comparison, the report of the resources, Ctrl+K and the restart.
//  1. Ctrl+K «Новый сценарий» → «Экономика» (the price, the sales, the royalty, c_m0, RF 0,5…1,2 step 0,1) → «Породы» → «Углы бортов» 45° → «Проверить шаблон» (the
//     offsets, K, the error of the angle; K = 3 — the warning «увеличьте K») → «Календарь»; «Запустить» is unavailable with the hint of the core until everything is set.
//  2. «Запустить»: the stages of the job (the events of the stream of the core), the result (8 nested shells, closed), the fields PIT and ORE, the same run — the same numbers.
//  3. The panel «Pit-by-pit»: the table is the text of the core, the chart, NPV of the best and of the worst case (the best — by the formula of ТЗ 8.11.4; r = 0 — the sum
//     of CF), «3D» of a shell (the quick restrictor of the model), the section «Карьер → Сценарии» of the tree and «Свойства».
//  4. «Поверхности» (the DTMs of the shells: nested, in the tree) → «Каркас карьера» (closed, the volume of the core).
//  5. «defect» (expected to fail until 21.21 decides): the shortcut `PIT <= n` passes the blocks with PIT = 0 (outside every shell) too.
// Not covered (no point in the interface yet / out of the scope of the E2E): the domains of the angles by a category field (the synthetic model has no category field — the
// core tests do), the rock types by a field or by rules (one type «Все породы»; the core tests do), the restrictor by a filter, the cancel of a run (the core tests: nothing
// is left), the MineLib files and McLaughlin (the core tests and the benches), «Перестроить» after an edit of the grade field (`test_pit_lineage.py`), the pixels of the
// 3D and of the chart (not compared). A window of 1024 × 640 with the ribbon folded; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import { close, coreApi, runCommand, shot } from "./helpers";
import { lastJob, lastJobId } from "./estimationWorld";
import { boot, CALENDAR, closeWindows, coreCheck, LIMIT, NAME_A, NBSP, near, npv, option, raiseDock, report, RF_A, ru, scenario, scenarios, type Ids } from "./pitKit";
import { models, palette, relief, wireframes } from "./pitWorld";

let app: ElectronApplication;
let page: Page;
let dir: string;
let profile: string;
let ids: Ids;
let idA = 0;
/** What the shortcut `PIT <= n` passes (the window of the model) and what the shell n has: the numbers of the test «defect». */
const defect = { shown: 0, shell: 0, shellBlocks: 0 };

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(300_000));
  ({ app, page, dir, profile, ids } = await boot());
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
  if (profile) fs.rmSync(profile, { recursive: true, force: true });
});

test("1. «Новый сценарий» → «Экономика» → «Породы» → «Углы бортов» → «Проверить шаблон» → «Календарь»; «Запустить» is unavailable until it is all set", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Новый сценарий");
  const win = page.getByTestId("pit-scenario-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("pit-name").fill(NAME_A);
  await win.getByTestId("pit-model").selectOption(String(ids.model));
  await win.getByTestId("pit-terrain").selectOption(String(ids.terrain));
  await win.getByTestId("pit-apply").click();
  // the next step of a new scenario: the window «Экономика»
  const econ = page.getByTestId("pit-econ-window");
  await expect(econ).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => (await scenarios(page)).map((s) => s.name), { timeout: 30_000 }).toEqual([NAME_A]);
  idA = (await scenarios(page))[0].id;
  const fresh = await scenario(page, idA);
  expect(fresh.has_result).toBe(false);
  expect(fresh.params).toMatchObject({ model_id: ids.model, terrain_id: ids.terrain });

  // «Запустить» is unavailable: the hint is the first thing the core says is missing (`validate`)
  const missing = async () => (await coreApi<{ ready: boolean; missing: { message: string }[] }>(page, "POST", "/pit/validate", { params: (await scenario(page, idA)).params })).body;
  const first = await missing();
  expect(first.ready).toBe(false);
  const noPrice = await option(page, "Запустить", /Запустить/);
  expect(noPrice.disabled).toBe(true);
  expect(noPrice.hint).toContain(first.missing[0].message);

  // «Экономика»: the element AU in g/t with the price, the sales and the royalty; c_m0, c_гл; RF 0,5…1,2 step 0,1 → 8 shells
  await econ.getByTestId("pit-el-AU").check();
  await econ.getByTestId("pit-el-unit-AU").selectOption("g/t");
  await econ.getByTestId("pit-el-price-AU").fill("2000");
  await econ.getByTestId("pit-el-sales-AU").fill("5");
  await econ.getByTestId("pit-el-roy-AU").fill("0,02");
  await econ.getByTestId("pit-c-m0").fill("2,5");
  await econ.getByTestId("pit-c-gl").fill("0,05");
  await econ.getByTestId("pit-rf-from").fill("0,5");
  await econ.getByTestId("pit-rf-to").fill("1,2");
  await econ.getByTestId("pit-rf-step").fill("0,1");
  await expect(econ.getByTestId("pit-rf-count")).toContainText("Оболочек: 8 из");
  await econ.getByTestId("pit-econ-apply").click();
  // the prices are in: what is still missing is the rock types, and the window says it in the words of the core
  await expect.poll(async () => (await scenario(page, idA)).params.rf, { timeout: 30_000 }).toEqual(RF_A);
  const rest = await missing();
  expect(rest.ready).toBe(false);
  expect(rest.missing[0].message).not.toBe(first.missing[0].message);
  await expect(econ.getByTestId("pit-ready")).toContainText(rest.missing[0].message.replace(/\.$/, ""), { timeout: 30_000 });
  expect((await option(page, "Запустить", /Запустить/)).disabled).toBe(true);
  await closeWindows(page);
  // the same button on the ribbon («Карьер» → «Расчёт») and in «Свойства» of the scenario: unavailable, the hint is the word of the core
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const ribbon = page.getByLabel("Лента");
  await ribbon.getByRole("tab", { name: "Карьер" }).click();
  const runButton = ribbon.getByRole("button", { name: "Запустить", exact: true });
  await expect(runButton).toBeVisible({ timeout: 10_000 });
  await expect(runButton).toBeDisabled();
  expect((await runButton.getAttribute("title")) ?? "").toContain(rest.missing[0].message);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const scenarioRow = page.getByRole("tree", { name: "Разделы и объекты проекта" }).getByTestId(`tree-pit-scenario-${idA}`);
  await scenarioRow.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await scenarioRow.click();
  await expect(page.getByTestId("props-pit-run")).toBeDisabled({ timeout: 30_000 });
  expect((await page.getByTestId("props-pit-run").getAttribute("title")) ?? "").toContain(rest.missing[0].message);

  // «Породы»: one type «Все породы» (the model has no category field): c_p 20 $/t, the recovery of AU 0,9
  await palette(page, "Породы");
  const rocks = page.getByTestId("pit-rocks-window");
  await expect(rocks).toBeVisible({ timeout: 30_000 });
  await rocks.getByTestId("pit-cp-Все породы").fill("20");
  await rocks.getByTestId("pit-rec-Все породы-AU").fill("0,9");
  await rocks.getByTestId("pit-rocks-apply").click();
  await expect(rocks.getByTestId("pit-ready")).toContainText("готов к запуску", { timeout: 30_000 });
  expect((await missing()).ready).toBe(true);
  await closeWindows(page);

  // «Углы бортов»: 45° and K = 3 (too few levels) → «Применить и проверить»: the error of the angle is above the tolerance — «увеличьте K»
  await palette(page, "Углы бортов");
  const slopes = page.getByTestId("pit-slopes-window");
  await expect(slopes).toBeVisible({ timeout: 30_000 });
  await slopes.getByTestId("pit-sectors-angle-0").fill("45");
  await slopes.getByTestId("pit-k-own").click();
  await slopes.getByTestId("pit-k").fill("3");
  await slopes.getByTestId("pit-slopes-check").click();
  const check = page.getByTestId("pit-check-window");
  await expect(check.getByTestId("pit-check-summary")).toContainText("K = 3", { timeout: LIMIT(60_000) });
  const coarse = await coreCheck(page, idA);
  expect(coarse.k).toBe(3);
  expect(coarse.domains[0].error.max).toBeGreaterThan(2.5);
  expect(coarse.warnings.map((w) => w.code)).toContain("angle_error");
  await expect(check.getByTestId("pit-check-warning-angle_error")).toBeVisible();
  await expect(check.getByTestId("pit-check-domain-0")).toHaveAttribute("data-over", "true");
  await expect(check.getByTestId("pit-check-domain-0")).toContainText(`${coarse.domains[0].n_offsets}`.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP));
  await closeWindows(page);

  // K auto: «Проверить шаблон» — the offsets, K and the error of the angle are the numbers of the core
  await palette(page, "Углы бортов");
  await slopes.getByTestId("pit-k-auto").click();
  await slopes.getByTestId("pit-slopes-apply").click();
  await expect.poll(async () => (await scenario(page, idA)).params, { timeout: 30_000 }).toMatchObject({ k: null });
  await closeWindows(page);
  await palette(page, "Проверить шаблон");
  await expect(check.getByTestId("pit-check-summary")).toContainText("подобран автоматически", { timeout: LIMIT(60_000) });
  const fine = await coreCheck(page, idA);
  expect(fine.k_auto).toBe(true);
  expect(fine.k).toBeGreaterThanOrEqual(8);
  expect(fine.domains[0].n_offsets).toBeGreaterThan(coarse.domains[0].n_offsets);
  expect(fine.domains[0].error.max).toBeLessThanOrEqual(fine.tolerance); // 8.11.2: the error of the angle is inside the tolerance
  expect(fine.warnings).toEqual([]);
  await expect(check.getByTestId("pit-check-summary")).toContainText(`K = ${fine.k}`);
  await expect(check.getByTestId("pit-check-ok")).toBeVisible();
  await expect(check.getByTestId("pit-check-warning-angle_error")).toHaveCount(0);
  const row = check.getByTestId("pit-check-domain-0");
  await expect(row).toContainText(`${fine.domains[0].n_offsets}`);
  await expect(row).toContainText(`${ru(fine.domains[0].error.max, 2).replace(/0$/, "")}°`);
  await expect(row).toHaveAttribute("data-over", "false");
  await expect(check.getByTestId("pit-check-chart")).toHaveAttribute("data-domains", "1");
  await closeWindows(page);

  // «Календарь»: Q_р, Q_гм, r — shown, not an input of the shells
  await palette(page, "Календарь");
  const cal = page.getByTestId("pit-calendar-window");
  await expect(cal).toBeVisible({ timeout: 30_000 });
  await cal.getByTestId("pit-q-ore").fill("100000");
  await cal.getByTestId("pit-q-mass").fill("250000");
  await cal.getByTestId("pit-rate").fill("8");
  await cal.getByTestId("pit-calendar-apply").click();
  await expect.poll(async () => (await scenario(page, idA)).params.calendar, { timeout: 30_000 }).toEqual(CALENDAR);
  await closeWindows(page);

  // everything is set: «Запустить» is available
  expect((await missing()).ready).toBe(true);
  const ready = await option(page, "Запустить", /Запустить/);
  expect(ready.disabled, ready.hint).toBe(false);
  const took = Date.now() - started;
  console.log(`pit 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. «Запустить»: the stages, the result of the core (nested shells), the fields PIT and ORE; the same run again — the same numbers", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const before = await lastJobId(page, "pit.run");
  // the course of the job as the interface gets it: the events `job` of the stream of the core (the stage and the progress of the job), read by a reader of its own
  await page.evaluate((after) => {
    type Course = { stages: string[]; progress: number; ended: boolean; stop: () => void };
    const w = window as unknown as { __pitCourse?: Course };
    const ctrl = new AbortController();
    const course: Course = { stages: [], progress: 0, ended: false, stop: () => ctrl.abort() };
    w.__pitCourse = course;
    const c = window.bf!.core()!;
    void (async () => {
      const r = await fetch(`${c.baseUrl}/api/v1/events`, { headers: { "X-BF-Token": c.token }, signal: ctrl.signal });
      const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        const blocks = buf.split("\n\n");
        buf = blocks.pop() ?? "";
        for (const b of blocks) {
          if (!/^event: job$/m.test(b)) continue;
          const data = /^data: (.*)$/m.exec(b);
          const j = data ? (JSON.parse(data[1]) as { id: number; status?: string; message?: string; progress?: number }) : null;
          if (!j || j.id <= after) continue;
          if (j.message && course.stages[course.stages.length - 1] !== j.message) course.stages.push(j.message);
          if (typeof j.progress === "number") course.progress = Math.max(course.progress, j.progress);
          if (j.status === "done") course.ended = true;
        }
      }
    })().catch(() => undefined);
  }, before);
  await page.waitForTimeout(300); // the stream is open
  await palette(page, "Запустить");
  const job = await lastJob(page, "pit.run", LIMIT(90_000), before);
  await expect.poll(() => page.evaluate(() => (window as unknown as { __pitCourse: { ended: boolean } }).__pitCourse.ended), { timeout: 10_000 }).toBe(true);
  const { stages, progress } = await page.evaluate(() => {
    const c = (window as unknown as { __pitCourse: { stages: string[]; progress: number; stop: () => void } }).__pitCourse;
    c.stop();
    return { stages: c.stages, progress: c.progress };
  });
  // the job is quick, so not every stage is caught between two events: the stages caught are those of the job and come in its order, the end is «Готово» at 1
  const order = ["Чтение", "Экономика", "Шаблон", "Граф", "RF_max", "Оболочки", "Отчёт", "Запись", "Готово"];
  const seen = stages.map((s) => order.findIndex((o) => s.startsWith(o)));
  expect(stages.length).toBeGreaterThan(0);
  expect(seen.every((i) => i >= 0), JSON.stringify(stages)).toBe(true);
  expect(seen, JSON.stringify(stages)).toEqual([...seen].sort((a, b) => a - b));
  expect(stages[stages.length - 1]).toBe("Готово");
  expect(progress).toBe(1);
  const seconds = (job.result as { seconds: Record<string, number> }).seconds;
  expect(Object.keys(seconds)).toEqual(expect.arrayContaining(["read", "econ", "template", "graph", "rf_max", "shells", "report", "write", "total"]));

  // the result of the core: 8 shells at the RF of the scenario, nested (more blocks with every RF), closed; the scenario holds it
  const s = await scenario(page, idA);
  expect(s.has_result).toBe(true);
  expect(s.params_changed).toBe(false);
  expect(s.meta.shells.map((x) => x.rf)).toEqual(RF_A);
  expect(s.meta.shells.map((x) => x.k)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  for (let i = 1; i < 8; i++) {
    expect(s.meta.shells[i].blocks).toBeGreaterThan(s.meta.shells[i - 1].blocks);
    expect(s.meta.shells[i].value).toBeGreaterThanOrEqual(s.meta.shells[i - 1].value); // the value of the optimum grows with the price
  }
  expect(s.meta.control).toMatchObject({ closed: true, nested: true });
  expect(s.meta.template.k).toBe((await coreCheck(page, idA)).k);
  expect((job.result as { shells: unknown }).shells).toEqual(s.meta.shells);
  // the fields PIT and ORE are in the model (the interface follows the events)
  expect(s.meta.outputs.fields).toEqual(["PIT", "ORE"]);
  const model = (await models(page)).find((m) => m.id === ids.model)!;
  expect(model.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["PIT", "ORE"]));
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-pit-scenario-${idA}`)).toBeAttached({ timeout: 30_000 });

  // the same parameters again (`pit.run` through the registry — the job followed to its end): the same numbers, bit for bit
  const again = await runCommand(page, "pit.run", { scenario_id: idA });
  expect(again.ok, again.result).toBe(true);
  const s2 = await scenario(page, idA);
  expect(s2.version).toBeGreaterThan(s.version);
  expect(s2.meta.shells).toEqual(s.meta.shells);
  expect(s2.meta.outputs).toEqual(s.meta.outputs);

  const took = Date.now() - started;
  console.log(`pit 2: ${(took / 1000).toFixed(1)} s; stages caught: ${stages.join(" | ")}`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. «Pit-by-pit»: the table is the text of the core, the chart, NPV of the best and of the worst case; «3D»; the tree «Карьер → Сценарии» and «Свойства»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Pit-by-pit");
  const panel = page.getByTestId("pit-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await raiseDock(page, 220);
  const rep = await report(page, idA);
  expect(rep.npv_ready).toBe(true);
  expect(rep.shells.length).toBe(8);
  await expect(panel.getByTestId("pit-panel-title")).toContainText(NAME_A);
  await expect(panel.getByTestId("pit-table")).toHaveAttribute("data-rows", "8");
  await expect(panel.getByTestId("pit-table-count")).toContainText("Оболочек: 8");

  // the table is the text of the core: every cell of the table of the answer is on the screen as it is (a gap is «—», never 0)
  const table = panel.getByTestId("pit-table");
  for (const r of rep.table.rows) {
    for (const c of rep.table.columns) {
      const cell = r.cells[c.key];
      if (c.key === "k") {
        await expect(table.locator(`tr[data-row="${r.k}"] td[data-col="k"]`)).toHaveText(String(r.k)); // the number of the shell is the row's, not a cell of the core
        continue;
      }
      const want = !cell || cell.value === null ? "—" : cell.text ? cell.text : ru(cell.value, c.decimals); // a cell without a text is printed from its value, a gap is «—»
      await expect(table.locator(`tr[data-row="${r.k}"] td[data-col="${c.key}"]`), `shell ${r.k}, ${c.key}`).toHaveText(want);
    }
  }
  expect(rep.table.columns.map((c) => c.key)).toEqual(expect.arrayContaining(["k", "rf", "ore_t", "waste_t", "cf", "best_npv", "worst_npv"]));

  // the sums are the sums: Σ ΔC_k = the last cumulative = the totals; the shells are the shells of the run
  const sum = (key: "blocks" | "ore_t" | "waste_t" | "cf") => rep.shells.reduce((a, s) => a + s[key], 0);
  near(sum("ore_t"), rep.totals.ore_t, 1e-9);
  near(sum("waste_t"), rep.totals.waste_t, 1e-9);
  near(sum("cf"), rep.totals.cf, 1e-9);
  near(rep.shells[7].cum.ore_t, rep.totals.ore_t, 1e-9);
  expect(sum("blocks")).toBe(rep.totals.blocks);
  const meta = (await scenario(page, idA)).meta.shells;
  expect(rep.shells.map((s) => s.cum.blocks)).toEqual(meta.map((s) => s.blocks));
  for (let i = 0; i < 8; i++) {
    near(rep.shells[i].cum.ore_t, meta[i].ore_t, 1e-9);
    near(rep.shells[i].cum.waste_t, meta[i].waste_t, 1e-9);
  }

  // NPV of the best case = the formula of ТЗ 8.11.4 over the shells of the report; the worst case is not above it
  expect(rep.calendar).toEqual(CALENDAR);
  for (let n = 1; n <= 8; n++) {
    const parts = rep.shells.slice(0, n).map((s) => ({ ore: s.ore_t, waste: s.waste_t, cf: s.cf }));
    near(rep.series.npv_best[n - 1]!, npv(parts, { q_ore: CALENDAR.q_ore, q_mass: CALENDAR.q_mass, rate: CALENDAR.rate }), 1e-9);
    near(rep.shells[n - 1].best.npv!, rep.series.npv_best[n - 1]!, 1e-12);
    near(rep.shells[n - 1].worst.npv!, rep.series.npv_worst[n - 1]!, 1e-12);
    expect(rep.series.npv_worst[n - 1]!).toBeLessThanOrEqual(rep.series.npv_best[n - 1]! + 1e-6 * Math.abs(rep.series.npv_best[n - 1]!));
  }

  // the chart: a point per shell, the legend of the series
  await panel.getByTestId("pit-tab-chart").click();
  await expect(panel.getByTestId("pit-chart")).toHaveAttribute("data-points", "8");
  await expect(panel.getByTestId("pit-legend")).toBeVisible();
  expect(await panel.getByTestId("pit-legend").locator("[data-series]").evaluateAll((els) => els.map((e) => e.getAttribute("data-series")))).toEqual(expect.arrayContaining(["worst", "best", "cf"]));
  await page.screenshot({ path: shot("pit-chart.png") });
  await panel.getByTestId("pit-tab-table").click();

  // the calendar of the panel is a view: r = 0 — NPV of the best case is the sum of CF (the limit of the formula), the scenario is not changed
  const version = (await scenario(page, idA)).version;
  await panel.getByTestId("pit-rate").fill("0");
  await panel.getByTestId("pit-calendar-apply").click();
  await expect(panel.getByTestId("pit-calendar-reset")).toBeEnabled({ timeout: 30_000 });
  const zero = await report(page, idA, { ...CALENDAR, rate: 0 });
  for (let n = 1; n <= 8; n++) near(zero.series.npv_best[n - 1]!, rep.shells.slice(0, n).reduce((a, s) => a + s.cf, 0), 1e-9);
  await expect(table.locator('tr[data-row="8"] td[data-col="best_npv"]')).toHaveText(zero.table.rows[7].cells.best_npv.text!);
  expect((await scenario(page, idA)).version).toBe(version);
  expect((await scenario(page, idA)).params.calendar.rate).toBe(0.08);
  await panel.getByTestId("pit-calendar-reset").click();
  await expect(table.locator('tr[data-row="8"] td[data-col="best_npv"]')).toHaveText(rep.table.rows[7].cells.best_npv.text!, { timeout: 30_000 });

  // «3D» of the shell 5: the quick restrictor `PIT >= 1` and `PIT <= 5` of the model (PIT = 0 is outside every shell); its count is read in the window of the model
  const n = 5;
  await table.locator(`tr[data-row="${n}"] td button`).evaluate((el) => el.scrollIntoView({ block: "center" }));
  await panel.getByTestId(`pit-show-${n}`).click();
  await expect.poll(async () => (await runCommand(page, "blocks.settings", { model_id: ids.model })).ok, { timeout: 30_000 }).toBe(true);
  const modelWindow = page.getByTestId("model-window");
  await expect(modelWindow).toBeVisible({ timeout: 30_000 });
  await modelWindow.getByTestId("model-tab-quick").click();
  await expect(modelWindow.getByTestId("quick-row-0-field")).toHaveValue("PIT", { timeout: 30_000 });
  await expect(modelWindow.getByTestId("quick-row-0-op")).toHaveValue(">=");
  await expect(modelWindow.getByTestId("quick-row-1-field")).toHaveValue("PIT");
  await expect(modelWindow.getByTestId("quick-row-1-op")).toHaveValue("<=");
  const passedOf = async () => Number(/прошло(\d+)из/.exec((await modelWindow.getByTestId("quick-passed").innerText()).replace(/\s/g, ""))?.[1]);
  defect.shown = await passedOf();
  defect.shell = n;
  defect.shellBlocks = meta[n - 1].blocks;
  // exactly the blocks of the shell n (the nodes of the shell of the core)
  await expect.poll(passedOf, { timeout: 30_000 }).toBe(defect.shellBlocks);
  expect((await runCommand(page, "blocks.quick", { model_id: ids.model, clear: true })).ok).toBe(true);
  await closeWindows(page);

  // the tree: «Карьер → Сценарии» — the scenario by name; a click — «Свойства»
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  const treeRow = tree.getByTestId(`tree-pit-scenario-${idA}`);
  await treeRow.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await expect(treeRow).toContainText(NAME_A);
  await treeRow.click();
  await expect(page.getByTestId("props-pit-scenario")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("props-pit-name")).toContainText(NAME_A);
  await expect(page.getByTestId("props-pit-facts")).toBeVisible();
  await expect(page.getByTestId("props-pit-noresult")).toHaveCount(0);
  await expect(page.getByTestId("props-pit-run")).toBeEnabled();
  await expect(page.getByTestId("props-pit-changed")).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`pit 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("4. «Поверхности» (the DTMs of the shells: nested, in the tree) → «Каркас карьера» (closed, the volume of the core)", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const before = (await wireframes(page)).length;
  await palette(page, "Поверхности");
  const win = page.getByTestId("pit-surf-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("pit-surf-count")).toContainText("Выбрано оболочек: 8 из 8", { timeout: 30_000 }); // every shell is picked at first (№ 124)
  await win.getByTestId("pit-surf-run").click();
  await expect(win.getByTestId("pit-surf-result")).toBeVisible({ timeout: LIMIT(60_000) });
  for (let k = 1; k <= 8; k++) await expect(win.getByTestId(`pit-surf-wire-${k}`)).toBeVisible();
  // the DTMs are wireframes of the project (the folder of the scenario), the tree has them; the core named them by RF
  const all = await wireframes(page);
  expect(all.length).toBe(before + 8);
  const made = all.filter((w) => w.name.startsWith(`${NAME_A} — RF `));
  expect(made.map((w) => w.name)).toEqual(RF_A.map((rf) => `${NAME_A} — RF ${rf.toLocaleString("ru-RU", { minimumFractionDigits: 1 })}`));
  expect(made.every((w) => w.dtm !== null && w.folder === `Карьер/${NAME_A}`)).toBe(true);
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  for (const w of made) await expect(tree.getByTestId(`tree-dtm-${w.id}`), w.name).toBeAttached({ timeout: 30_000 });
  // the shells are nested: the DTM of a bigger RF is nowhere above the one of a smaller RF; where nothing is mined it is the relief; where the ore is — the bottom of a block
  const z = async (id: number, x: number, y: number) => {
    const r = await coreApi<{ hit: { point: number[] } | null }>(page, "POST", "/wireframes/pick", { origin: [x, y, 400], direction: [0, 0, -1], wireframe_ids: [id] });
    expect(r.body.hit, `the DTM ${id} at ${x} ${y}`).not.toBeNull();
    return r.body.hit!.point[2];
  };
  const centre = [];
  for (const w of made) centre.push(await z(w.id, 5155, 7155));
  for (let i = 1; i < 8; i++) expect(centre[i]).toBeLessThanOrEqual(centre[i - 1] + 1e-6);
  expect(centre[7]).toBeLessThan(centre[0]);
  for (const c of centre) near(((c - 100) / 5) % 1, 0, 1e-6); // the bottom of a block of the model (100 + 5 j)
  for (const w of made) expect(Math.abs((await z(w.id, 5005, 7005)) - relief(5005, 7005))).toBeLessThan(0.05); // a column outside every pit is the relief
  const areas = made.map((w) => w.stats.area);
  for (let i = 1; i < 8; i++) expect(areas[i]).toBeGreaterThanOrEqual(areas[i - 1] - 1e-6);
  // a second run replaces them in place: no new wireframes
  await win.getByTestId("pit-surf-run").click();
  await expect(win.getByTestId("pit-surf-wire-1")).toContainText("(заменён)", { timeout: LIMIT(60_000) });
  expect((await wireframes(page)).length).toBe(before + 8);
  // in the tree under the scenario
  await closeWindows(page);
  const row = tree.getByTestId(`tree-pit-scenario-${idA}`);
  await row.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await row.getByRole("button").first().click();
  for (const w of made) await expect(tree.getByTestId(`tree-pit-${idA}-wireframe-${w.id}`), w.name).toBeAttached({ timeout: 30_000 });
  await row.getByRole("button").first().click();

  // «Каркас карьера»: the shell 6 (RF 1,0); the solid is closed, its volume is the number of the core
  const jobBefore = await lastJobId(page, "pit.solid");
  await palette(page, "Каркас карьера");
  const solid = page.getByTestId("pit-solid-window");
  await expect(solid).toBeVisible({ timeout: 30_000 });
  await solid.getByTestId("pit-solid-shell").selectOption("6");
  await solid.getByTestId("pit-solid-run").click();
  await expect(solid.getByTestId("pit-solid-result")).toBeVisible({ timeout: LIMIT(60_000) });
  const job = (await lastJob(page, "pit.solid", LIMIT(60_000), jobBefore)).result as { shell: number; rf: number; wireframe_id: number; volume: number; volume_blocks: number; volume_diff_pct: number; parts: number; closed: boolean };
  expect(job).toMatchObject({ shell: 6, rf: 1, closed: true });
  await expect(solid.getByTestId("pit-solid-r-closed")).toHaveText("да");
  expect(job.parts).toBeGreaterThanOrEqual(1);
  const wf = (await wireframes(page)).find((w) => w.id === job.wireframe_id)!;
  expect(wf.dtm).toBeNull(); // a solid, not a DTM
  expect(wf.stats).toMatchObject({ closed: true, degenerate: 0, nonmanifold_edges: 0, open_edges: 0 });
  near(wf.stats.volume!, job.volume, 1e-9);
  expect(job.volume).toBeGreaterThan(0);
  // the volume of the blocks: the parents of the shell 6 in full (10 × 10 × 5 m); the solid is between the relief and the surface on the grid of the centres of the columns —
  // the two differ (8,4 % here), the difference is shown and is not an error
  const meta6 = (await scenario(page, idA)).meta.shells[5].blocks;
  expect(job.volume_blocks).toBe(meta6 * 500);
  near(job.volume_diff_pct, (100 * Math.abs(job.volume_blocks - job.volume)) / job.volume_blocks, 1e-2 / 8);
  expect(job.volume_diff_pct).toBeLessThan(20);
  await expect(solid.getByTestId("pit-solid-r-volume")).toContainText(ru(job.volume, 0).slice(0, 3));
  await expect(solid.getByTestId("pit-solid-r-blocks")).toContainText(ru(job.volume_blocks, 0).slice(0, 3));
  await expect(tree.getByTestId(`tree-wireframe-${job.wireframe_id}`)).toBeAttached({ timeout: 30_000 });
  const took = Date.now() - started;
  console.log(`pit 4: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

// Fixed in 21.21: PIT = 0 is «outside every shell» (ADR 0028 decision 22), so the shortcut «Карьер» and `pit.show` take `PIT >= 1` and `PIT <= n`.
test("«3D» of the shell n (`PIT >= 1` and `PIT <= n`) passes exactly the blocks of the shell n", async () => {
  expect(defect.shown).toBe(defect.shellBlocks);
});
