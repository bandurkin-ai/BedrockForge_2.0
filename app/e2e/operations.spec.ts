// Э9, подшаг 9.36 (ТЗ 7.23 «Приёмка»; ADR 0015 §6): the operations with lines and wireframes end to end, in the
// windows of the operations. Two contours 10 × 10 ten metres apart with flat caps → a closed body of 1000 m³;
// cube − cube → a closed body of 875 m³ (and its «Свойства»: the operation, the sources, the parameters, the
// tolerances); a boolean with an open wireframe does not start — the refusal names its open edges and
// «Исправить каркас» opens the repair of it, which closes it; «Перестроить» after an edit of a contour in the
// string editor → a new object with the new volume, the old one unchanged; both operations of the second test run
// while a long job of the shared queue (`diag.sleep`, standing for a long import) goes on. The data — layers of
// contours — is written straight through the core; no clicks in the 3D window (the small window of Windows CI).
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, launch, saveLines, setWindowSize, shot, waitFrame, waitJob, type CoreJob } from "./helpers";

// The cache of numba of both launches (10.11): the first one starts cold (its operations wait for the kernels the
// workers are compiling — reported), the second one is the start of a program started before — its first operation
// is held to FIRST_S on Linux (Windows: reported; was ~13 s, the kernels of the stitch compiled while it waited).
const NUMBA = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-numba-"));
// 3 s is the budget of the operation itself (bench `ops_first`); here it is measured through the window and the
// polling of the job on a loaded CI runner (2,6 s seen at load 9,6) — twice the budget, still far below the 13 s it was
const FIRST_S = 6;
let primed = false; // the first launch went to its end: its workers filled the cache
test.afterAll(() => fs.rmSync(NUMBA, { recursive: true, force: true }));

type Wf = { id: number; name: string; version: number; stats: { closed: boolean; open_edges: number; volume: number | null }; built?: { op?: string; rebuild_of?: number | null; sources?: { id: number | null; version?: number }[] } | null };
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
const wireframes = async (page: Page) => (await coreApi<{ wireframes: Wf[] }>(page, "GET", "/wireframes")).body.wireframes;
const wireframe = async (page: Page, name: string) => {
  const w = (await wireframes(page)).find((x) => x.name === name);
  expect(w, `no wireframe «${name}»`).toBeTruthy();
  return w!;
};
const VOLUME = (v: string) => new RegExp(`объём ${v.replace(" ", "\\s")} м³`); // ru-RU groups by a no-break space

async function emptyProject(page: Page, name: string): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill(name);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText(name, { timeout: 60_000 });
  await waitFrame(page);
}

/** A square side × side in the plane Z = z with its corner at (x, y). */
const square = (x: number, y: number, z: number, side = 10) => ({ points: [[x, y, z], [x + side, y, z], [x + side, y + side, z], [x, y + side, z]], closed: true });

/** A new layer of lines with the closed squares (version 2); the interface knows it when the tree shows it. */
async function contours(page: Page, name: string, squares: ReturnType<typeof square>[]): Promise<number> {
  const made = await coreApi<{ id: number }>(page, "POST", "/lines/layers", { name });
  expect(made.status, JSON.stringify(made.body)).toBe(201);
  expect(await saveLines(page, made.body.id, 1, squares)).toBe(2);
  await expect(page.getByTestId(`tree-layer-${made.body.id}`)).toBeVisible({ timeout: 30_000 });
  return made.body.id;
}

/** `POST /operations` straight through the core and its job to the end (the objects the tests need beside). */
async function operation(page: Page, op: string, params: Record<string, unknown>, name: string): Promise<Wf> {
  const started = await coreApi<{ source: string; id: number }>(page, "POST", "/operations", { op, params, name });
  expect(started.status, JSON.stringify(started.body)).toBe(200);
  const job = await waitJob(page, started.body.source, started.body.id);
  expect(job.status, JSON.stringify(job.error)).toBe("done");
  return wireframe(page, name);
}

const opWindow = (page: Page, op: string) => page.locator(`[data-testid="op-window"][data-op="${op}"]`);

/** «Выполнить» of the window → the window follows the job to «Выполнено»; the report opens by itself. → seconds. */
async function execute(page: Page, op: string): Promise<number> {
  const win = opWindow(page, op);
  await expect(win.getByTestId("op-check")).toBeVisible({ timeout: 30_000 }); // the preconditions were checked
  await expect(win.getByTestId("op-run")).toBeEnabled();
  const t0 = Date.now();
  await win.getByTestId("op-run").click();
  const follow = page.getByTestId("op-follow");
  await expect(follow).toHaveAttribute("data-status", "done", { timeout: 120_000 });
  const s = (Date.now() - t0) / 1000;
  await expect(page.getByTestId("op-report")).toBeVisible({ timeout: 30_000 });
  return s;
}

/** Closes the window of the job and the report (they cover the tree on a small window). */
async function closeWindows(page: Page): Promise<void> {
  // the report opens over the window of the job: it goes first
  for (const r of await page.getByTestId("op-report").all()) await r.getByRole("button", { name: "Закрыть", exact: true }).click();
  const follow = page.getByTestId("op-follow");
  if (await follow.count()) await follow.getByTestId("op-close").click();
  await expect(page.getByTestId("op-follow")).toHaveCount(0);
  await expect(page.getByTestId("op-report")).toHaveCount(0);
}

/** «Сшить контуры» of the whole layer (prefilled by `layer_id`), flat caps, the name given. → seconds of the job. */
async function stitchInWindow(page: Page, layerId: number, layerName: string, name: string): Promise<number> {
  expect((await run(page, "model.stitch", { layer_id: layerId })).ok).toBe(true);
  const win = opWindow(page, "stitch");
  await expect(win).toBeVisible();
  await expect(win.getByTestId("op-field-contours").getByLabel(`Слой «${layerName}»`)).toBeChecked();
  await expect(win.getByTestId("op-field-start_cap").getByRole("combobox")).toHaveValue("flat");
  await expect(win.getByTestId("op-field-end_cap").getByRole("combobox")).toHaveValue("flat");
  await win.getByTestId("op-name").fill(name);
  const s = await execute(page, "stitch");
  await expect(win).toHaveCount(0); // the window became the one that follows the job
  return s;
}

/** The wireframe chosen in the tree: «Свойства» in the right panel. */
async function props(page: Page, w: Wf) {
  const tree = page.getByRole("tree", { name: "Разделы и объекты проекта" });
  const row = tree.getByTestId(`tree-wireframe-${w.id}`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.getByText(w.name, { exact: true }).click();
  const panel = page.getByLabel("Правая панель").getByTestId("props-wireframe");
  await expect(panel).toHaveAttribute("aria-label", `Свойства: ${w.name}`);
  return panel;
}

test("operations: two contours → 1000 m³, cube − cube, a boolean with an open wireframe is refused and fixed", async () => {
  test.setTimeout(420_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1", NUMBA_CACHE_DIR: NUMBA });
  const times: string[] = [];
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    await emptyProject(page, "Операции с каркасами");
    const layerA = await contours(page, "Контуры A", [square(1000, 1000, 0), square(1000, 1000, 10)]);
    const layerB = await contours(page, "Контуры B", [square(1005, 1005, 5), square(1005, 1005, 15)]);
    const layerT = await contours(page, "Контуры трубы", [square(1100, 1000, 0), square(1100, 1000, 10)]);

    // ---- two contours 10 × 10 ten metres apart, flat caps → a closed body of 1000 m³ ---------------------------
    times.push(`сшивка ${(await stitchInWindow(page, layerA, "Контуры A", "Куб A")).toFixed(1)} с`);
    await expect(page.getByTestId("op-report")).toHaveAttribute("data-op", "stitch");
    await expect(page.getByTestId("op-report-made")).toContainText("«Куб A»");
    await expect(page.getByTestId("op-report-made")).toContainText(VOLUME("1 000"));
    const a = await wireframe(page, "Куб A");
    expect(a.stats.closed).toBe(true);
    expect(a.stats.volume!).toBeCloseTo(1000, 6);
    await page.screenshot({ path: shot("operations-stitch.png") });
    await closeWindows(page);

    // ---- cube − cube: A [1000…1010]³ − B [1005…1015]³ → 1000 − 125 = 875 m³ --------------------------------------
    const b = await operation(page, "stitch", { contours: [{ layer_id: layerB }] }, "Куб B");
    expect(b.stats.volume!).toBeCloseTo(1000, 6);
    expect((await run(page, "model.boolean", { wireframe_id: a.id })).ok).toBe(true);
    const bool = opWindow(page, "boolean");
    await expect(bool.getByTestId("op-field-a").getByRole("combobox")).toHaveValue(String(a.id));
    await expect(bool.getByTestId("op-field-op").getByRole("combobox")).toHaveValue("difference");
    await bool.getByTestId("op-field-b").getByLabel("Каркас «Куб B»").check();
    await bool.getByTestId("op-name").fill("A − B");
    await expect(bool.getByTestId("op-check")).toContainText("«Куб A»");
    await expect(bool.getByTestId("op-check")).toContainText("«Куб B»");
    times.push(`булева ${(await execute(page, "boolean")).toFixed(1)} с`);
    await expect(page.getByTestId("op-report-made")).toContainText(VOLUME("875"));
    const diff = await wireframe(page, "A − B");
    expect(diff.stats.closed).toBe(true);
    expect(diff.stats.volume!).toBeCloseTo(875, 6);
    expect((await wireframe(page, "Куб A")).version).toBe(a.version); // the sources are not changed
    expect((await wireframe(page, "Куб B")).version).toBe(b.version);
    await closeWindows(page);

    // ---- «Свойства» of the result: the operation, the sources, the parameters, the tolerances ---------------------
    const panel = await props(page, diff);
    const built = panel.getByTestId("props-built");
    await expect(built).toHaveAttribute("data-op", "boolean");
    await expect(built.getByTestId("props-built-sources")).toContainText("«Куб A»");
    await expect(built.getByTestId("props-built-sources")).toContainText("«Куб B»");
    await expect(built.getByTestId("props-built-params")).toContainText("Вычитание");
    await expect(built.getByTestId("props-built-tolerances")).toBeVisible();
    await expect(built.getByTestId("props-rebuild")).toBeVisible();
    await page.screenshot({ path: shot("operations-props.png") });

    // ---- a boolean with an open wireframe does not start: the refusal names its open edges -------------------------
    const tube = await operation(page, "stitch", { contours: [{ layer_id: layerT }], start_cap: "none", end_cap: "none" }, "Труба");
    expect(tube.stats.closed).toBe(false);
    const edges = tube.stats.open_edges;
    expect(edges).toBeGreaterThan(0);
    const operations = async () => (await coreApi<{ jobs: CoreJob[] }>(page, "GET", "/jobs")).body.jobs.filter((j) => j.kind.startsWith("geometry.operation")).length;
    const jobsBefore = await operations();
    expect((await run(page, "model.boolean", { wireframe_id: tube.id })).ok).toBe(true);
    await expect(bool.getByTestId("op-field-a").getByRole("combobox")).toHaveValue(String(tube.id));
    await bool.getByTestId("op-field-b").getByLabel("Каркас «Куб B»").check();
    const refusal = bool.getByTestId("op-refusal");
    await expect(refusal).toContainText("«Труба»");
    await expect(refusal).toContainText(`открытых рёбер — ${edges}`);
    await expect(bool.getByTestId("op-check")).toHaveCount(0);
    await expect(bool.getByTestId("op-run")).toBeDisabled();
    // the core refuses it too (the check of the window and `model.operation`): nothing is queued
    const checked = await coreApi<{ code: string; message: string; open_edges: number; fix: { op: string; wireframe_id: number } }>(page, "POST", "/operations/check", { op: "boolean", params: { op: "difference", a: tube.id, b: [b.id] } });
    expect(checked.status).toBe(422);
    expect(checked.body).toMatchObject({ code: "wireframe_open", open_edges: edges, fix: { op: "repair", wireframe_id: tube.id } });
    expect(checked.body.message).toContain(`открытых рёбер — ${edges}`);
    expect(await operations()).toBe(jobsBefore);
    await page.screenshot({ path: shot("operations-refusal.png") });

    // ---- «Исправить каркас» opens the repair of that wireframe; «Закрыть отверстия» makes it a closed body --------
    await bool.getByTestId("op-fix").click();
    const repair = opWindow(page, "repair");
    await expect(repair).toBeVisible();
    await expect(repair.getByTestId("op-field-wireframe_id").getByRole("combobox")).toHaveValue(String(tube.id));
    await expect(repair.getByTestId("op-field-fill_holes").getByRole("checkbox")).toBeChecked();
    await repair.getByTestId("op-name").fill("Труба исправленная");
    times.push(`исправление ${(await execute(page, "repair")).toFixed(1)} с`);
    await expect(page.getByTestId("op-report-compare")).toBeVisible();
    await expect(page.getByTestId("op-report-compare").locator('tr[data-changed="yes"]').first()).toBeVisible();
    const fixed = await wireframe(page, "Труба исправленная");
    expect(fixed.stats.closed).toBe(true);
    expect(fixed.stats.volume!).toBeCloseTo(1000, 6);
    expect((await wireframe(page, "Труба")).stats.closed).toBe(false); // the source is not changed
    await page.screenshot({ path: shot("operations-repair.png") });
    primed = true;
  } finally {
    await close(app);
  }
  console.log(`operations: stitch, boolean, refusal, repair (cold cache) — ${times.join(", ")}; всего ${((Date.now() - started) / 1000).toFixed(1)} с`);
});

test("operations: «Перестроить» after an edit of a contour, while a long job of the shared queue runs", async () => {
  test.setTimeout(420_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1", NUMBA_CACHE_DIR: NUMBA });
  const times: string[] = [];
  let sleep: { source: string; id: number } | null = null;
  try {
    await setWindowSize(app, 1024, 640);
    await emptyProject(page, "Перестроить");
    const layer = await contours(page, "Контуры", [square(2000, 2000, 0), square(2000, 2000, 10)]);

    // ---- a long job of the shared queue (a long import): up to 5 minutes, cancelled at the end -----------------
    const submitted = await coreApi<{ source: string; id: number }>(page, "POST", "/jobs", { kind: "diag.sleep", params: { steps: 3000, step_s: 0.1 }, title: "Долгий импорт (проба)" });
    expect(submitted.status, JSON.stringify(submitted.body)).toBe(200);
    sleep = submitted.body;
    await waitJob(page, sleep.source, sleep.id, ["running"], 60_000);
    const stillRunning = async () => expect((await coreApi<CoreJob>(page, "GET", `/jobs/${sleep!.source}/${sleep!.id}`)).body.status).toBe("running");

    // ---- the operation does not wait for it ------------------------------------------------------------------------
    const first = await stitchInWindow(page, layer, "Контуры", "Рудное тело");
    times.push(`сшивка ${first.toFixed(1)} с`);
    expect(first, "the operation waited for the long job").toBeLessThan(90);
    if (primed && process.platform === "linux") expect(first, "the first operation after a start").toBeLessThanOrEqual(FIRST_S);
    await stillRunning();
    await expect(page.getByTestId("op-report-made")).toContainText(VOLUME("1 000"));
    const old = await wireframe(page, "Рудное тело");
    expect(old.stats.volume!).toBeCloseTo(1000, 6);
    expect(old.built?.sources?.[0]?.version).toBe(2);
    await closeWindows(page);

    // ---- an edit of the contours in the string editor: a third contour 10 m above, typed in -------------------------
    expect((await run(page, "lines.edit", { layer_id: layer })).ok).toBe(true);
    await expect(page.getByTestId("edit-panel")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("edit-version")).toHaveText("в. 2");
    await page.getByTestId("edit-tool-polygon").click();
    const coord = page.getByTestId("edit-coord");
    for (const t of ["2000 2000 20", "@@10 0", "@@0 10", "@@-10 0"]) {
      await coord.fill(t);
      await coord.press("Enter");
      await expect(page.getByTestId("edit-coord-error")).toHaveCount(0);
    }
    await page.getByTestId("edit-modify-finish_line").click();
    await expect(page.getByTestId("edit-measures")).toContainText("площадь 100 м²");
    await page.getByTestId("edit-layer").click(); // Ctrl+S from the panel, not from a field of input
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("edit-version")).toHaveText("в. 3", { timeout: 30_000 });
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);

    // ---- «Перестроить» in «Свойства»: a new object of 2000 m³, the old one is as it was -----------------------------
    const panel = await props(page, old);
    const built = panel.getByTestId("props-built");
    await expect(built).toHaveAttribute("data-op", "stitch");
    await expect(built.getByTestId("props-built-sources")).toContainText("«Контуры»");
    await expect(built.getByTestId("props-built-sources")).toContainText("версия 2");
    await expect(built.getByTestId("props-built-params")).toContainText("Плоская крышка");
    const t0 = Date.now();
    await built.getByTestId("props-rebuild").click();
    const report = page.getByTestId("op-report");
    await expect(report).toBeVisible({ timeout: 120_000 });
    const second = (Date.now() - t0) / 1000;
    times.push(`перестроить ${second.toFixed(1)} с`);
    expect(second, "the rebuild waited for the long job").toBeLessThan(90);
    await stillRunning();
    await expect(report).toContainText(`Перестроено: прежний объект №${old.id} не изменён.`);
    await expect(page.getByTestId("op-report-made")).toContainText(VOLUME("2 000"));
    const made = await wireframe(page, "Рудное тело (2)");
    expect(made.id).not.toBe(old.id);
    expect(made.stats.closed).toBe(true);
    expect(made.stats.volume!).toBeCloseTo(2000, 6);
    expect(made.built).toMatchObject({ op: "stitch", rebuild_of: old.id });
    expect(made.built?.sources?.[0]?.version).toBe(3);
    const now = await wireframe(page, "Рудное тело");
    // the old one is as it was (its `stats.checks` may come meanwhile from the queued `wireframe.check`)
    const same = (w: Wf) => ({ version: w.version, closed: w.stats.closed, open_edges: w.stats.open_edges, volume: w.stats.volume, built: w.built });
    expect(same(now)).toEqual(same(old));
    await page.screenshot({ path: shot("operations-rebuild.png") });
    for (const r of await report.all()) await r.getByRole("button", { name: "Закрыть", exact: true }).click();

    // «Свойства» of the new one: rebuilt from the old one
    const next = await props(page, made);
    await expect(next.getByTestId("props-built")).toContainText(`№${old.id}`);
    await expect(next.getByTestId("props-built-sources")).toContainText("версия 3");
  } finally {
    if (sleep) {
      await coreApi(page, "POST", `/jobs/${sleep.source}/${sleep.id}/cancel`).catch(() => undefined);
      await waitJob(page, sleep.source, sleep.id, ["cancelled", "done", "failed"], 30_000).catch(() => undefined);
    }
    await close(app);
  }
  console.log(`operations: rebuild beside diag.sleep${primed ? " (the cache of the first launch)" : ""} — ${times.join(", ")}; всего ${((Date.now() - started) / 1000).toFixed(1)} с`);
});
