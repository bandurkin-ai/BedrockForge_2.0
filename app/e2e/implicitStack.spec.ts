// Э19б, подшаг 19.40, часть E2E (ТЗ 7.35 Д-24, Д-31, Д-34 ч. 2, Д-39; ADR 0026 «Неявное моделирование»): the stack of the relations, the faults, the systems of veins
// and the planned holes end to end on the scene of `tools/gen/implicit.py` (100 holes through folded layers, the sphere INT, the vein VN; the second vein VN2 is
// made by `implicitStackWorld.ts` the way `core/tests/stack_world.py` makes it) imported through the core — no demo, no real data, every answer is the core's own.
// One app, one project, the scenarios in a row (each ≤ 60 s; the waits are on events and states, the numbers — from the state, never from the pixels of software WebGL):
//  1. «Стек отношений» (`implicit-stack`) of a model of three domains (INT, SS, ALV): the order by the buttons, «Интрузия» and «Эрозия» → «Применить» → the model is
//     built (`implicit.update`) → «Свойства» (`props-implicit-stack`): the order and the relations; the journal of the points dropped by the relations
//     (`stats.stack.dropped`, `stk-dropped`).
//  2. «Разлом» (`grp-fault`): the name, the three layers of strings (the contact, inside, outside), the domains it cuts → «Сохранить и обновить» → the wings in the
//     tree (`tree-implicit-<id>-wings-*`), the offset (`props-implicit-offset`, `flt-offset`) — the result of the build, read only.
//  3. «Система жил» (`grp-vein_system`): the veins VN and VN2 → the build → the table of the pairs (`vsys-pairs`) and the lines of intersection: in the tree and in 3D.
//  4. «Проектная скважина» (`planned-window`): the collar and a target → «Сохранить» → the layer of 3D (`__bfPlanned`), the forecast by the bodies of the model
//     (`pl-evaluate`), «План / факт» with a hole of the set (`pl-compare-run`), the export of the CSV into a folder of the test (`pl-export-csv`).
//  5. Ctrl+K finds the commands `implicit.relations`, `implicit.fault`, `implicit.vein_system`, `planned.new` by their Russian words.
// A window of 1024 × 640 with the ribbon folded; the limits on the .exe are ×1.5 (ADR 0022 «CI»).
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { answerOpenDialog, close, coreApi, launch, runCommand, setWindowSize, shot, waitFrame, type CoreJob } from "./helpers";
import { buildSet, createModel, domain, faultLayers, SET, type Ids } from "./implicitStackWorld";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);

type Output = { wireframe_id: number | null; line_layer_id: number | null; kind: string; group: number | null; name: string; wing: number | null; length: number | null };
type Info = { id: number; name: string; version: number; state: string; stale: boolean; outputs: Output[]; meta: { groups: { id: number; name: string; type: string; relation?: string; faults?: number[] }[]; stats?: Record<string, unknown> } };
type PlannedStats = { holes: number; segments: number; chosenSegments: number; targets: number; collars: number; bbox: number[] | null };
type PlannedUi = { id: number | null; version: number | null; tab: string; dirty: boolean; saved: string | null; issue: string | null };
type PlannedCheck = { evaluate: string; intervals: number | null; nearest: number | null; compare: string; max: number | null; rows: number | null };

const hook = <T>(page: Page, name: string, fn: string): Promise<T | null> =>
  page.evaluate(([n, f]) => {
    const h = (window as unknown as Record<string, Record<string, () => unknown> | undefined>)[n];
    return (h && h[f] ? h[f]() : null) as never;
  }, [name, fn] as const) as Promise<T | null>;
const plannedStats = (page: Page) => hook<PlannedStats>(page, "__bfPlanned", "stats");
const plannedList = (page: Page) => hook<{ id: number; name: string; version: number; path: number; target: unknown }[]>(page, "__bfPlannedUi", "list");
const plannedWindow = (page: Page) => hook<PlannedUi>(page, "__bfPlannedUi", "window");
const plannedCheck = (page: Page) => hook<PlannedCheck>(page, "__bfPlannedUi", "check");
const layers = (page: Page) => page.evaluate(() => window.__bfScene!.state().layers);
const info = async (page: Page, id: number) => (await coreApi<Info>(page, "GET", `/implicit/models/${id}`)).body;
const stats = async (page: Page, id: number) => (await info(page, id)).meta.stats ?? {};

const KINDS = ["implicit.build", "implicit.update"];
/** The last job of the build or the update once it is over (the jobs list of the core; the waits are polls of the state). */
async function lastJob(page: Page, timeout: number, after = 0): Promise<CoreJob> {
  let job: CoreJob | undefined;
  await expect
    .poll(
      async () => {
        const jobs = (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => KINDS.includes(j.kind) && j.id > after);
        job = jobs.sort((a, b) => b.id - a.id)[0];
        return job?.status ?? "none";
      },
      { timeout, intervals: [200, 400, 800] },
    )
    .toMatch(/^(done|failed|cancelled)$/);
  expect(job!.status, JSON.stringify(job!.error)).toBe("done");
  return job!;
}
const lastJobId = async (page: Page) => Math.max(0, ...(await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => KINDS.includes(j.kind)).map((j) => j.id));

/** Ctrl+K → the command by its title found in the list → the palette closes (the command runs on Enter when `run`). */
async function palette(page: Page, query: string, run = false): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  const options = page.getByLabel("Палитра команд").getByRole("option");
  await expect(options.filter({ hasText: query }).first(), `«${query}»`).toBeVisible();
  if (!run) {
    await page.keyboard.press("Escape");
  } else {
    await options.filter({ hasText: query }).first().click();
  }
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

/** A window of the test is closed by its button (a window with unsaved changes would ask: none here). */
async function closeWindow(page: Page, testId: string, button: string): Promise<void> {
  await page.getByTestId(testId).getByTestId(button).click();
  await expect(page.getByTestId(testId)).toHaveCount(0);
}

let app: ElectronApplication;
let page: Page;
let dir: string;
let ids: Ids;
let stackModel: number; // INT, SS, ALV
let faultModel: number; // ALV, SS, SH
let veinModel: number; // SS

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(LIMIT(240_000));
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-stack-"));
  ({ app, page } = await launch({ BF_E2E: "1" }));
  await setWindowSize(app, 1024, 640); // as small as on the Windows runner
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill("Стек Э19б");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText("Стек Э19б", { timeout: 60_000 });
  await waitFrame(page);
  expect((await runCommand(page, "ribbon.toggle")).ok).toBe(true);
  const t = Date.now();
  ids = await buildSet(page, dir);
  stackModel = await createModel(page, ids, "Стек Э19б", [domain(1, "Интрузия", "INT"), domain(2, "Песчаник", "SS"), domain(3, "Наносы", "ALV")]);
  faultModel = await createModel(page, ids, "Разломы Э19б", [domain(1, "Наносы", "ALV"), domain(2, "Песчаник", "SS"), domain(3, "Сланец", "SH")]);
  veinModel = await createModel(page, ids, "Жилы Э19б", [domain(1, "Песчаник", "SS")]);
  console.log(`implicit stack: the project is built in ${((Date.now() - t) / 1000).toFixed(1)} s`);
});

test.afterAll(async () => {
  if (app) await close(app);
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
});

test("1. «Стек отношений»: the order, «Интрузия» and «Эрозия» → «Применить» → the build → «Свойства», the dropped points", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "implicit.relations", { model_id: stackModel });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("implicit-stack");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("stk-model")).toContainText("Стек Э19б", { timeout: 30_000 });
  const rows = win.getByTestId("stk-list").locator("> li[data-testid^='stk-row-']");
  await expect(rows).toHaveCount(3);
  // the order the model was made in: the youngest on top
  await expect(rows.nth(0)).toHaveAttribute("data-testid", "stk-row-1");
  await expect(rows.nth(2)).toHaveAttribute("data-testid", "stk-row-3");
  await expect(win.getByTestId("stk-apply")).toBeDisabled(); // nothing is changed
  await expect(win.getByTestId("stk-up-1")).toBeDisabled();
  await expect(win.getByTestId("stk-down-3")).toBeDisabled();
  await expect(win.getByTestId("stk-relation-3")).toBeDisabled(); // the oldest group: nothing is below it

  // «Наносы» is the youngest of the rocks, under the intrusion: one step up; the intrusion intrudes, the alluvium erodes
  await win.getByTestId("stk-up-3").click();
  await expect(rows.nth(1)).toHaveAttribute("data-testid", "stk-row-3");
  await expect(rows.nth(2)).toHaveAttribute("data-testid", "stk-row-2");
  await win.getByTestId("stk-relation-1").selectOption("intrusion");
  await win.getByTestId("stk-relation-3").selectOption("erode");
  await expect(win.getByTestId("stk-relation-3")).toHaveValue("erode");
  await expect(win.getByTestId("stk-help-3")).not.toHaveText("");
  await expect(win.getByTestId("stk-apply")).toBeEnabled();
  await page.screenshot({ path: shot("implicit-stack.png") });
  await win.getByTestId("stk-apply").click();
  await expect(win.getByTestId("stk-saved")).not.toHaveText("", { timeout: 30_000 });
  await expect(win.getByTestId("stk-error")).toHaveCount(0);
  await expect(win.getByTestId("stk-apply")).toBeDisabled(); // written: no more changes
  // the core stores the order and the relations
  const stored = await info(page, stackModel);
  expect(stored.meta.groups.filter((g) => g.type === "domain").map((g) => g.id)).toEqual([1, 3, 2]);
  expect(stored.meta.groups.find((g) => g.id === 1)?.relation).toBe("intrusion");
  expect(stored.meta.groups.find((g) => g.id === 3)?.relation).toBe("erode");

  // the build with the stack (the job `implicit.update` / `implicit.build`)
  const before = await lastJobId(page);
  const built = await runCommand(page, "implicit.update", { model_id: stackModel });
  expect(built.ok, built.result).toBe(true);
  const job = await lastJob(page, LIMIT(90_000), before);
  console.log(`implicit stack build: ${job.kind} ${JSON.stringify(job.result ?? {}).slice(0, 160)}`);
  const model = await info(page, stackModel);
  expect(model.state).toBe("ready");
  expect(model.outputs.length).toBeGreaterThan(0);

  // the journal of the dropped points: the number of the core, in the window and in «Свойства»
  const stack = (await stats(page, stackModel)).stack as { dropped_total: number; dropped: { group: number; by: number; relation: string; points: number; of: number }[]; order: number[] };
  expect(stack.order).toEqual([1, 3, 2]);
  expect(typeof stack.dropped_total).toBe("number");
  expect(stack.dropped_total).toBeGreaterThanOrEqual(0);
  console.log(`implicit stack: dropped ${stack.dropped_total} points: ${JSON.stringify(stack.dropped)}`);
  await expect(win.getByTestId("stk-dropped")).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("stk-dropped")).toContainText(`отброшено точек данных: ${stack.dropped_total.toLocaleString("ru-RU")}`);
  await closeWindow(page, "implicit-stack", "stk-close");

  // the tree: the branch «Стек» lists the domains by age with the relations
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  const row = tree.getByTestId(`tree-implicit-${stackModel}`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(tree.getByTestId(`tree-implicit-${stackModel}-stack`)).toBeAttached();
  await expect(tree.getByTestId(`tree-implicit-${stackModel}-stack-1`)).toContainText("Интрузия");
  await expect(tree.getByTestId(`tree-implicit-${stackModel}-stack-3`)).toContainText("Эрозия");
  const order = await tree.locator(`[data-testid^="tree-implicit-${stackModel}-stack-"]`).evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  expect(order).toEqual([1, 3, 2].map((g) => `tree-implicit-${stackModel}-stack-${g}`));

  // «Свойства»: the order, the relations, what was dropped
  await row.dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  const panel = props.getByTestId("props-implicit-stack");
  await expect(panel).toBeVisible();
  const lines = panel.getByTestId("props-implicit-stack-lines").locator("li");
  await expect(lines).toHaveCount(3);
  await expect(lines.nth(0)).toContainText("Интрузия");
  await expect(lines.nth(1)).toContainText("Наносы");
  await expect(lines.nth(1)).toContainText("Эрозия");
  await expect(lines.nth(2)).toContainText("Песчаник");
  await expect(panel.getByTestId("props-implicit-stack-dropped")).toContainText(stack.dropped_total.toLocaleString("ru-RU"));
  await expect(panel.getByTestId("props-implicit-relations")).toBeEnabled();
  await page.screenshot({ path: shot("implicit-stack-props.png") });

  const took = Date.now() - started;
  console.log(`implicit stack 1: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("2. «Разлом»: the wizard (three layers of strings, the domains) → the build → the wings in the tree, the offset (read only)", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const layerIds = await faultLayers(page);
  const opened = await runCommand(page, "implicit.fault", { model_id: faultModel });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("grp-fault");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("grp-model")).toContainText("Разломы Э19б", { timeout: 30_000 });
  await win.getByTestId("grp-name").fill("Разлом X200");
  await expect(win.getByTestId("flt-radius-words")).toBeVisible(); // an empty radius: an infinite fault, in words
  // the hand data: the contact is the trace of the fault, «внутри» and «снаружи» fix the sides
  for (const [i, role] of (["contact", "inside", "outside"] as const).entries()) {
    await win.getByTestId("flt-layer-add").click();
    const pick = win.getByTestId(`flt-layer-pick-${i}`);
    await expect(pick.locator(`option[value="${layerIds[i]}"]`)).toBeAttached({ timeout: 30_000 });
    await pick.selectOption(String(layerIds[i]));
    await win.getByTestId(`flt-layer-role-${i}`).selectOption(role);
  }
  // the domains it cuts: «Песчаник» (2) and «Сланец» (3); the alluvium is above it
  for (const d of [2, 3]) await win.getByTestId(`flt-domain-${d}`).check();
  await expect(win.getByTestId("flt-domain-1")).not.toBeChecked();
  await expect(win.getByTestId("flt-no-offset")).toHaveCount(0); // a new fault has no result to speak of
  await expect(win.getByTestId("grp-check")).toHaveCount(0); // «Данные группы» checks the codes; the layers are the hand data
  await page.screenshot({ path: shot("implicit-fault.png") });

  const before = await lastJobId(page);
  await win.getByTestId("grp-save-update").click();
  const job = await lastJob(page, LIMIT(90_000), before);
  await expect(win.getByTestId("grp-saved")).not.toHaveText("", { timeout: LIMIT(30_000) });
  const model = await info(page, faultModel);
  expect(model.state, JSON.stringify(job.error)).toBe("ready");
  const fault = model.meta.groups.find((g) => g.type === "fault")!;
  expect(fault.name).toBe("Разлом X200");
  expect(model.meta.groups.filter((g) => (g.faults ?? []).includes(fault.id)).map((g) => g.id).sort()).toEqual([2, 3]);
  await closeWindow(page, "grp-fault", "grp-close");

  // the tree: the fault, the wings of the domains it cuts (two each), the regions of the rest
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-implicit-${faultModel}-fault-${fault.id}`)).toBeVisible({ timeout: 30_000 });
  for (const g of [2, 3]) {
    // one row in «Поверхности» and one in «Тела»: both carry the same test id (the branch is not in it)
    const wings = tree.getByTestId(`tree-implicit-${faultModel}-wings-${g}`);
    await expect(wings).toHaveCount(2, { timeout: 30_000 });
    for (const w of await wings.all()) {
      await expect(w).toContainText("крылья");
      await expect(w).toContainText("2");
    }
  }
  const outputs = model.outputs.filter((o) => o.wing !== null);
  expect(outputs.length).toBeGreaterThanOrEqual(4);
  console.log(`implicit fault: ${model.outputs.length} outputs, ${outputs.length} of the wings`);
  const fstats = (await stats(page, faultModel)).faults as Record<string, { points: number; wing_volumes: number[] }>;
  expect(fstats[String(fault.id)].points).toBeGreaterThan(0);

  // «Свойства»: the offset is a result of the build, read only
  await tree.getByTestId(`tree-implicit-${faultModel}`).dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  await expect(props.getByTestId("props-implicit-faults")).toBeVisible();
  await expect(props.getByTestId(`props-implicit-fault-${fault.id}`)).toContainText("Разлом X200");
  const offset = props.getByTestId("props-implicit-offset");
  await expect(offset).toBeVisible();
  await expect(offset.locator("tbody tr")).not.toHaveCount(0);
  expect(await offset.locator("input, select, textarea").count()).toBe(0); // nothing to type: the offset is the difference of the fields of the wings
  await expect(offset).toContainText("только чтение");
  await page.screenshot({ path: shot("implicit-fault-props.png") });

  // the window of the fault again (double click in the tree): the offset stands in it, read only too
  await tree.getByTestId(`tree-implicit-${faultModel}-fault-${fault.id}`).locator("[data-tree-name]").dblclick(); // the handler is on the name
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("grp-name")).toHaveValue("Разлом X200");
  await expect(win.getByTestId("flt-offset")).toContainText("Смещение", { timeout: 30_000 });
  await expect(win.getByTestId("flt-offset").locator("input")).toHaveCount(0);
  await closeWindow(page, "grp-fault", "grp-close");

  const took = Date.now() - started;
  console.log(`implicit stack 2: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("3. «Система жил»: two veins → the build → the table of the pairs, the lines of intersection in the tree and in 3D", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  const opened = await runCommand(page, "implicit.vein_system", { model_id: veinModel });
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("grp-vein_system");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("grp-model")).toContainText("Жилы Э19б", { timeout: 30_000 });
  await win.getByTestId("grp-name").fill("Жилы Q");
  const add = win.getByTestId("vsys-add");
  // the codes of the table, minus those of the groups of the model («Песчаник» keeps SS): the veins are VN (the youngest, it cuts) and VN2
  await expect(add.locator('option[value="VN"]')).toBeAttached({ timeout: 30_000 });
  await expect(add.locator('option[value="SS"]')).toHaveCount(0);
  await add.selectOption("VN");
  await expect(win.getByTestId("vsys-code-0")).toHaveValue("VN");
  await add.selectOption("VN2");
  await expect(win.getByTestId("vsys-code-1")).toHaveValue("VN2");
  await win.getByTestId("vsys-name-0").fill("Жила VN");
  await win.getByTestId("vsys-name-1").fill("Жила VN2");
  await expect(win.getByTestId("vsys-pairs")).toHaveCount(0); // nothing is built yet
  await page.screenshot({ path: shot("implicit-vein-system.png") });

  const before = await lastJobId(page);
  await win.getByTestId("grp-save-update").click();
  const job = await lastJob(page, LIMIT(90_000), before);
  await expect(win.getByTestId("grp-saved")).not.toHaveText("", { timeout: LIMIT(30_000) });
  const model = await info(page, veinModel);
  expect(model.state, JSON.stringify(job.error)).toBe("ready");
  const system = model.meta.groups.find((g) => g.type === "vein_system")!;
  const pairs = (await stats(page, veinModel)).vein_pairs as { a: string; b: string; line_length: number; volume_common: number }[];
  expect(pairs.length).toBeGreaterThanOrEqual(1);
  console.log(`implicit veins: pairs ${JSON.stringify(pairs)}`);
  expect(pairs[0].line_length).toBeGreaterThan(0);
  const lineOutputs = model.outputs.filter((o) => o.kind === "line");
  expect(lineOutputs.length).toBeGreaterThanOrEqual(1);
  const lineId = lineOutputs[0].line_layer_id!;
  expect(lineId).toBeGreaterThan(0);

  // the table of the pairs in the window: the pair, the length of the line, the volume of the common zone
  const table = win.getByTestId("vsys-pairs");
  await expect(table).toBeVisible({ timeout: 30_000 });
  await expect(table.locator("tbody tr")).toHaveCount(pairs.length);
  await expect(table.locator("tbody tr").first()).toContainText("VN");
  await expect(win.getByTestId(`vsys-line-${lineId}`)).toBeVisible();
  await closeWindow(page, "grp-vein_system", "grp-close");

  // the lines of intersection in the tree (a branch «Линии», a row per layer) and in 3D (`layer.show {layer: "lines"}` by the check box)
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId(`tree-implicit-${veinModel}-line`)).toBeAttached({ timeout: 30_000 });
  const lineRow = tree.getByTestId(`tree-implicit-line-${lineId}`);
  await lineRow.scrollIntoViewIfNeeded();
  await expect(lineRow).toBeVisible();
  await expect(lineRow).toContainText("м");
  const key = `ln:${lineId}`;
  const shown = await layers(page);
  if (!shown.includes(key)) {
    await lineRow.getByRole("checkbox").check();
  }
  await expect.poll(async () => await layers(page), { timeout: LIMIT(30_000) }).toContain(key);
  expect((await page.evaluate(() => window.__bfScene!.state().lines)).layers).toBeGreaterThanOrEqual(1);
  await waitFrame(page);
  // hidden and shown again by the command of the registry the check box runs
  expect((await runCommand(page, "layer.show", { layer: "lines", id: lineId, visible: false })).ok).toBe(true);
  await expect(lineRow.getByRole("checkbox")).not.toBeChecked();
  expect((await runCommand(page, "layer.show", { layer: "lines", id: lineId, visible: true })).ok).toBe(true);
  await expect(lineRow.getByRole("checkbox")).toBeChecked();
  expect(await layers(page)).toContain(key);

  // «Свойства»: the system with its pairs
  await tree.getByTestId(`tree-implicit-${veinModel}`).dblclick();
  const props = page.getByTestId("props-implicit");
  await expect(props).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  const panel = props.getByTestId(`props-implicit-system-${system.id}`);
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("vsys-pairs").locator("tbody tr")).toHaveCount(pairs.length);
  await expect(panel.getByTestId(`vsys-line-${lineId}`)).toBeVisible();
  await page.screenshot({ path: shot("implicit-vein-props.png") });

  const took = Date.now() - started;
  console.log(`implicit stack 3: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("4. «Проектная скважина»: the collar and a target → the layer of 3D → the forecast by the bodies of the model → «План / факт» → the CSV", async () => {
  test.setTimeout(LIMIT(120_000));
  const started = Date.now();
  expect(await plannedStats(page)).toMatchObject({ holes: 0 });
  const opened = await runCommand(page, "planned.new");
  expect(opened.ok, opened.result).toBe(true);
  const win = page.getByTestId("planned-window");
  await expect(win).toBeVisible({ timeout: 30_000 });
  await expect(win.getByTestId("pl-version")).toHaveText("не сохранена");
  await win.getByTestId("pl-name").fill("ПС-Э19б");
  await win.getByTestId("pl-set").selectOption(String(ids.set));
  for (const [axis, v] of [["x", "200"], ["y", "200"], ["z", "330"]] as const) await win.getByTestId(`pl-collar-${axis}`).fill(v);
  // a hole through a target: the heart of the intrusion; the azimuth and the dip are solved by the core
  await win.getByTestId("pl-mode-target").check();
  for (const [axis, v] of [["x", "200"], ["y", "200"], ["z", "200"]] as const) await win.getByTestId(`pl-target-${axis}`).fill(v);
  await expect(win.getByTestId("pl-azimuth")).toHaveCount(0); // a hole through a target has no azimuth to type
  await page.screenshot({ path: shot("planned-window.png") });
  await win.getByTestId("pl-save").click();
  await expect(win.getByTestId("pl-saved")).toContainText("записана в проект", { timeout: 30_000 });
  await expect(win.getByTestId("pl-solved")).toContainText("ядро нашло азимут", { timeout: 30_000 });
  const list = (await plannedList(page))!;
  expect(list).toHaveLength(1);
  const hole = list[0];
  expect(hole.path).toBeGreaterThan(100); // the path of a point every metre: ~130 m
  expect(hole.target).not.toBeNull();
  expect(await plannedWindow(page)).toMatchObject({ id: hole.id, version: hole.version, dirty: false, issue: null });

  // the section of the tree and the layer of 3D
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await expect(tree.getByTestId("tree-planned")).toHaveAttribute("data-holes", "1", { timeout: 30_000 });
  await expect(tree.getByTestId(`tree-planned-${hole.id}`)).toContainText("ПС-Э19б");
  await expect.poll(async () => (await plannedStats(page))?.holes ?? 0, { timeout: LIMIT(30_000) }).toBe(1);
  const drawn = (await plannedStats(page))!;
  expect(drawn.segments).toBeGreaterThan(0);
  expect(drawn.targets).toBe(1);
  expect(drawn.collars).toBe(1);
  expect(await layers(page)).toContain("planned");

  // the forecast: the hole is cut by the bodies of the model of the first scenario; the nearest holes of the set at the target
  await win.getByTestId("pl-tab-forecast").click();
  await win.getByTestId("pl-source-model").check();
  const model = win.getByTestId("pl-model");
  await expect(model.locator(`option[value="${stackModel}"]`)).toBeAttached({ timeout: 30_000 });
  await model.selectOption(String(stackModel));
  await win.getByTestId("pl-eval-set").selectOption(String(ids.set));
  await expect(win.getByTestId("pl-evaluate")).toBeEnabled();
  await win.getByTestId("pl-evaluate").click();
  const answer = win.getByTestId("pl-evaluate-answer");
  await expect(answer).toBeVisible({ timeout: LIMIT(60_000) });
  await expect(win.getByTestId("pl-evaluate-error")).toHaveCount(0);
  const intervals = win.getByTestId("pl-intervals").locator("tbody tr");
  expect(await intervals.count()).toBeGreaterThan(0);
  await expect(answer).toContainText("Внутри тел");
  const nearest = win.getByTestId("pl-nearest").locator("tbody tr");
  const near = await nearest.count();
  expect(near).toBeGreaterThan(0);
  expect(near).toBeLessThanOrEqual(5);
  const rowsText = await intervals.allTextContents();
  console.log(`planned forecast: ${rowsText.length} intervals: ${rowsText.slice(0, 4).join(" | ")}; nearest ${near}`);
  expect(await plannedCheck(page)).toMatchObject({ evaluate: expect.stringMatching(/ok|done|ready/), intervals: rowsText.length, nearest: near });
  await page.screenshot({ path: shot("planned-forecast.png") });

  // «План / факт»: a hole of the set against the planned one
  await win.getByTestId("pl-tab-compare").click();
  await expect(win.getByTestId("pl-compare")).toBeVisible();
  await win.getByTestId("pl-cmp-set").selectOption(String(ids.set));
  const holeSelect = win.getByTestId("pl-cmp-hole");
  await expect(holeSelect.locator(`option[value="${ids.firstHole}"]`)).toBeAttached({ timeout: 30_000 });
  await holeSelect.selectOption(ids.firstHole);
  await win.getByTestId("pl-compare-run").click();
  await expect(win.getByTestId("pl-compare-answer")).toBeVisible({ timeout: LIMIT(60_000) });
  await expect(win.getByTestId("pl-compare-error")).toHaveCount(0);
  const checked = (await plannedCheck(page))!;
  expect(checked.max).not.toBeNull();
  expect(checked.max!).toBeGreaterThan(0); // two different holes: the deviation is a number, never a made-up 0
  expect(checked.rows).toBeGreaterThan(50);
  await expect(win.getByTestId("pl-max")).not.toHaveText("");
  await expect(win.getByTestId("pl-compare-table")).toHaveAttribute("data-rows", String(checked.rows));
  console.log(`planned compare: max ${checked.max} m, ${checked.rows} rows`);
  await page.screenshot({ path: shot("planned-compare.png") });

  // the export of the CSV into a folder of the test (the system dialog of the folder answers at once)
  const out = fs.mkdtempSync(path.join(dir, "export-"));
  await answerOpenDialog(app, [out]);
  await win.getByTestId("pl-export-csv").click();
  await expect(win.getByTestId("pl-saved")).toContainText("выгружена (CSV)", { timeout: 30_000 });
  expect(fs.readdirSync(out).sort()).toEqual(["collar.csv", "survey.csv"]);
  const collar = fs.readFileSync(path.join(out, "collar.csv"), "utf8");
  expect(collar).toContain("ПС-Э19б");
  expect(collar).toContain("200");
  const survey = fs.readFileSync(path.join(out, "survey.csv"), "utf8").trim().split("\n"); // a straight hole: the head and the two stations
  expect(survey.length).toBeGreaterThanOrEqual(3);
  expect(survey.slice(1).every((l) => l.includes("ПС-Э19б"))).toBe(true);
  await closeWindow(page, "planned-window", "pl-close");

  // the hole is not a hole of the set: the set is as it was
  const tables = (await coreApi<{ tables: unknown[] }>(page, "GET", `/drillholes/sets/${ids.set}/tables`)).body.tables;
  expect(tables.length).toBeGreaterThan(0);
  expect((await coreApi<{ sets: { id: number; name: string; holes?: number }[] }>(page, "GET", "/drillholes/sets")).body.sets.some((s) => s.name === SET)).toBe(true);

  const took = Date.now() - started;
  console.log(`implicit stack 4: ${(took / 1000).toFixed(1)} s`);
  expect(took).toBeLessThan(LIMIT(60_000));
});

test("5. Ctrl+K finds the commands of the stack, the faults, the systems of veins and the planned holes by their Russian words", async () => {
  test.setTimeout(LIMIT(60_000));
  for (const query of ["Стек отношений", "Разлом", "Система жил", "Проектная скважина", "План / факт"]) await palette(page, query);
  // a command found runs: «Проектная скважина» opens the window of a new hole
  await palette(page, "Проектная скважина", true);
  await expect(page.getByTestId("planned-window")).toBeVisible({ timeout: 30_000 });
  await closeWindow(page, "planned-window", "pl-close");
  // «Стек отношений» opens the window of the model chosen in the tree
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  await tree.getByTestId(`tree-implicit-${stackModel}`).click();
  await palette(page, "Стек отношений", true);
  await expect(page.getByTestId("implicit-stack")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("implicit-stack").getByTestId("stk-model")).toContainText("Стек Э19б", { timeout: 30_000 });
  await closeWindow(page, "implicit-stack", "stk-close");
});
