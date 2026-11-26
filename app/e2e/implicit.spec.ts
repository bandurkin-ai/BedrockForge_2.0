// Э19, подшаг 19.28 (ТЗ 8.9.4, 7.35 Д-15, Д-32, Д-33, Д-34 ч. 1; ADR 0026 «Неявное моделирование»): the implicit modelling end to end on the small
// scene of `tools/gen/implicit.py` (100 holes through folded layers, a sphere INT; the committed fixtures `tests/fixtures/implicit/`, imported
// through the core: no demo, no real data — every answer is the core's own). One app, one project, the scenarios in a row (each ≤ 60 s, the
// waits — on events and states, the numbers — from the state, never from the pixels of software WebGL):
//  1. The wizard «Новая модель»: the boundary by the extent and the step → «Домены литологии» (the set, the table and the field of the codes, two
//     groups by age) → «Интерполяция» → the summary → «Сохранить и построить» → the job `implicit.build` is done.
//  2. The bodies of the model: the tree (`tree-implicit-*`: «Данные», «Поверхности», «Тела»), the outputs are ordinary wireframes in 3D, the points of a
//     group in 3D (`window.__bfImplicit`), «Свойства» (`props-implicit-*`).
//  3. «Проверка по скважинам»: the table of the deviations (`chk-*`, `window.__bfImplicitCheck.check()`), a click on a row, «Показать в 3D»,
//     «Карта опоры» (`sup-*`).
//  4. «Сверка со скважинами»: the matrix and the share of the agreement (`cmp-*`, `window.__bfImplicitCheck.compare()`), a mismatch → «Данные по скважинам».
//  5. «Модель на скважины»: the new table of the set (`fm-*`), a name that is taken → the suggested one.
//  6. ТД15-8: «Сохранить как тренд» in «Вариографии» → a row in «Тренды» → the trend in the step «Интерполяция» of the wizard of RBF → the model is built with
//     it, the ellipsoid of the trend in 3D; a structural trend (the measurements of a table) → the discs in 3D (`window.__bfTrendDiscs`) → it is in the list
//     of the wizard («структурный»).
//  7. Ctrl+K finds the commands `implicit.*` and `drillholes.from_model` by their Russian words.
// A window of 1024 × 640 with the ribbon folded; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame, type CoreJob } from "./helpers";
import { BOX, build, CODES, FIXTURES, STEP_H, type Ids } from "./implicitWorld";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);

type Group = { id: number | null; name: string; type: string };
type Output = { wireframe_id: number | null; kind: string; group: number | null; name: string };
type Row = { id: number; name: string; version: number; state: string; stale: boolean; groups: Group[]; outputs: Output[]; stats: { points: number | null; patches: number | null; seconds: number | null } | null };
type Stats = { points: { key: string; count: number; origin: { x: number; y: number; z: number }; bbox: number[] | null }[]; ellipsoids: { key: string; centre: number[]; ranges: number[]; angles: number[] }[] };
type CheckHook = { modelId: number | null; tab: string; phase: string; total: number | null; rows: number; over: number | null; maxResidual: number | null; highlight: number | null; picked: number | null; shown: boolean; points: unknown };
type CompareHook = { modelId: number | null; phase: string; share: number | null; mismatches: number | null; rows: number; picked: number | null; error: string | null };
type FromHook = { busy: boolean; rows: number | null; table: string | null; holes: number | null; error: string | null; suggested: string | null };
type SupportHook = { modelId: number | null; phase: string; shownId: number | null; layer: unknown; counts: unknown; shares: Record<string, number | null> };
type Discs = { id: number; count: number; skipped: number; radius: number; color: string; opacity: number }[];

const g = <T>(page: Page, name: string, fn: string): Promise<T | null> =>
  page.evaluate(([n, f]) => {
    const h = (window as unknown as Record<string, Record<string, () => unknown> | undefined>)[n];
    return (h && h[f] ? h[f]() : null) as never;
  }, [name, fn] as const) as Promise<T | null>;
const stats = (page: Page) => g<Stats>(page, "__bfImplicit", "stats");
const checkHook = (page: Page) => g<CheckHook>(page, "__bfImplicitCheck", "check");
const supportHook = (page: Page) => g<SupportHook>(page, "__bfImplicitCheck", "support");
const compareHook = (page: Page) => g<CompareHook>(page, "__bfImplicitCheck", "compare");
const fromHook = (page: Page) => g<FromHook>(page, "__bfImplicitCheck", "fromModel");
const discs = (page: Page) => g<Discs>(page, "__bfTrendDiscs", "discs");
const layers = (page: Page) => page.evaluate(() => window.__bfScene!.state().layers);
const rowsList = async (page: Page) => (await coreApi<{ models: Row[] }>(page, "GET", "/implicit/models")).body.models;

/** The last job of `kind` once it is over (the jobs list of the core; the waits are polls of the state). */
async function lastJob(page: Page, kind: string, timeout: number, after = 0): Promise<CoreJob> {
  let job: CoreJob | undefined;
  try {
    await expect
      .poll(
        async () => {
          const jobs = (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind && j.id > after);
          job = jobs.sort((a, b) => b.id - a.id)[0];
          return job?.status ?? "none";
        },
        { timeout, intervals: [200, 400, 800] },
      )
      .toMatch(/^(done|failed|cancelled)$/);
  } catch (e) {
    // what the window and the core say when no job comes (the log of a CI run is all there is to read a failure on another machine)
    const all = (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs").catch(() => null))?.body.jobs ?? [];
    const jobsLine = all.slice(0, 8).map((j) => `${j.id}:${j.kind}:${j.status}`).join(", ");
    const models = (await rowsList(page).catch(() => [])).map((m) => `${m.id}:${m.name}:${m.state}`).join(", ");
    const alerts = await page.locator('[role="alert"], [role="status"], [data-testid*="toast"], [data-testid*="issue"]').allInnerTexts().catch(() => []);
    throw new Error(`${kind} after ${after}: no end in ${timeout} ms. jobs: [${jobsLine}]; models: [${models}]; messages: ${JSON.stringify(alerts).slice(0, 600)}\n${String(e)}`);
  }
  expect(job!.status, JSON.stringify(job!.error)).toBe("done");
  return job!;
}
const lastJobId = async (page: Page, kind: string) => Math.max(0, ...(await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind === kind).map((j) => j.id));

/** Ctrl+K → the command by its title → Enter (the palette closes). */
async function palette(page: Page, query: string, title = query): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(title);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

let app: ElectronApplication;
let page: Page;
let dir: string;
let ids: Ids;
let model: Row;

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(240_000));
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-implicit-"));
  ({ app, page } = await launch({ BF_E2E: "1" }));
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Неявные Э19");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Неявные Э19", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  ids = await build(page, dir);
  console.log(`implicit: the project is built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
});

test("1. the wizard «Новая модель»: the boundary and the step → the domains → the interpolation → «Сохранить и построить»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  await palette(page, "Новая модель");
  const wiz = page.getByTestId("implicit-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  const tab = (n: number) => wiz.getByTestId(`imp-step-${n}-tab`);

  // 1. the boundary by the extent, the step h
  await expect(wiz.getByTestId("imp-step-1")).toBeVisible();
  await wiz.getByTestId("imp-name").fill("Литология Э19");
  await expect(wiz.getByTestId("imp-boundary-box")).toBeChecked();
  for (let i = 0; i < 6; i++) await wiz.getByTestId(`imp-box-${i}`).fill(String(BOX[i]));
  await wiz.getByTestId("imp-h").fill(String(STEP_H));
  // the grid of the step typed (not «по умолчанию»): the cells along the axes and in all
  await expect(wiz.getByTestId("imp-grid")).toHaveText(/^Сетка \d+ × \d+ × \d+ = [\d\s]+ ячеек, шаг 20 м, уровней 2\.$/);

  // 2. the domains: the set, the table and the field of the codes, two groups by age (the youngest first)
  await tab(2).click();
  await expect(wiz.getByTestId("imp-step-2")).toBeVisible();
  await wiz.getByTestId("imp-set").selectOption(String(ids.set));
  await expect(wiz.getByTestId("imp-table").locator(`option[value="${ids.litho.table}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-table").selectOption(ids.litho.table);
  await expect(wiz.getByTestId("imp-field").locator('option[value="CODE"]')).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-field").selectOption("CODE");
  await expect(wiz.getByTestId("imp-codes-note")).toContainText("Кодов в поле", { timeout: 30_000 });
  await wiz.getByTestId("imp-dmax").fill("30");
  await wiz.getByTestId("imp-step").fill("10"); // Δ_т: fewer points, faster
  for (const [i, code] of CODES.entries()) {
    await wiz.getByTestId("imp-group-add").click();
    await wiz.getByTestId(`imp-group-name-${i}`).fill(code === "INT" ? "Интрузия" : "Песчаник");
    await wiz.getByTestId(`imp-code-add-${i}`).selectOption(code);
    await expect(wiz.getByTestId(`imp-code-${i}-${code}`)).toBeVisible();
  }
  await expect(wiz.getByTestId("imp-other")).toBeVisible(); // «Прочее» — the rest of the boundary

  // 3. the interpolation: the kernel and the smoothing of the core by default
  await tab(3).click();
  await expect(wiz.getByTestId("imp-step-3")).toBeVisible();
  await expect(wiz.getByTestId("imp-kernel")).toHaveValue("linear");
  await expect(wiz.getByTestId("imp-smoothing-manual")).toBeChecked();
  await expect(wiz.getByTestId("imp-trend-mode")).toHaveValue("none");

  // 4. the summary; «Сохранить и построить»
  await tab(4).click();
  await expect(wiz.getByTestId("imp-step-4")).toBeVisible();
  await expect(wiz.getByTestId("imp-sum-name")).toHaveText("Литология Э19");
  await expect(wiz.getByTestId("imp-sum-groups")).toContainText("Интрузия");
  await expect(wiz.getByTestId("imp-sum-groups")).toContainText("Песчаник");
  await page.screenshot({ path: shot("implicit-wizard.png") });
  const before = await lastJobId(page, "implicit.build");
  await wiz.getByTestId("imp-save-build").click();
  const job = await lastJob(page, "implicit.build", LIMIT(90_000), before);
  expect(job.status).toBe("done");
  await expect(wiz.getByTestId("imp-saved")).toContainText("Сохранено: «Литология Э19»", { timeout: LIMIT(30_000) });
  const rows = await rowsList(page);
  expect(rows).toHaveLength(1);
  model = rows[0];
  expect(model.state).toBe("ready");
  expect(model.stats?.points ?? 0).toBeGreaterThan(0);
  console.log(`implicit build: ${model.stats?.points} points, ${model.stats?.patches} patches, ${model.stats?.seconds} s`);
  await wiz.getByTestId("imp-close").click();
  await expect(wiz).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`implicit 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. the bodies of the model: the tree, the wireframes of the outputs and the data points in 3D, «Свойства»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  const section = tree.getByTestId("tree-implicit");
  await expect(section).toBeAttached({ timeout: 30_000 });
  await expect(section).toHaveAttribute("data-models", "1");
  const row = tree.getByTestId(`tree-implicit-${model.id}`);
  await expect(row).toBeVisible();
  // the branches: «Данные» (the groups), «Поверхности» and «Тела» (the wireframes the build wrote)
  const surfaces = model.outputs.filter((o) => o.kind === "surface");
  const bodies = model.outputs.filter((o) => o.kind === "body");
  console.log(`implicit outputs: ${surfaces.length} surfaces, ${bodies.length} bodies; groups ${model.groups.map((x) => `${x.id}:${x.name}`).join(", ")}`);
  expect(surfaces.length).toBeGreaterThan(0);
  expect(bodies.length).toBeGreaterThan(0);
  await expect(tree.getByTestId(`tree-implicit-${model.id}-data`)).toContainText("2");
  await expect(tree.getByTestId(`tree-implicit-${model.id}-surface`)).toContainText(String(surfaces.length));
  await expect(tree.getByTestId(`tree-implicit-${model.id}-body`)).toContainText(String(bodies.length));
  for (const o of model.outputs) await expect(tree.getByTestId(`tree-implicit-out-${o.wireframe_id}`)).toBeAttached();

  // the outputs are ordinary wireframes of the project, shown in 3D
  const wires = (await coreApi<{ wireframes: { id: number; name: string }[] }>(page, "GET", "/wireframes")).body.wireframes;
  for (const o of model.outputs) expect(wires.some((w) => w.id === o.wireframe_id)).toBe(true);
  await expect.poll(async () => (await layers(page)).filter((k) => k.startsWith("wf:")).sort(), { timeout: LIMIT(30_000) }).toEqual(model.outputs.map((o) => `wf:${o.wireframe_id}`).sort());
  const scene = await page.evaluate(() => window.__bfScene!.state().wireframes);
  expect(scene.layers).toBe(model.outputs.length);
  expect(scene.triangles).toBeGreaterThan(0);
  await page.screenshot({ path: shot("implicit-bodies.png") });

  // the data points of a group: the check box of the group → a layer of the scene (the points of the core, float32 from an integer origin)
  const group = model.groups.find((x) => x.name === "Интрузия")!;
  const gRow = tree.getByTestId(`tree-implicit-${model.id}-group-${group.id}`);
  await gRow.scrollIntoViewIfNeeded();
  await gRow.getByRole("checkbox").check();
  const key = `implicit:points:${model.id}:${group.id}`;
  await expect.poll(async () => (await stats(page))?.points.map((p) => p.key) ?? [], { timeout: LIMIT(30_000) }).toContain(key);
  const layer = (await stats(page))!.points.find((p) => p.key === key)!;
  expect(layer.count).toBeGreaterThan(0);
  expect(layer.bbox).toHaveLength(6);
  expect(await layers(page)).toContain(key);
  console.log(`points of «${group.name}»: ${layer.count} in 3D, origin ${JSON.stringify(layer.origin)}`);
  await gRow.getByRole("checkbox").uncheck();
  await expect.poll(async () => (await stats(page))?.points.length ?? -1, { timeout: 10_000 }).toBe(0);

  // «Свойства»: a double click on the model
  await row.dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toBeVisible({ timeout: 30_000 });
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  await expect(props).toHaveAttribute("data-model-state", "ready");
  await expect(props.getByTestId("props-implicit-settings")).toBeVisible();
  await expect(props.getByTestId("props-implicit-result")).toHaveAttribute("data-built", "yes");
  await expect(props.getByTestId("props-implicit-groups").getByRole("row")).toHaveCount(model.groups.length + 1); // the head and the groups with points («Прочее» has none)
  await expect(props.getByTestId("props-implicit-update")).toBeEnabled();
  await expect(props.getByTestId("props-implicit-to_wireframe")).toBeEnabled();
  await expect(props.getByTestId("props-implicit-stale")).toHaveCount(0);
  await page.screenshot({ path: shot("implicit-props.png") });

  const took = Date.now() - started;
  console.log(`implicit 2: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. «Проверка по скважинам»: the table of the deviations, a row, «Показать в 3D», «Карта опоры»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "implicit.check", { model_id: model.id });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("implicit-check");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("chk-table")).toBeVisible({ timeout: LIMIT(30_000) });
  await expect(win.getByTestId("chk-model")).toHaveValue(String(model.id));
  const hook = (await checkHook(page))!;
  expect(hook).toMatchObject({ modelId: model.id, tab: "table", phase: "ok" });
  expect(hook.total!).toBeGreaterThan(0);
  expect(hook.rows).toBeGreaterThan(0);
  expect(hook.maxResidual).not.toBeNull(); // a number of the core, never a made-up 0 for a gap
  // the line of the table is a point of the data: its value of the model, the data, the residual
  const first = win.getByTestId("chk-table").locator("tbody tr").first();
  await expect(first).toBeVisible();
  expect(await win.getByTestId("chk-table").locator("tbody tr").count()).toBe(hook.rows);
  await expect(win.getByTestId("chk-n")).not.toHaveText("");
  await expect(win.getByTestId("chk-max")).not.toHaveText("—");
  console.log(`check: ${hook.total} points, max residual ${hook.maxResidual}, beyond the threshold ${hook.over}, the table ${hook.rows} rows`);
  // a click on the row goes to the point: the points of the page are put in 3D (coloured by the residual) and the view goes to the point
  await first.click();
  await expect.poll(async () => (await checkHook(page))!.picked, { timeout: 10_000 }).not.toBeNull();
  await expect.poll(async () => (await checkHook(page))!.shown, { timeout: 10_000 }).toBe(true);
  await expect.poll(async () => (await layers(page)).includes("implicit-check"), { timeout: LIMIT(30_000) }).toBe(true);
  // «Скрыть в 3D» takes them out, «Показать в 3D» puts them back
  await win.getByTestId("chk-3d").click();
  await expect.poll(async () => (await layers(page)).includes("implicit-check"), { timeout: 10_000 }).toBe(false);
  await win.getByTestId("chk-3d").click();
  await expect.poll(async () => (await checkHook(page))!.shown, { timeout: 10_000 }).toBe(true);
  await expect.poll(async () => (await layers(page)).includes("implicit-check"), { timeout: LIMIT(30_000) }).toBe(true);
  await page.screenshot({ path: shot("implicit-check.png") });

  // «Карта опоры»: the surface painted by the distance to the data, the share of the area beyond D_оп
  await win.getByTestId("chk-tab-support").click();
  await expect(win.getByTestId("sup-table")).toBeVisible({ timeout: LIMIT(30_000) });
  const surfaceIds = model.outputs.filter((o) => o.kind === "surface").map((o) => o.wireframe_id as number);
  for (const id of surfaceIds) await expect(win.getByTestId(`sup-share-${id}`)).not.toHaveText("");
  await win.getByTestId("sup-surface").selectOption(String(surfaceIds[0]));
  await win.getByTestId("sup-show").click();
  await expect.poll(async () => (await supportHook(page))!.shownId, { timeout: LIMIT(30_000) }).toBe(surfaceIds[0]);
  await expect.poll(async () => (await layers(page)).includes("implicit-support"), { timeout: LIMIT(30_000) }).toBe(true);
  const sup = (await supportHook(page))!;
  expect(sup.phase).toBe("ok");
  for (const id of surfaceIds) expect(sup.shares[String(id)]).toBeGreaterThanOrEqual(0); // a share of the area: 0…1
  await page.screenshot({ path: shot("implicit-support.png") });
  await win.getByTestId("chk-close").click();
  await expect(win).toHaveCount(0);
  await expect.poll(async () => (await layers(page)).filter((k) => k === "implicit-check" || k === "implicit-support"), { timeout: 10_000 }).toEqual([]); // a window that goes takes its points out

  const took = Date.now() - started;
  console.log(`implicit 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("4. «Сверка со скважинами»: the matrix, the share of the agreement, a mismatch → «Данные по скважинам»", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "implicit.compare_holes", { model_id: model.id });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("implicit-compare");
  await expect(win).toBeVisible({ timeout: 30_000 });
  // the set, the table and the field are those of the data of the model
  await expect(win.getByTestId("cmp-set")).toHaveValue(String(ids.set), { timeout: 30_000 });
  await expect(win.getByTestId("cmp-table")).toHaveValue(ids.litho.table, { timeout: 30_000 });
  await expect(win.getByTestId("cmp-field")).toHaveValue("CODE", { timeout: 30_000 });
  await win.getByTestId("cmp-run").click();
  await expect(win.getByTestId("cmp-matrix")).toBeVisible({ timeout: LIMIT(60_000) });
  await expect.poll(async () => (await compareHook(page))!.phase, { timeout: LIMIT(60_000) }).toBe("ok");
  const hook = (await compareHook(page))!;
  expect(hook).toMatchObject({ modelId: model.id, phase: "ok", error: null });
  // the model has the domains of INT and SS only (the rest is «Прочее», with no codes): the length of these two codes of the table is what agrees
  const lines = fs.readFileSync(path.join(FIXTURES, "lithology.csv"), "utf8").trim().split("\n").slice(1).map((l) => l.split(","));
  const total = lines.reduce((a, [, from, to]) => a + (Number(to) - Number(from)), 0);
  const own = lines.filter(([, , , code]) => CODES.includes(code)).reduce((a, [, from, to]) => a + (Number(to) - Number(from)), 0);
  console.log(`compare: the share of the agreement ${(hook.share! * 100).toFixed(1)} % (the length of ${CODES.join(", ")} of the table: ${((own / total) * 100).toFixed(1)} %), mismatches ${hook.mismatches}`);
  expect(hook.share!).toBeGreaterThan(0);
  expect(hook.share!).toBeLessThanOrEqual(1);
  expect(Math.abs(hook.share! - own / total)).toBeLessThan(0.1);
  await expect(win.getByTestId("cmp-fact-share")).toBeVisible();
  await win.getByTestId("cmp-metric-count").click().catch(() => undefined);
  expect(await win.getByTestId("cmp-matrix").locator("tbody tr").count()).toBeGreaterThan(1);
  await page.screenshot({ path: shot("implicit-compare.png") });
  if ((hook.mismatches ?? 0) > 0) {
    await expect(win.getByTestId("cmp-list")).toBeVisible();
    await win.getByTestId("cmp-goto-0").click();
    const data = page.getByRole("dialog", { name: /Данные по скважинам/ });
    await expect(data).toBeVisible({ timeout: LIMIT(30_000) });
    await data.getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(data).toHaveCount(0);
  }
  await win.getByTestId("cmp-close").click();
  await expect(win).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`implicit 4: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("5. «Модель на скважины»: the new interval table of the set; a name that is taken → the suggested one", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "drillholes.from_model", {});
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("implicit-from-model");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("fm-set").selectOption(String(ids.set));
  await win.getByTestId("fm-source-model").check();
  await win.getByTestId("fm-model").selectOption(String(model.id));
  await expect(win.getByTestId("fm-groups")).toBeVisible({ timeout: 30_000 });
  await win.getByTestId("fm-name").fill("Модель Э19");
  const before = await lastJobId(page, "drillholes.from_model");
  await win.getByTestId("fm-run").click();
  await lastJob(page, "drillholes.from_model", LIMIT(90_000), before);
  await expect(win.getByTestId("fm-result")).toBeVisible({ timeout: LIMIT(30_000) });
  const hook = (await fromHook(page))!;
  expect(hook).toMatchObject({ busy: false, table: "Модель Э19", error: null });
  expect(hook.rows!).toBeGreaterThan(0);
  expect(hook.holes!).toBeGreaterThan(0);
  console.log(`from model: the table «${hook.table}», ${hook.rows} rows of ${hook.holes} holes`);
  // the new table is in the set; the table of the codes is not replaced
  const tables = (await coreApi<{ tables: { table: string; name: string; kind: string; rows: number }[] }>(page, "GET", `/drillholes/sets/${ids.set}/tables`)).body.tables;
  const made = tables.find((t) => t.name === "Модель Э19");
  expect(made, JSON.stringify(tables)).toBeDefined();
  expect(made!.rows).toBe(hook.rows);
  expect(tables.find((t) => t.table === ids.litho.table)?.rows).toBe(ids.litho.rows);
  await page.screenshot({ path: shot("implicit-from-model.png") });

  // the same name again: the core refuses (a table is never replaced), the dialog offers a free one; nothing is written
  await expect(win.getByTestId("fm-run")).toBeEnabled();
  await win.getByTestId("fm-run").click();
  await expect(win.getByTestId("fm-name-taken")).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("fm-suggested")).toBeVisible({ timeout: 30_000 });
  expect((await coreApi<{ tables: { name: string }[] }>(page, "GET", `/drillholes/sets/${ids.set}/tables`)).body.tables.filter((t) => t.name === "Модель Э19")).toHaveLength(1);
  await win.getByTestId("fm-suggested").click();
  await expect(win.getByTestId("fm-name")).not.toHaveValue("Модель Э19");
  await win.getByTestId("fm-close").click();
  await expect(win).toHaveCount(0);

  const took = Date.now() - started;
  console.log(`implicit 5: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("6. ТД15-8: «Сохранить как тренд» → «Тренды» → the trend in the step «Интерполяция» of the wizard → the model with it, the ellipsoid in 3D", async () => {
  test.setTimeout(LIMIT(150_000));
  const started = Date.now();
  // the model of the variogram of AU: «Рассчитать» (the job), «Подобрать», then «Сохранить как тренд»
  await palette(page, "Вариография");
  const vario = page.getByRole("dialog").filter({ has: page.getByTestId("variography-window") });
  await expect(vario).toHaveCount(1);
  await expect(vario.getByLabel("Набор скважин", { exact: true })).toBeVisible({ timeout: 30_000 });
  await vario.getByLabel("Набор скважин", { exact: true }).selectOption(String(ids.set));
  await vario.getByLabel("Таблица", { exact: true }).selectOption(ids.assay.table);
  await vario.getByLabel("Поле", { exact: true }).selectOption("AU");
  await expect(vario.getByTestId("variography-calc")).toBeEnabled({ timeout: 30_000 });
  await vario.getByTestId("variography-calc").click();
  await expect(vario.getByTestId("variography-fit-run")).toBeEnabled({ timeout: LIMIT(60_000) });
  await vario.getByTestId("variography-fit-run").click();
  await expect(vario.getByTestId("variography-fit")).toBeVisible({ timeout: LIMIT(30_000) });
  const trendsOf = async () => (await coreApi<{ trends: { id: number; name: string; kind: string }[] }>(page, "GET", "/geostat/trends")).body.trends;
  expect(await trendsOf()).toEqual([]);
  await vario.getByRole("button", { name: "Сохранить", exact: true }).click(); // the model is stored: the window closes without asking
  await expect(vario.getByRole("button", { name: "Экспорт", exact: true })).toBeEnabled({ timeout: 15_000 });
  await vario.getByRole("button", { name: "Сохранить как тренд", exact: true }).click();
  await expect.poll(async () => (await trendsOf()).length, { timeout: 15_000 }).toBe(1);
  const trend = (await trendsOf())[0];
  expect(trend.kind).toBe("global");
  await expect(page.getByTestId(`tree-trend-${trend.id}`)).toBeAttached({ timeout: 15_000 });
  await vario.getByRole("button", { name: "Закрыть", exact: true }).click();
  await expect(vario).toHaveCount(0);

  // the wizard of RBF: one group of the domains, the trend of the list in the step «Интерполяция»
  await palette(page, "Новая модель");
  const wiz = page.getByTestId("implicit-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await wiz.getByTestId("imp-name").fill("Модель с трендом");
  for (let i = 0; i < 6; i++) await wiz.getByTestId(`imp-box-${i}`).fill(String(BOX[i]));
  await wiz.getByTestId("imp-h").fill(String(STEP_H));
  await wiz.getByTestId("imp-step-2-tab").click();
  await wiz.getByTestId("imp-set").selectOption(String(ids.set));
  await expect(wiz.getByTestId("imp-table").locator(`option[value="${ids.litho.table}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-table").selectOption(ids.litho.table);
  await expect(wiz.getByTestId("imp-field").locator('option[value="CODE"]')).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-field").selectOption("CODE");
  await expect(wiz.getByTestId("imp-codes-note")).toContainText("Кодов в поле", { timeout: 30_000 });
  await wiz.getByTestId("imp-step").fill("10");
  await wiz.getByTestId("imp-group-add").click();
  await wiz.getByTestId("imp-group-name-0").fill("Интрузия");
  await wiz.getByTestId("imp-code-add-0").selectOption("INT");
  await wiz.getByTestId("imp-step-3-tab").click();
  await wiz.getByTestId("imp-trend-mode").selectOption("list");
  await expect(wiz.getByTestId("imp-trend-id").locator(`option[value="${trend.id}"]`)).toBeAttached({ timeout: 30_000 });
  await expect(wiz.getByTestId("imp-trend-id").locator(`option[value="${trend.id}"]`)).toContainText("глобальный");
  await wiz.getByTestId("imp-trend-id").selectOption(String(trend.id));
  await wiz.getByTestId("imp-step-4-tab").click();
  await expect(wiz.getByTestId("imp-sum-trend")).toHaveText(`тренд №${trend.id}`);
  const before = await lastJobId(page, "implicit.build");
  await wiz.getByTestId("imp-save-build").click();
  await lastJob(page, "implicit.build", LIMIT(90_000), before);
  await expect(wiz.getByTestId("imp-saved")).toContainText("Сохранено: «Модель с трендом»", { timeout: LIMIT(30_000) });
  await wiz.getByTestId("imp-close").click();
  await expect(wiz).toHaveCount(0);
  const withTrend = (await rowsList(page)).find((m) => m.name === "Модель с трендом")!;
  expect(withTrend.state).toBe("ready");
  const stored = (await coreApi<{ meta?: Record<string, unknown> } & Record<string, unknown>>(page, "GET", `/implicit/models/${withTrend.id}`)).body;
  expect(JSON.stringify(stored)).toContain(`"trend_id":${trend.id}`); // the model keeps the trend it was built with

  // the ellipsoid of the trend in «Свойства» → in 3D (`implicit:ellipsoid:<модель>`)
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await tree.getByTestId(`tree-implicit-${withTrend.id}`).dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  await expect(props.getByTestId("props-implicit-anisotropy")).toHaveAttribute("data-source", "trend");
  await props.getByTestId("props-implicit-anisotropy").getByRole("checkbox", { name: "Эллипсоид в 3D" }).check();
  const key = `implicit:ellipsoid:${withTrend.id}`;
  await expect.poll(async () => (await stats(page))?.ellipsoids.map((e) => e.key) ?? [], { timeout: LIMIT(30_000) }).toContain(key);
  const ell = (await stats(page))!.ellipsoids.find((e) => e.key === key)!;
  expect(ell.ranges.every((r) => r > 0)).toBe(true);
  expect(ell.centre).toHaveLength(3);
  expect(await layers(page)).toContain(key);
  console.log(`ellipsoid of the trend: ranges ${ell.ranges.map((r) => r.toFixed(1)).join(" / ")}, angles ${ell.angles.map((a) => a.toFixed(1)).join(" / ")}`);
  await page.screenshot({ path: shot("implicit-trend.png") });

  const took = Date.now() - started;
  console.log(`implicit 6: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("7. ТД15-8: a structural trend (the measurements of a table) → the discs in 3D → it is in the list of the wizard of RBF and the model is built with it", async () => {
  test.setTimeout(LIMIT(150_000));
  const started = Date.now();
  expect((await runCommand(page, "trend.open", {})).ok).toBe(true);
  const win = page.locator('[data-testid^="trend-window-"]');
  await expect(win).toBeVisible({ timeout: 30_000 });
  const one = (prefix: string) => win.locator(`[data-testid^="${prefix}-"]`);
  await one("trend-kind-structural").check();
  await one("trend-name").fill("Структура Э19");
  await expect(one("trend-set")).toBeVisible({ timeout: 30_000 });
  await one("trend-set").selectOption(String(ids.structSet));
  await expect(one("trend-table").locator(`option[value="${ids.measures.table}"]`)).toBeAttached({ timeout: 30_000 });
  await one("trend-table").selectOption(ids.measures.table);
  await expect(one("trend-alpha").locator('option[value="ALPHA"]')).toBeAttached({ timeout: 30_000 });
  await one("trend-alpha").selectOption("ALPHA");
  await one("trend-dipdir").selectOption("DIPDIR");
  await one("trend-save").click();
  const trendsOf = async () => (await coreApi<{ trends: { id: number; name: string; kind: string; measurements?: number | null }[] }>(page, "GET", "/geostat/trends")).body.trends;
  await expect.poll(async () => (await trendsOf()).filter((t) => t.kind === "structural").length, { timeout: 15_000 }).toBe(1);
  const trend = (await trendsOf()).find((t) => t.kind === "structural")!;
  expect(trend.name).toBe("Структура Э19");
  await expect(page.getByTestId(`tree-trend-${trend.id}`)).toBeAttached({ timeout: 15_000 });

  // the discs of the trend in 3D: one disc per measurement (108), at the places of the measurements
  expect(await discs(page)).toEqual([]);
  await expect(one("trend-discs")).toBeEnabled({ timeout: 15_000 });
  await one("trend-discs").check();
  await expect.poll(async () => (await discs(page))?.map((d) => d.id) ?? [], { timeout: LIMIT(30_000) }).toEqual([trend.id]);
  const shown = (await discs(page))![0];
  expect(shown.count).toBeGreaterThan(0);
  expect(shown.count + shown.skipped).toBe(108);
  expect(await layers(page)).toContain(`trend:discs:${trend.id}`);
  await expect(one("trend-discs-info")).toContainText("Показано");
  console.log(`discs of the structural trend: ${shown.count} shown, ${shown.skipped} skipped, radius ${shown.radius}`);
  await page.screenshot({ path: shot("implicit-discs.png") });
  await win.getByRole("button", { name: "Закрыть", exact: true }).click();
  await expect(win).toHaveCount(0);

  // the wizard of RBF: the list of the trends has the structural one, «структурный»; the summary names it; the model is built with it
  await palette(page, "Новая модель");
  const wiz = page.getByTestId("implicit-wizard");
  await expect(wiz).toBeVisible({ timeout: 30_000 });
  await wiz.getByTestId("imp-name").fill("Модель со структурой");
  for (let i = 0; i < 6; i++) await wiz.getByTestId(`imp-box-${i}`).fill(String(BOX[i]));
  await wiz.getByTestId("imp-h").fill(String(STEP_H));
  await wiz.getByTestId("imp-step-2-tab").click();
  await wiz.getByTestId("imp-set").selectOption(String(ids.set));
  await expect(wiz.getByTestId("imp-table").locator(`option[value="${ids.litho.table}"]`)).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-table").selectOption(ids.litho.table);
  await expect(wiz.getByTestId("imp-field").locator('option[value="CODE"]')).toBeAttached({ timeout: 30_000 });
  await wiz.getByTestId("imp-field").selectOption("CODE");
  await expect(wiz.getByTestId("imp-codes-note")).toContainText("Кодов в поле", { timeout: 30_000 });
  await wiz.getByTestId("imp-step").fill("10");
  await wiz.getByTestId("imp-group-add").click();
  await wiz.getByTestId("imp-group-name-0").fill("Интрузия");
  await wiz.getByTestId("imp-code-add-0").selectOption("INT");
  await wiz.getByTestId("imp-step-3-tab").click();
  await wiz.getByTestId("imp-trend-mode").selectOption("list");
  const option = wiz.getByTestId("imp-trend-id").locator(`option[value="${trend.id}"]`);
  await expect(option).toBeAttached({ timeout: 30_000 });
  await expect(option).toContainText("структурный");
  await wiz.getByTestId("imp-trend-id").selectOption(String(trend.id));
  await wiz.getByTestId("imp-step-4-tab").click();
  await expect(wiz.getByTestId("imp-sum-trend")).toHaveText(`тренд №${trend.id}`);
  const before = await lastJobId(page, "implicit.build");
  await wiz.getByTestId("imp-save-build").click();
  await lastJob(page, "implicit.build", LIMIT(90_000), before);
  await expect(wiz.getByTestId("imp-saved")).toContainText("Сохранено: «Модель со структурой»", { timeout: LIMIT(30_000) });
  await wiz.getByTestId("imp-close").click();
  await expect(wiz).toHaveCount(0);
  const built = (await rowsList(page)).find((m) => m.name === "Модель со структурой")!;
  expect(built.state).toBe("ready");
  expect(built.outputs.length).toBeGreaterThan(0);
  // the axes of a structural trend are local: there is no single ellipsoid to draw, and the properties say so
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await tree.getByTestId(`tree-implicit-${built.id}`).dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  await expect(props.getByTestId("props-implicit-anisotropy")).toHaveAttribute("data-source", "trend");
  await expect(props.getByTestId("props-implicit-anisotropy").getByRole("checkbox", { name: "Эллипсоид в 3D" })).toBeDisabled();

  const took = Date.now() - started;
  console.log(`implicit 7: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("8. Ctrl+K finds the commands of the implicit modelling by their Russian words", async () => {
  test.setTimeout(LIMIT(60_000));
  // the palette lists the commands of the ribbon in its order (no ranking): the command is among the options found
  const found: string[] = ["Новая модель", "Домены литологии", "Проверка по скважинам", "Сверка со скважинами", "Модель на скважины", "Карта опоры"];
  for (const query of found) {
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill(query);
    const options = page.getByLabel("Палитра команд").getByRole("option");
    await expect(options.filter({ hasText: query }).first(), `«${query}»`).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
  }
  // the group of the section is found by its name: every command of it is listed
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill("Неявные модели");
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText("Неявные модели");
  expect(await page.getByLabel("Палитра команд").getByRole("option").count()).toBeGreaterThanOrEqual(5);
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
  // a command found runs: «Проверка по скважинам» opens the window of the check on the model chosen in the tree
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill("Проверка по скважинам");
  await page.getByLabel("Палитра команд").getByRole("option").filter({ hasText: "Проверка по скважинам" }).first().click();
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
  await expect(page.getByTestId("implicit-check")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("implicit-check").getByTestId("chk-close").click();
  await expect(page.getByTestId("implicit-check")).toHaveCount(0);
});
