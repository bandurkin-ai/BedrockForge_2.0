// Э18б, подшаг 18.36 (ТЗ 8.8.1, Д-23, Д-38; ADR 0024 «Оценка содержаний» §9): the simulation end to end on the synthetic project of
// `estimationWorld.ts` (36 holes, a block model 12 × 12 × 6 with DOMAIN and the density DENS, the models of variogram of AU: in normal scores and
// one indicator per threshold — all through the core). One app, one project, the scenarios in a row (each ≤ 60 s, the waits — on events and states):
//  1. SGS: Ctrl+K «SGS» → the wizard `simulation-wizard`, steps 1–6 (3 realizations, the seed) → «Выполнить моделирование» → progress → done
//     → «Сводка реализаций» (`sum-table`: r1…r3 and «Все вместе») → «Постобработка» → the field AU_SGS_MEAN in «Свойства полей» → the model coloured
//     by it → the row of the tree `tree-sim-row-<id>`.
//  2. SIS by thresholds «0,7; 1; 1,5»: «Подобрать по порогам» takes the three indicator models → run → summary.
//  3. Co-SGS: the secondary field DENS, ρ by the samples → the journal «ρ = … (по N парам проба–узел)».
// A window of 1024 × 640 with the ribbon folded; the hooks and the colours are read from the state, never from the pixels of software WebGL;
// the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame } from "./helpers";
import { blocksList, build, FIELD, lastJob, lastJobId, palette, THRESHOLDS, type Ids } from "./estimationWorld";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);
const N_REAL = 3;

type Sim = { id: number; name: string; kind: string; model_id: number; version: number; stale: boolean; last_run: { realizations: number; nodes: number } | null };
const simsList = async (page: Page) => (await coreApi<{ simulations: Sim[] }>(page, "GET", "/geostat/simulations")).body.simulations;
const blocksHook = <T>(page: Page, fn: string, ...args: unknown[]) =>
  page.evaluate(([f, a]) => {
    const h = (window as unknown as { __bfBlocks?: Record<string, (...x: unknown[]) => unknown> }).__bfBlocks;
    return (h ? h[f as string](...(a as unknown[])) : null) as never;
  }, [fn, args] as const) as Promise<T | null>;

let app: ElectronApplication;
let page: Page;
let dir: string;
let ids: Ids;

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(300_000));
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-simulation-"));
  ({ app, page } = await launch({ BF_E2E: "1" }));
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Моделирование Э18б");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Моделирование Э18б", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  ids = await build(page, dir, true);
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-block-${ids.model}`)).toBeAttached({ timeout: 30_000 });
  console.log(`simulation: the project is built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
});

/** Steps 1 and 2 (the estimation's): the block model and its domain, the set, the table, the field AU, the domain of the samples. */
async function target(wiz: Locator, kind: "sgs" | "sis" | "cosgs"): Promise<void> {
  const tab = (n: number) => wiz.getByTestId(`sim-step-${n}-tab`);
  await expect(wiz.getByTestId("sim-step-1")).toBeVisible();
  await wiz.getByTestId(`sim-kind-${kind}`).check();
  await wiz.getByTestId("est-model").selectOption(String(ids.model));
  await expect(wiz.getByTestId("est-model-info")).toContainText("Блоков");
  await wiz.getByTestId("est-domain-field").selectOption("DOMAIN");
  await wiz.getByTestId("est-label").fill("1");
  await tab(2).click();
  await expect(wiz.getByTestId("est-set")).toHaveValue(String(ids.set), { timeout: 30_000 });
  await expect(wiz.getByTestId("est-table")).toHaveValue(ids.table, { timeout: 30_000 });
  await expect(wiz.getByTestId("est-field")).toHaveValue(FIELD, { timeout: 30_000 });
  await wiz.getByTestId("est-domain").selectOption("field");
  await wiz.getByTestId("est-domain-col").selectOption("DOM");
  await expect(wiz.getByTestId("est-domain-values").getByRole("checkbox", { name: "1", exact: true })).toBeChecked({ timeout: 30_000 });
  await expect(wiz.getByTestId("est-samples-summary")).toContainText("Проб:", { timeout: 30_000 });
}

/** Steps 4–6: the search from the model, the realizations, «Выполнить моделирование» → the job → the journal. Returns the id of the row. */
async function realizeAndRun(wiz: Locator, nReal: number, seed: string): Promise<{ id: number; journal: string }> {
  const tab = (n: number) => wiz.getByTestId(`sim-step-${n}-tab`);
  await tab(4).click();
  await expect(wiz.getByTestId("sim-search-model")).toContainText("Углы и радиусы ядро возьмёт", { timeout: 30_000 });
  await tab(5).click();
  await wiz.getByTestId("sim-n-real").fill(String(nReal));
  await wiz.getByTestId("sim-seed").fill(seed);
  await tab(6).click();
  await expect(wiz.getByTestId("sim-step-6")).toBeVisible();
  await expect(wiz.getByTestId("sim-summary")).toContainText(String(nReal));
  const before = await lastJobId(page, "simulate.run");
  await wiz.getByTestId("sim-run-go").click();
  await expect(wiz.getByTestId("sim-run-progress").or(wiz.getByTestId("sim-run-done"))).toBeVisible({ timeout: LIMIT(30_000) });
  const job = await lastJob(page, "simulate.run", LIMIT(90_000), before);
  expect(job.status).toBe("done");
  const done = wiz.getByTestId("sim-run-done");
  await expect(done).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(wiz.getByTestId("sim-run-error")).toHaveCount(0);
  const rows = await simsList(page);
  const mine = rows.find((r) => r.id === Number((job.result as { simulation_id: number }).simulation_id))!;
  expect(mine.last_run).toMatchObject({ realizations: nReal });
  return { id: mine.id, journal: await done.innerText() };
}

/** The title-bar «Закрыть» of the floating window that holds `testId`. */
async function closeWindow(testId: string): Promise<void> {
  const win = page.getByRole("dialog").filter({ has: page.getByTestId(testId) });
  await win.getByRole("button", { name: "Закрыть", exact: true }).first().click();
  await expect(win).toHaveCount(0);
}

async function closeWizard(wiz: Locator): Promise<void> {
  await wiz.getByTestId("sim-close").click();
  await expect(wiz).toHaveCount(0);
}

test("1. SGS: the wizard → the run → the summary of the realizations → the post-processing AU_SGS_MEAN → the model coloured by it → the row of the tree", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "SGS", "SGS");
  const wiz = page.getByTestId("simulation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await target(wiz, "sgs");

  // 3. the model in normal scores (a model in the units of the field does not fit SGS: Д-38)
  await wiz.getByTestId("sim-step-3-tab").click();
  await expect(wiz.getByTestId("sim-vario")).toBeVisible();
  await expect(wiz.getByTestId("sim-variogram").locator(`option[value="${ids.nscore}"]`)).toBeAttached({ timeout: 30_000 });
  await expect(wiz.getByTestId("sim-variogram").locator(`option[value="${ids.vario}"]`)).toHaveAttribute("data-usable", "false");
  await wiz.getByTestId("sim-variogram").selectOption(String(ids.nscore));

  const { id, journal } = await realizeAndRun(wiz, N_REAL, "777");
  expect(journal).toContain("Моделирование");
  await page.screenshot({ path: shot("simulation-sgs-done.png") });

  // the row is in the tree of «Геостатистика → Моделирование»
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-sim-row-${id}`)).toBeAttached({ timeout: 30_000 });

  // «Сводка реализаций…»: r1…r3 and «Все вместе»
  await wiz.getByTestId("sim-open-summary").click();
  const sum = page.getByTestId("simulation-summary");
  await expect(sum).toBeVisible({ timeout: 30_000 });
  const table = sum.getByTestId("sum-table");
  await expect(table).toBeVisible({ timeout: LIMIT(30_000) });
  for (let r = 1; r <= N_REAL; r++) await expect(table.getByTestId(`sum-row-r${r}`)).toBeVisible();
  await expect(table.getByTestId("sum-row-all")).toContainText("Все вместе");
  await expect(table.getByTestId(`sum-row-r${N_REAL + 1}`)).toHaveCount(0);
  await page.screenshot({ path: shot("simulation-summary.png") });

  // «Постобработка»: the fields of the statistics in the block model
  await sum.getByTestId("sum-post").click();
  const post = page.getByTestId("simulation-post");
  await expect(post).toBeVisible({ timeout: 30_000 });
  await expect(post.getByTestId("post-names")).toContainText("AU_SGS_MEAN", { timeout: 30_000 });
  await post.getByTestId("post-go").click();
  await expect(post.getByTestId("post-done")).toBeVisible({ timeout: LIMIT(60_000) });
  await expect(post.getByTestId("post-done")).toContainText("AU_SGS_MEAN");
  const model = (await blocksList(page)).find((m) => m.id === ids.model)!;
  expect(model.fields.map((f) => f.name)).toEqual(expect.arrayContaining(["AU_SGS_MEAN", "AU_SGS_STD"]));
  await page.screenshot({ path: shot("simulation-post.png") });
  await closeWindow("simulation-post");
  await closeWindow("simulation-summary");
  await closeWizard(wiz);

  // «Свойства полей» of the model: the field AU_SGS_MEAN is there, with its source
  const opened = await runCommand(page, "blocks.field.props", { model_id: ids.model });
  expect(opened.ok, opened.result).toBe(true);
  const fieldsWin = page.getByTestId("fields-window");
  await expect(fieldsWin).toBeVisible({ timeout: 30_000 });
  await expect(fieldsWin.getByTestId("props-row-AU_SGS_MEAN")).toBeVisible({ timeout: 30_000 });
  await expect(fieldsWin.getByTestId("props-source-AU_SGS_MEAN")).not.toHaveText("");
  await fieldsWin.getByTestId("fields-close").click();
  await expect(fieldsWin).toHaveCount(0);

  // the model in 3D coloured by AU_SGS_MEAN
  expect((await runCommand(page, "blocks.show", { model_id: ids.model })).ok).toBe(true);
  await expect.poll(async () => ((await blocksHook<unknown[]>(page, "shown")) ?? []).length, { timeout: LIMIT(30_000) }).toBe(1);
  const colored = await runCommand(page, "blocks.style", { model_id: ids.model, field: "AU_SGS_MEAN", kind: "scale" });
  expect(colored.ok, colored.result).toBe(true);
  await expect.poll(async () => JSON.stringify((await blocksHook<Record<string, unknown>>(page, "styles"))?.[String(ids.model)] ?? {}), { timeout: LIMIT(30_000) }).toContain("AU_SGS_MEAN");
  await page.screenshot({ path: shot("simulation-colored.png") });

  const took = Date.now() - started;
  console.log(`simulation 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. SIS by the thresholds «0,7; 1; 1,5»: «Подобрать по порогам» → the run → the summary", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "SIS", "SIS");
  const wiz = page.getByTestId("simulation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await target(wiz, "sis");

  await wiz.getByTestId("sim-step-3-tab").click();
  await expect(wiz.getByTestId("sim-sis")).toBeVisible();
  await wiz.getByTestId("sim-by-thresholds").check();
  await wiz.getByTestId("sim-thresholds").fill("0,7; 1; 1,5");
  await expect(wiz.getByTestId("sim-variogram-2")).toBeVisible();
  await expect(wiz.getByTestId("sim-variogram-0").locator(`option[value="${ids.indicators![0]}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("sim-match").click();
  for (let i = 0; i < THRESHOLDS.length; i++) await expect(wiz.getByTestId(`sim-variogram-${i}`)).toHaveValue(String(ids.indicators![i]));

  const { id } = await realizeAndRun(wiz, 2, "555");
  await page.screenshot({ path: shot("simulation-sis-done.png") });
  await wiz.getByTestId("sim-open-summary").click();
  const sum = page.getByTestId("simulation-summary");
  await expect(sum.getByTestId("sum-table")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(sum.getByTestId("sum-row-r1")).toBeVisible();
  await expect(sum.getByTestId("sum-row-r2")).toBeVisible();
  const row = (await simsList(page)).find((r) => r.id === id)!;
  expect(row.kind).toBe("sis");
  await closeWindow("simulation-summary");
  await closeWizard(wiz);

  const took = Date.now() - started;
  console.log(`simulation 2: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. co-SGS: the secondary field DENS, ρ by the samples → «ρ = … (по N парам)» in the journal", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Ко-SGS", "Ко-SGS");
  const wiz = page.getByTestId("simulation-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await target(wiz, "cosgs");

  await wiz.getByTestId("sim-step-3-tab").click();
  await wiz.getByTestId("sim-variogram").selectOption(String(ids.nscore));
  await expect(wiz.getByTestId("sim-secondary")).toBeVisible();
  await expect(wiz.getByTestId("sim-sec-field").locator('option[value="DENS"]')).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("sim-sec-field").selectOption("DENS");
  await wiz.getByTestId("sim-rho-samples").check();

  const { journal } = await realizeAndRun(wiz, 2, "999");
  expect(journal).toMatch(/ρ = [-−]?[\d\s ,]+ \(по [\d\s ]+ парам/);
  expect(journal).toContain("DENS");
  await page.screenshot({ path: shot("simulation-cosgs-done.png") });
  expect(await simsList(page)).toHaveLength(3);
  await closeWizard(wiz);

  const took = Date.now() - started;
  console.log(`simulation 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});
