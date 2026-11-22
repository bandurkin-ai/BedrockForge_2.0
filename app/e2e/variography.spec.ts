// Э16, подшаг 16.27 (ТЗ 8.6, 7.35 Д-14, Д-15; ADR 0022, решение 25 — одна спецификация): variography end to end
// on the demo (the demo itself is not changed).
//  1. Ctrl+K «Вариография» → the window «Вариография · ‹поле›» with the data of the demo (the active set, «Пробы»,
//     the first number field) → «Рассчитать» (the job) → the curves of the three axes → «Подобрать» → the model in
//     the table.
//  2. ТД14-4: «Эллипсоид в 3D» → the ellipsoid of the working model in the main 3D with the handles; a drag of the
//     ring of the azimuth changes ang1 in the table of the model (and only the angles of the structure chosen) and
//     the curves of the model on the charts.
//  3. ТД14-5: every frame of an edit is measured (`performance.measure("vario:frame")`, ≤ 50 ms in software WebGL);
//     the times are printed (the budget `ellipsoid_drag` of bench is fed from here).
//  4. «Сохранить» → the model in «Модели вариограмм» of the tree; ТД15-5: «Сохранить как тренд» → a trend of the
//     saved model (`model_id`, the structure chosen) in «Тренды» of the tree.
// A window of 1024 × 640 with the ribbon folded; the waits — through the core and `window.__bfVario` (BF_E2E=1);
// ≤ 60 s after the demo under xvfb, the limits on the .exe are ×1.5 (≤ 90 s, ADR 0022 «CI»).
import { expect, test, type Locator, type Page } from "@playwright/test";
import { close, coreApi, launch, onCanvas, openDemo, placeRight, runCommand, setWindowSize, shot, waitFrame } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 1.5 * ms : ms);
/** a frame of an edit in software WebGL, ms (ТД14-5) */
const FRAME_MS = 50;

type Kind = "ang1" | "ang2" | "ang3" | "a1" | "a2" | "a3";
type Point = { x: number; y: number };
type VarioHook = {
  ellipsoids(): { win: string; selected: number; structures: number; samples: number; centre: number[] }[];
  handles(): { shown: { kind: Kind; at: number | number[]; lit: boolean; dimmed: boolean }[]; dragging: Kind | null; hover: Kind | null };
  point(kind: Kind, deg?: number): Point | null;
  frames(): number[];
};
type ModelRow = { id: number; name: string; structures?: unknown };
type TrendRow = { id: number; name: string; origin: { type: string; model_id?: number | null; structure?: number } };

const hook = <T>(page: Page, fn: string, ...args: unknown[]) =>
  page.evaluate(([f, a]) => {
    const h = (window as unknown as { __bfVario?: Record<string, (...x: unknown[]) => unknown> }).__bfVario;
    return (h ? h[f as string](...(a as unknown[])) : null) as never;
  }, [fn, args] as const) as Promise<T | null>;
const ellipsoids = async (page: Page) => (await hook<ReturnType<VarioHook["ellipsoids"]>>(page, "ellipsoids")) ?? [];
const handles = async (page: Page) => (await hook<ReturnType<VarioHook["handles"]>>(page, "handles")) ?? { shown: [], dragging: null, hover: null };
const pointOf = (page: Page, kind: Kind, deg?: number) => (deg === undefined ? hook<Point>(page, "point", kind) : hook<Point>(page, "point", kind, deg));
const frames = async (page: Page) => (await hook<number[]>(page, "frames")) ?? [];
const measures = (page: Page) => page.evaluate(() => performance.getEntriesByName("vario:frame").map((e) => e.duration));
const models = async (page: Page) => (await coreApi<{ models: ModelRow[] }>(page, "GET", "/geostat/variography/models")).body.models;
const trends = async (page: Page) => (await coreApi<{ trends: TrendRow[] }>(page, "GET", "/geostat/trends")).body.trends;

const num = (t: string) => Number(t.replace(/\s/g, "").replace(",", ".").replace("−", "-"));

/** The angles of structure 1 in the table of the model. */
async function angles(win: Locator): Promise<Record<"ang1" | "ang2" | "ang3", number>> {
  const v = async (k: string) => num(await win.getByLabel(`Структура 1: ${k}`, { exact: true }).inputValue());
  return { ang1: await v("ang1"), ang2: await v("ang2"), ang3: await v("ang3") };
}

/** The curves of the model on the charts of the axes (the points of their polylines). */
const curves = (win: Locator) => win.getByTestId("variography-axes").locator("[data-model] polyline").evaluateAll((ls) => ls.map((l) => l.getAttribute("points") ?? ""));

/** Ctrl+K → the command by its title → Enter (the palette closes). */
async function palette(page: Page, query: string): Promise<void> {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toContainText(query);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

/**
 * A drag of the ring of ang1 by `by` degrees along it, in small steps as a hand does it: from a point of the ring on
 * the free canvas (not under the window, a panel or a label; the knobs of the axes win over the ring there) to the
 * point `by` degrees farther. The drag turns by the angle between the press and the pointer, not to an absolute one.
 */
async function dragRing(page: Page, by: number): Promise<number> {
  for (let deg = 0; deg < 360; deg += 15) {
    const from = await pointOf(page, "ang1", deg);
    const to = await pointOf(page, "ang1", deg + by);
    if (!from || !to || !(await onCanvas(page, from.x, from.y)) || !(await onCanvas(page, to.x, to.y))) continue;
    await page.mouse.move(from.x, from.y);
    const lit = await expect
      .poll(async () => (await handles(page)).hover, { timeout: 1_500 })
      .toBe("ang1")
      .then(() => true, () => false);
    if (!lit) continue;
    await page.mouse.down();
    await expect.poll(async () => (await handles(page)).dragging).toBe("ang1");
    await page.mouse.move(to.x, to.y, { steps: 12 });
    await page.mouse.up();
    await expect.poll(async () => (await handles(page)).dragging).toBe(null);
    return deg;
  }
  throw new Error("no free point of the ring of ang1 on the canvas");
}

test("variography: Ctrl+K → «Рассчитать» → «Подобрать»; the ring of ang1 in 3D turns the model and its curves (ТД14-4), frames ≤ 50 ms (ТД14-5); «Сохранить» → «Модели вариограмм», «Сохранить как тренд» → «Тренды» (ТД15-5)", async () => {
  test.setTimeout(LIMIT(150_000));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await openDemo(page);
    const started = Date.now();
    expect(await models(page)).toEqual([]);
    expect(await trends(page)).toEqual([]);

    // ---- 1. Ctrl+K «Вариография» → «Рассчитать» → «Подобрать» ------------------------------------------------------
    await palette(page, "Вариография");
    const win = page.getByRole("dialog").filter({ has: page.getByTestId("variography-window") });
    await expect(win).toHaveCount(1);
    await expect(win.getByLabel("Поле", { exact: true })).not.toHaveValue("", { timeout: 30_000 });
    const field = await win.getByLabel("Поле", { exact: true }).inputValue();
    await expect(win).toHaveAttribute("aria-label", new RegExp(`Вариография · ${field}`));
    await expect(win.getByTestId("variography-calc")).toBeEnabled({ timeout: 30_000 });
    await win.getByTestId("variography-calc").click();
    await expect(win.getByTestId("variography-fit-run")).toBeEnabled({ timeout: LIMIT(60_000) }); // the job done: its key
    await expect(win.getByTestId("variography-axes").locator("[data-series]").first()).toBeVisible();
    console.log(`calc: ${((Date.now() - started) / 1000).toFixed(1)} s`);
    await win.getByTestId("variography-fit-run").click();
    await expect(win.getByTestId("variography-fit")).toBeVisible({ timeout: LIMIT(30_000) });
    await expect(win.getByLabel("Структура 1: ang1", { exact: true })).toBeVisible();
    await expect.poll(async () => (await curves(win)).filter((p) => p !== "").length, { timeout: 10_000 }).toBeGreaterThan(0);
    console.log(`fit: ${((Date.now() - started) / 1000).toFixed(1)} s`);

    // ---- 2. ТД14-4: the ellipsoid in 3D, the ring of ang1 → ang1 in the table and the curves ------------------------
    for (const panel of ["project", "properties"]) expect((await runCommand(page, "panel.set", { panel })).ok).toBe(true);
    await expect(page.getByLabel("Дерево данных")).toHaveCount(0);
    await placeRight(page, win, 460);
    await win.getByRole("button", { name: "Эллипсоид в 3D", exact: true }).click();
    await expect.poll(async () => (await ellipsoids(page)).length, { timeout: 15_000 }).toBe(1);
    await expect.poll(async () => (await handles(page)).shown.map((h) => h.kind).sort(), { timeout: 10_000 }).toEqual(["a1", "a2", "a3", "ang1", "ang2", "ang3"]);
    // «Вписать», then the view moved left of the window (the right button drags the view; the pointer of the scene
    // passes the handles) and a step farther (the wheel)
    await page.keyboard.press("f");
    await waitFrame(page);
    const free = (await win.boundingBox())!.x;
    const canvas = (await page.locator('[data-testid="scene-host"] canvas').boundingBox())!;
    const centre = { x: canvas.x + canvas.width / 2, y: canvas.y + canvas.height / 2 };
    const left = { x: canvas.x + (free - canvas.x) / 2, y: centre.y };
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.down({ button: "right" });
    await page.mouse.move(left.x, left.y, { steps: 6 });
    await page.mouse.up({ button: "right" });
    await page.mouse.wheel(0, 240);
    await waitFrame(page);
    await page.screenshot({ path: shot("variography-ellipsoid.png") });

    const before = await angles(win);
    const curvesBefore = await curves(win);
    const framesBefore = (await frames(page)).length;
    const from = await dragRing(page, 30);
    await expect.poll(async () => (await angles(win)).ang1, { timeout: 10_000 }).not.toBe(before.ang1);
    const after = await angles(win);
    const turned = ((((after.ang1 - before.ang1) % 360) + 540) % 360) - 180;
    console.log(`ang1: ${before.ang1} → ${after.ang1} (${turned.toFixed(1)}° for a drag of 30° along the ring from ${from}°)`);
    expect(Math.abs(Math.abs(turned) - 30), `ang1 turned by ${turned}° for 30°`).toBeLessThanOrEqual(3);
    expect(after.ang2).toBe(before.ang2);
    expect(after.ang3).toBe(before.ang3);
    // the curves of the model follow the model (the axes of the job are those of structure 1)
    await expect.poll(async () => JSON.stringify(await curves(win)), { timeout: LIMIT(30_000) }).not.toBe(JSON.stringify(curvesBefore));
    await waitFrame(page);
    await page.screenshot({ path: shot("variography-ring.png") });

    // ---- 3. ТД14-5: the frames of the edits --------------------------------------------------------------------
    const times = (await frames(page)).slice(framesBefore);
    const marks = await measures(page);
    expect(times.length, "no frame of an edit measured").toBeGreaterThan(0);
    expect(marks.length, "no performance measure «vario:frame»").toBeGreaterThan(0);
    const sorted = [...times].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const max = sorted[sorted.length - 1];
    console.log(`vario:frame: ${times.length} frames, median ${median.toFixed(1)} ms, max ${max.toFixed(1)} ms, measures ${marks.length}`);
    expect(median, `the median frame of an edit ${median.toFixed(1)} ms`).toBeLessThanOrEqual(LIMIT(FRAME_MS));

    // ---- 4. «Сохранить» → «Модели вариограмм»; «Сохранить как тренд» → «Тренды» ---------------------------------
    await win.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect.poll(async () => (await models(page)).length, { timeout: 15_000 }).toBe(1);
    const model = (await models(page))[0];
    await expect(win.getByRole("button", { name: "Экспорт", exact: true })).toBeEnabled({ timeout: 10_000 }); // the window holds the saved model
    expect((await runCommand(page, "panel.set", { panel: "project" })).ok).toBe(true);
    const modelRow = page.getByTestId(`tree-vario-model-${model.id}`);
    await modelRow.scrollIntoViewIfNeeded();
    await expect(modelRow).toBeVisible({ timeout: 15_000 });
    await expect(modelRow).toContainText(model.name);

    await win.getByRole("button", { name: "Сохранить как тренд", exact: true }).click();
    await expect.poll(async () => (await trends(page)).length, { timeout: 15_000 }).toBe(1);
    const trend = (await trends(page))[0];
    // the saved model unchanged: a copy of its structure chosen (`model_id`, `structure`), not the bare axes
    expect(trend.origin).toMatchObject({ type: "model", model_id: model.id, structure: 1 });
    const trendRow = page.getByTestId(`tree-trend-${trend.id}`);
    await trendRow.scrollIntoViewIfNeeded();
    await expect(trendRow).toBeVisible({ timeout: 15_000 });
    await expect(trendRow).toContainText(trend.name);
    await page.screenshot({ path: shot("variography-saved.png") });

    const seconds = (Date.now() - started) / 1000;
    console.log(`variography.spec: ${seconds.toFixed(1)} s after the demo`);
    expect(seconds).toBeLessThan(LIMIT(60));
  } finally {
    await close(app);
  }
});
