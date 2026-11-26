// Э21, подшаг 21.20 (ТЗ 8.11.4–8.11.5; ADR 0028 решения 22, 27, 29; ADR 0027 решение 8): the second half of the E2E of the pit optimisation, on its own app and project
// (the world — `pitWorld.ts`, the helpers — `pitKit.ts`; the scenarios are made through the commands of the registry, as the assistant makes them). Each scenario ≤ 60 s:
//  5. «Сравнить сценарии»: A against A — every difference is 0; A against B (another price, RF 0,7…1,3 against 0,5…1,2) — the shells by RF, «только в одном» is «—» (not 0), the
//     parameters that differ, the blocks only in one; a second scenario on the same fields without a prefix is refused (409 `pit_fields_taken`, «задайте префикс»).
//  6. The report of the resources: the field PIT works as a filter (`PIT >= 1` and `PIT <= n`, «Ниже рельефа» by the fraction) — the blocks and the tonnes of the report are the
//     table of pit-by-pit for the shells 1, 5, 8; the shortcut «Карьер» of the window — see the test «defect».
//  7. Ctrl+K finds the commands of the pit (the unavailable one says why); a restart of the app — the scenarios, the results, the fields and the report are in place.
//  8. «defect» (expected to fail until 21.21 decides): the shortcut «Карьер» `PIT <= n` of the report passes the blocks with PIT = 0 (outside every shell) too.
// Not covered: see the header of `pit.spec.ts`. A window of 1024 × 640 with the ribbon folded; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import { close, coreApi, launch, runCommand, setWindowSize, waitFrame } from "./helpers";
import { boot, choose, closeWindows, type Compared, LIMIT, makeScenario, NAME_A, NAME_B, near, option, report, RF_A, RF_B, say, scenario, scenarios, type Ids } from "./pitKit";
import { models, palette } from "./pitWorld";

let app: ElectronApplication;
let page: Page;
let dir: string;
let profile: string;
let ids: Ids;
let idA = 0;
let idB = 0;
/** The tonnes of the report with the shortcut «Карьер» `PIT <= 5` and of the table of pit-by-pit for the shell 5: the numbers of the test «defect». */
const defect = { label: 0, pit: 0 };

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(300_000));
  ({ app, page, dir, profile, ids } = await boot());
  idA = await makeScenario(page, ids, { name: NAME_A, price: "2000", rf: RF_A.join("; ").replace(/\./g, ","), run: true });
  expect((await scenario(page, idA)).has_result).toBe(true);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
  if (profile) fs.rmSync(profile, { recursive: true, force: true });
});

test("5. «Сравнить сценарии»: A against A — every difference is 0; A against B — the shells by RF, «только в одном» is «—», the blocks only in one", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  // the scenario B: the price 2200, RF 0,7…1,3, the same fields — so the prefix is asked for
  idB = await makeScenario(page, ids, { name: NAME_B, price: "2200", rf: RF_B.join("; ").replace(/\./g, ",") });
  // the fields PIT and ORE are A's: B is refused in words, with the way out (409 `pit_fields_taken`: «задайте префикс»); nothing is written
  const taken = await coreApi<{ code: string; message: string; hint: string }>(page, "POST", `/pit/scenarios/${idB}/run`, {});
  expect(taken.status).toBe(409);
  expect(taken.body.code).toBe("pit_fields_taken");
  expect(taken.body.hint).toContain("префикс");
  const refused = await say(page, "pit.run", { scenario_id: idB });
  expect(refused.ok).toBe(false);
  expect(refused.result).toContain("префикс");
  expect((await scenario(page, idB)).has_result).toBe(false);
  expect((await say(page, "pit.economics", { scenario_id: idB, prefix: "B_" })).ok).toBe(true);
  expect((await say(page, "pit.run", { scenario_id: idB })).ok).toBe(true);
  const b = await scenario(page, idB);
  expect(b.has_result).toBe(true);
  expect(b.meta.shells.map((s) => s.rf)).toEqual(RF_B);
  expect(b.meta.outputs.fields).toEqual(["B_PIT", "B_ORE"]);
  expect((await models(page)).find((m) => m.id === ids.model)!.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["PIT", "ORE", "B_PIT", "B_ORE"]));
  const a = await scenario(page, idA);

  // A against A in the window: the same scenario on both sides — every difference is 0, nothing is only in one, the parameters are the same
  await choose(page, "Сравнить", /Сравнить.*Карьер/);
  const win = page.getByTestId("pit-cmp-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.locator(`[data-testid="pit-cmp-a"] option[value="${idB}"]`)).toBeAttached({ timeout: 30_000 });
  await win.getByTestId("pit-cmp-a").selectOption(String(idA));
  await win.getByTestId("pit-cmp-b").selectOption(String(idA));
  await win.getByTestId("pit-cmp-run").click();
  await expect(win.getByTestId("pit-cmp-shells")).toBeVisible({ timeout: LIMIT(30_000) });
  const same = (await coreApi<Compared>(page, "POST", "/pit/compare", { a: idA, b: idA })).body;
  expect(same.same_grid).toBe(true);
  expect(same.params_diff).toEqual([]);
  expect(same.shells.map((s) => s.rf)).toEqual(RF_A);
  for (const s of same.shells) {
    expect(s.status).toBe("both");
    expect(Object.values(s.diff!)).toEqual([0, 0, 0, 0, 0, 0]);
  }
  expect(same.blocks).toEqual({ only_a: expect.objectContaining({ blocks: 0, tonnes: 0 }), only_b: expect.objectContaining({ blocks: 0, tonnes: 0 }) });
  await expect(win.getByTestId("pit-cmp-params-same")).toBeVisible();
  await expect(win.getByTestId("pit-cmp-grids-differ")).toHaveCount(0);
  for (const rf of RF_A) {
    const row = win.getByTestId(`pit-cmp-row-${rf}`);
    await expect(row).toHaveAttribute("data-status", "both");
    await expect(row.locator("td").last()).toHaveText("0");
  }
  for (const key of ["npv_best", "npv_worst", "cf"]) await expect(win.getByTestId(`pit-cmp-total-${key}`).locator("td").last()).toHaveText("0");
  await expect(win.getByTestId("pit-cmp-only-a")).toContainText("0");
  await expect(win.getByTestId("pit-cmp-only-b")).toContainText("0");

  // A against B: 0,5 and 0,6 are only in A, 1,3 only in B (a gap «—», not 0); the common RF 0,7…1,2 — the differences A − B; the parameters that differ
  await win.getByTestId("pit-cmp-b").selectOption(String(idB));
  await win.getByTestId("pit-cmp-run").click();
  await expect(win.getByTestId("pit-cmp-card-B")).toContainText(NAME_B, { timeout: LIMIT(30_000) });
  const diff = (await coreApi<Compared>(page, "POST", "/pit/compare", { a: idA, b: idB })).body;
  expect(diff.same_grid).toBe(true);
  expect(diff.shells.map((s) => [s.rf, s.status])).toEqual([[0.5, "only_a"], [0.6, "only_a"], [0.7, "both"], [0.8, "both"], [0.9, "both"], [1, "both"], [1.1, "both"], [1.2, "both"], [1.3, "only_b"]]);
  expect(diff.params_diff.map((d) => d.param)).toEqual(expect.arrayContaining(["elements.0.price", "prefix"]));
  expect(diff.params_diff.some((d) => d.param.startsWith("rf"))).toBe(true); // the list of RF differs (listed by place)
  expect(diff.params_diff.find((d) => d.param === "elements.0.price")).toMatchObject({ a: 2000, b: 2200 });
  const ra = await report(page, idA);
  const rb = await report(page, idB);
  for (const s of diff.shells) {
    const row = win.getByTestId(`pit-cmp-row-${s.rf}`);
    await expect(row).toHaveAttribute("data-status", s.status);
    if (s.status === "both") {
      // the side values are the cumulative values of the reports at that RF, the difference is A − B
      const sa = ra.shells[s.k_a! - 1];
      const sb = rb.shells[s.k_b! - 1];
      expect(sa.rf).toBe(s.rf);
      expect(sb.rf).toBe(s.rf);
      expect(s.a!.blocks).toBe(sa.cum.blocks);
      expect(s.b!.blocks).toBe(sb.cum.blocks);
      near(s.a!.ore_t!, sa.cum.ore_t, 1e-9);
      near(s.b!.cf!, sb.cum.cf, 1e-9);
      near(s.diff!.blocks!, sa.cum.blocks - sb.cum.blocks, 1e-12);
      near(s.diff!.waste_t!, sa.cum.waste_t - sb.cum.waste_t, 1e-9);
      near(s.diff!.cf!, sa.cum.cf - sb.cum.cf, 1e-9);
      near(s.diff!.npv_best!, sa.best.npv! - sb.best.npv!, 1e-9);
    } else {
      // the shell of one: the other side and the difference are a gap — «—», not 0
      expect(s.diff).toBeNull();
      expect(s.status === "only_a" ? s.b : s.a).toBeNull();
      await expect(row.locator("td").last()).toHaveText("—");
      await expect(row.locator("td").nth(s.status === "only_a" ? 4 : 3)).toHaveText("—");
    }
  }
  await expect(win.getByTestId("pit-cmp-params").locator('tr[data-param="elements.0.price"]')).toBeVisible();
  await expect(win.getByTestId("pit-cmp-summary")).toContainText("только в A: 2");
  await expect(win.getByTestId("pit-cmp-summary")).toContainText("только в B: 1");
  // the blocks of the final pits: the identity |B| − |A| = only B − only A (the parents of the last shells of the two results)
  expect(diff.blocks).not.toBeNull();
  const lastA = a.meta.shells[a.meta.shells.length - 1].blocks;
  const lastB = b.meta.shells[b.meta.shells.length - 1].blocks;
  expect(diff.blocks!.only_b.blocks - diff.blocks!.only_a.blocks).toBe(lastB - lastA);
  expect(diff.blocks!.only_a.blocks + diff.blocks!.only_b.blocks).toBeGreaterThan(0);
  await expect(win.getByTestId("pit-cmp-only-a")).toContainText(diff.blocks!.only_a.blocks.toLocaleString("ru-RU"));
  await expect(win.getByTestId("pit-cmp-only-b")).toContainText(diff.blocks!.only_b.blocks.toLocaleString("ru-RU"));
  await expect(win.getByTestId("pit-cmp-chart")).toBeVisible();
  await win.getByTestId("pit-cmp-close").click();
  await expect(win).toHaveCount(0);
  const took = Date.now() - started;
  console.log(`pit 5: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

type Resource = { id: number; name: string };
type ResourceRows = { rows: { total: boolean; kind: string; blocks: number; volume: number | null; tonnes: number | null }[] };

test("6. The report of the resources: the field PIT as a filter — the blocks and the tonnes are the table of pit-by-pit (the shells 1, 5, 8)", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const rep = await report(page, idA);
  const cum = (n: number) => ({ blocks: rep.shells[n - 1].cum.blocks, tonnes: rep.shells[n - 1].cum.ore_t + rep.shells[n - 1].cum.waste_t });
  const ofReport = async (name: string) => {
    const listed = (await coreApi<{ reports: Resource[] }>(page, "GET", "/resources/reports")).body.reports;
    const id = listed.find((r) => r.name === name)!.id;
    const all = (await coreApi<ResourceRows>(page, "GET", `/resources/reports/${id}?size=500`)).body.rows;
    const row = all.find((r) => r.kind === "all" && r.blocks > 0 && !r.total) ?? all.find((r) => r.kind === "all" && r.blocks > 0)!;
    return { id, blocks: row.blocks, tonnes: row.tonnes! };
  };
  // the pit of the shell n: PIT >= 1 (inside a shell) and PIT <= n, the part of a block under the relief — the tonnes of ΔC_1…ΔC_n of the core
  for (const n of [1, 5, 8]) {
    const restrict = { op: "and", items: [{ cond: { field: "PIT", op: ">=", value: 1 } }, { cond: { field: "PIT", op: "<=", value: n } }, { surface: { id: ids.terrain, side: "below", by: "frac" } }] };
    const made = await runCommand(page, "resources.report", { model_id: ids.model, elements: "AU:г/т", cutoffs: "без борта", density: "DENS", restrict: JSON.stringify(restrict), save: true, name: `Оболочка ${n}` });
    expect(made.ok, made.result).toBe(true);
    const r = await ofReport(`Оболочка ${n}`);
    expect(r.blocks, `the shell ${n}`).toBe(cum(n).blocks);
    near(r.tonnes, cum(n).tonnes, 1e-9);
  }
  const inTree = page.getByTestId(/^tree-resource-report-\d+$/);
  await expect(inTree.first()).toBeAttached({ timeout: 30_000 });

  // the shortcut «Карьер» of the window (the field PIT up to the number) and «Ниже рельефа» by the fraction: the window of Э20 on the field of Э21
  await palette(page, "Отчёт по ресурсам");
  const win = page.getByTestId("res-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("res-model").selectOption(String(ids.model));
  await expect(win.getByTestId("res-element-AU")).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("res-element-AU").check();
  await win.getByTestId("res-unit-AU").fill("г/т");
  await win.getByTestId("res-cutoffs").fill("без борта");
  await win.getByTestId("res-density-field-on").check();
  await win.getByTestId("res-density-field").selectOption("DENS");
  await win.getByTestId("res-restrict-on").uncheck(); // the window keeps the form of the last command: its restrictor (the shell 8) is not this report's
  await win.getByTestId("res-pit-on").check();
  await win.getByTestId("res-pit-n").fill("5");
  await win.getByTestId("res-below-on").check();
  await win.getByTestId("res-below-surface").selectOption(String(ids.terrain));
  await win.getByTestId("res-below-frac").check(); // the fraction of a block under the relief (the core of the pit takes it so), not the centre
  await win.getByTestId("res-name").fill("Ярлык Карьер 5");
  await win.getByTestId("res-save").click();
  await expect(win.getByTestId("res-summary")).toContainText("записан в проект", { timeout: LIMIT(60_000) });
  await closeWindows(page);
  const label = await ofReport("Ярлык Карьер 5");
  defect.label = label.tonnes;
  defect.pit = cum(5).tonnes;
  expect(label.tonnes).toBeGreaterThanOrEqual(cum(5).tonnes); // the blocks outside every shell (PIT = 0) are in it too: see the test «defect»
  const took = Date.now() - started;
  console.log(`pit 6: ${(took / 1000).toFixed(1)} s; the shortcut «Карьер» 5: ${Math.round(label.tonnes)} t against ${Math.round(defect.pit)} t of the shell 5`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("7. Ctrl+K finds the commands of the pit; a restart of the app — the scenarios, the results, the fields and the report are in place", async () => {
  test.setTimeout(LIMIT(180_000));
  const started = Date.now();
  const commands: [string, RegExp][] = [
    ["Новый сценарий", /Новый сценарий.*Карьер/],
    ["Экономика", /Экономика.*Карьер/],
    ["Породы", /Породы.*Карьер/],
    ["Углы бортов", /Углы бортов.*Карьер/],
    ["Рельеф", /Рельеф.*Карьер/],
    ["Календарь", /Календарь.*Карьер/],
    ["Проверить шаблон", /Проверить шаблон.*Карьер/],
    ["Запустить", /Запустить.*Карьер/],
    ["Pit-by-pit", /Pit-by-pit.*Карьер/],
    ["Поверхности", /Поверхности.*Карьер/],
    ["Каркас карьера", /Каркас карьера.*Карьер/],
    ["Сравнить", /Сравнить.*Карьер/],
  ];
  await runCommand(page, "object.select", { kind: "pit_scenario", id: idA });
  for (const [query, title] of commands) {
    const o = await option(page, query, title);
    expect(o.disabled, `${query}: ${o.hint}`).toBe(false);
  }
  const before = { a: await scenario(page, idA), b: await scenario(page, idB), repA: await report(page, idA), repB: await report(page, idB) };
  const fields = (await models(page)).find((m) => m.id === ids.model)!.fields.map((f) => f.name);
  expect(fields).toEqual(expect.arrayContaining(["PIT", "ORE", "B_PIT", "B_ORE"]));
  const reports = (await coreApi<{ reports: Resource[] }>(page, "GET", "/resources/reports")).body.reports.map((r) => r.name).sort();
  expect(reports).toEqual(["Оболочка 1", "Оболочка 5", "Оболочка 8", "Ярлык Карьер 5"].sort());

  // the restart: the same profile — the last project opens at the start
  await close(app);
  ({ app, page } = await launch({ BF_E2E: "1" }, profile));
  await setWindowSize(app, 1024, 640);
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await expect(page.getByTestId("project-selector")).toContainText("Карьер Э21", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  expect((await scenarios(page)).map((s) => s.name).sort()).toEqual([NAME_A, NAME_B]);
  const after = { a: await scenario(page, idA), b: await scenario(page, idB), repA: await report(page, idA), repB: await report(page, idB) };
  for (const k of ["a", "b"] as const) {
    expect(after[k].has_result).toBe(true);
    expect(after[k].params_changed).toBe(false);
    expect(after[k].version).toBe(before[k].version);
    expect(after[k].meta.shells).toEqual(before[k].meta.shells);
    expect(after[k].meta.outputs).toEqual(before[k].meta.outputs);
  }
  expect(after.repA.table).toEqual(before.repA.table);
  expect(after.repB.series).toEqual(before.repB.series);
  expect((await models(page)).find((m) => m.id === ids.model)!.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["PIT", "ORE", "B_PIT", "B_ORE"]));
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-pit-scenario-${idA}`)).toBeAttached({ timeout: 30_000 });
  await expect(tree.getByTestId(`tree-pit-scenario-${idB}`)).toBeAttached({ timeout: 30_000 });
  await expect(tree.getByTestId(`tree-pit-scenario-${idA}`)).not.toContainText("нет результата");

  // the panel of the result opens from the stored data: the table is the text of the core as before the restart
  expect((await runCommand(page, "pit.report", { scenario_id: idB })).ok).toBe(true);
  const panel = page.getByTestId("pit-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByTestId("pit-panel-title")).toContainText(NAME_B);
  await expect(panel.getByTestId("pit-table")).toHaveAttribute("data-rows", String(RF_B.length));
  await expect(panel.getByTestId("pit-table").locator('tr[data-row="7"] td[data-col="best_npv"]')).toHaveText(before.repB.table.rows[6].cells.best_npv.text!);
  // the saved report of the resources is there too (the tree of the project follows the events)
  await expect(page.getByTestId(/^tree-resource-report-\d+$/).first()).toBeAttached({ timeout: 30_000 });
  const took = Date.now() - started;
  console.log(`pit 7: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

// Fixed in 21.21: PIT = 0 is «outside every shell» (ADR 0028 decision 22), so the shortcut «Карьер» and `pit.show` take `PIT >= 1` and `PIT <= n`.
test("the shortcut «Карьер» of the report (`PIT >= 1` and `PIT <= n`) gives the tonnes of the shell n", async () => {
  near(defect.label, defect.pit, 1e-9);
});
