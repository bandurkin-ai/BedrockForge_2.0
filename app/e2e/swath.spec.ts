// Э13, подшаг 13.34 (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5–6): swath plot end to end on the demo (the demo itself is not
// changed: the lesson of Э12 — a cold numba).
//  1. «+» of «Аудит и статистика» → the swath by default (the model of the demo against «Скважины 2025») saved in the
//     tree, its window «Swath plot · …»; «Полосы в 3D» — the cuboid and its swaths in the main 3D with the handles.
//  2. ТД01-4: a drag of each handle (azimuth, dip, roll) changes its own angle in the window and only it; the release
//     saves the settings (direction «по углам»); a number typed into «Азимут» turns the box in 3D.
//  3. Т7.30-3: the swath under the cursor of the plot is lit in 3D, and none once the cursor leaves the plot.
//  4. ТД01-3: with the window open and its swath chosen (the handles drawn) `T`, a click on a collar and `Ctrl+K` work
//     as without it.
//  5. A small block model (OMF v1 written here, 4 × 4 × 3 cells, half of them empty) through the import of the core:
//     a series of it and a series of the samples «внутри блоков» of it — fewer samples than in the whole cuboid, the
//     row of the series in the table of the window.
// A window of 1024 × 640 with the ribbon folded; the data and the waits for lists — through the core; the limits on
// the .exe are ×3.
import { expect, test, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, coreApi, importFiles, launch, onCanvas, openDemo, placeRight, runCommand, setWindowSize, shot, waitFrame, writeOmfBlocks } from "./helpers";

const EXE = !!process.env.BF_APP_EXE;
const LIMIT = (ms: number) => (EXE ? 3 * ms : ms);

type Kind = "azimuth" | "dip" | "roll";
type Point = { x: number; y: number };
type SwathHook = {
  layers(): { id: number; count: number; lit: number | null; chart: boolean }[];
  handles(): { shown: { kind: Kind; at: number; lit: boolean }[]; dragging: Kind | null };
  axis(id: number): { u: number[]; posed: boolean } | null;
  point(kind: Kind, deg: number): Point | null;
};
type Chart = { id: number; name: string; kind: string; version: number; settings: Record<string, unknown> };
type Answer = { direction: string; count: number; series: { name: string; kind: string; within?: number | "box" | null; total: { count: number; mean: number | null } }[] };

const layers = (page: Page) => page.evaluate(() => (window as unknown as { __bfSwath?: SwathHook }).__bfSwath?.layers() ?? []);
const handles = (page: Page) => page.evaluate(() => (window as unknown as { __bfSwath?: SwathHook }).__bfSwath?.handles() ?? { shown: [], dragging: null });
const axis = (page: Page, id: number) => page.evaluate((i) => (window as unknown as { __bfSwath?: SwathHook }).__bfSwath?.axis(i) ?? null, id);
const pointOf = (page: Page, kind: Kind, deg: number) => page.evaluate(([k, d]) => (window as unknown as { __bfSwath?: SwathHook }).__bfSwath?.point(k as Kind, d as number) ?? null, [kind, deg] as const);
const swaths = async (page: Page) => (await coreApi<{ charts: Chart[] }>(page, "GET", "/charts?kind=swath")).body.charts;
const state = (page: Page) => page.evaluate(() => window.__bfScene!.state());

/** The three angles in the fields of the window. */
async function angles(win: Locator): Promise<Record<Kind, string>> {
  const v = (label: string) => win.getByLabel(label, { exact: true }).inputValue();
  return { azimuth: await v("Азимут, °"), dip: await v("Наклон, °"), roll: await v("Поворот, °") };
}

const num = (t: string) => Number(t.replace(",", ".").replace("−", "-"));

/**
 * A drag of a handle from its knob by `by` degrees along it (the point of the handle at the new angle on the screen),
 * in small steps as a hand does it; both ends must be on the canvas, not under the window or a panel.
 */
async function dragHandle(page: Page, kind: Kind, by: number): Promise<void> {
  const at = (await handles(page)).shown.find((h) => h.kind === kind)!.at;
  const from = await pointOf(page, kind, at);
  const to = await pointOf(page, kind, at + by);
  expect(from && to, `the handle «${kind}» is not on the screen`).toBeTruthy();
  expect(await onCanvas(page, from!.x, from!.y), `the knob of «${kind}» is covered`).toBe(true);
  expect(await onCanvas(page, to!.x, to!.y), `the point of «${kind}» at ${at + by}° is covered`).toBe(true);
  await page.mouse.move(from!.x, from!.y);
  await expect.poll(async () => (await handles(page)).shown.find((h) => h.kind === kind)?.lit, { timeout: 5_000 }).toBe(true);
  await page.mouse.down();
  await expect.poll(async () => (await handles(page)).dragging).toBe(kind);
  await page.mouse.move(to!.x, to!.y, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await handles(page)).dragging).toBe(null);
}

test("swath: «+» → the window and the box in 3D; each handle its angle, a typed azimuth turns the box (ТД01-4); the swath lit in 3D (Т7.30-3); T, a collar, Ctrl+K (ТД01-3); a small OMF model and samples «внутри блоков»", async () => {
  test.setTimeout(LIMIT(120_000));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-swath-"));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await openDemo(page);

    // ---- 1. «+» of «Аудит и статистика» → a swath saved in the tree, its window ------------------------------------
    const section = page.getByTestId("tree-section-audit");
    await section.scrollIntoViewIfNeeded();
    await section.getByRole("button", { name: "Новый swath plot" }).click();
    await expect.poll(async () => (await swaths(page)).length, { timeout: 30_000 }).toBe(1);
    const row = (await swaths(page))[0];
    const win = page.getByRole("dialog").filter({ has: page.getByTestId("swath-window") });
    await expect(win).toHaveCount(1);
    await expect(win).toHaveAttribute("aria-label", row.name.startsWith("Swath plot") ? row.name : `Swath plot · ${row.name}`);
    await expect(page.getByTestId(`tree-swath-${row.id}`)).toBeVisible();
    await expect(win.getByTestId("swath-table")).toBeVisible({ timeout: 30_000 });
    const first = await angles(win);
    // the 3D over the left of the window: the tree and «Свойства» closed, the window narrowed to the right edge
    for (const panel of ["project", "properties"]) expect((await runCommand(page, "panel.set", { panel })).ok).toBe(true);
    await expect(page.getByLabel("Дерево данных")).toHaveCount(0);
    await placeRight(page, win, 420);
    await win.getByLabel("Полосы в 3D").check();
    await expect.poll(async () => (await layers(page)).find((l) => l.id === row.id)?.count ?? 0, { timeout: 30_000 }).toBeGreaterThan(1);
    // the window chosen: the handles at its angles
    await win.getByText("Полосы", { exact: true }).click();
    await expect.poll(async () => (await handles(page)).shown.map((h) => h.kind), { timeout: 10_000 }).toEqual(["azimuth", "dip", "roll"]);
    // «Вписать» (the box and its plot in the view), then the view moved left of the window and a step farther:
    // the right button drags the view, the wheel zooms — the pointer of the scene past the handles
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
    await page.screenshot({ path: shot("swath-open.png") });

    // ---- 2. ТД01-4: each handle — its own angle; the release saves «по углам» -------------------------------------
    let before = await angles(win);
    expect(before).toEqual(first);
    for (const [kind, by] of [["azimuth", 25], ["dip", 20], ["roll", 30]] as [Kind, number][]) {
      await dragHandle(page, kind, by);
      await expect.poll(async () => num((await angles(win))[kind]), { timeout: 10_000 }).not.toBe(num(before[kind]));
      const now = await angles(win);
      const turned = num(now[kind]) - num(before[kind]);
      expect(Math.abs(turned - by), `«${kind}» turned by ${turned}° for ${by}°`).toBeLessThanOrEqual(3);
      for (const other of ["azimuth", "dip", "roll"] as Kind[]) if (other !== kind) expect(now[other], `«${other}» after «${kind}»`).toBe(before[other]);
      // saved and drawn by the new angles (the box built again from the answer, no pose held)
      await expect.poll(async () => ((await swaths(page))[0].settings as Record<Kind, number>)[kind], { timeout: 10_000 }).toBe(num(now[kind]));
      await expect.poll(async () => (await axis(page, row.id))?.posed, { timeout: 15_000 }).toBe(false);
      await waitFrame(page);
      before = now;
    }
    expect((await swaths(page))[0].settings.direction).toBe("angles");
    await page.screenshot({ path: shot("swath-handles.png") });

    // a number typed: the box turns to it (the axis of the swaths in 3D by the new azimuth)
    const field = win.getByLabel("Азимут, °", { exact: true });
    await field.fill("45");
    const dip = (num(before.dip) * Math.PI) / 180;
    const want = [Math.cos(dip) * Math.SQRT1_2, Math.cos(dip) * Math.SQRT1_2, Math.sin(dip)];
    await expect
      .poll(async () => {
        const u = (await axis(page, row.id))?.u ?? [0, 0, 0];
        return Math.max(...u.map((x, i) => Math.abs(x - want[i])));
      }, { timeout: 15_000 })
      .toBeLessThan(1e-9);
    await expect.poll(async () => (await handles(page)).shown.find((h) => h.kind === "azimuth")?.at).toBe(45);
    await waitFrame(page);

    // ---- 3. Т7.30-3: the swath under the cursor of the plot is lit in 3D --------------------------------------------
    const lit = async () => (await layers(page)).find((l) => l.id === row.id)?.lit ?? null;
    const plot = win.locator("svg rect[fill=transparent]").first();
    await plot.scrollIntoViewIfNeeded();
    const count = (await layers(page)).find((l) => l.id === row.id)!.count;
    const box = (await plot.boundingBox())!;
    for (const k of [1, count - 2]) {
      await page.mouse.move(box.x + ((k + 0.5) / count) * box.width, box.y + box.height / 2);
      await expect.poll(lit, { timeout: 5_000 }).toBe(k);
    }
    await page.screenshot({ path: shot("swath-lit.png") });
    await page.mouse.move(box.x + box.width / 2, box.y - 40);
    await expect.poll(lit, { timeout: 5_000 }).toBe(null);

    // ---- 4. ТД01-3: T, a click on a collar, Ctrl+K — the window open, its swath chosen ------------------------------
    expect((await handles(page)).shown).toHaveLength(3);
    await win.getByText("Полосы", { exact: true }).click(); // the focus in the window, not in a field
    await page.keyboard.press("t");
    await waitFrame(page);
    expect((await state(page)).camera.dir[2]).toBeGreaterThan(0.999);
    const collars = await page.evaluate(() => window.__bfScene!.collars(2000));
    const open: { name: string; x: number; y: number; d: number }[] = [];
    for (const c of collars) {
      if (!(await onCanvas(page, c.x, c.y))) continue;
      open.push({ ...c, d: Math.min(...collars.filter((o) => o !== c).map((o) => Math.hypot(o.x - c.x, o.y - c.y))) });
    }
    const iso = open.sort((a, b) => b.d - a.d)[0];
    expect(iso, "no collar out of the window").toBeTruthy();
    await page.mouse.click(iso.x, iso.y);
    await expect(page.getByRole("contentinfo")).toContainText(`Выбрана: ${iso.name}`, { timeout: 10_000 });
    expect((await handles(page)).dragging).toBe(null);
    await page.keyboard.press("Control+KeyK");
    await expect(page.getByLabel("Поиск команды")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
    await expect(win).toHaveCount(1);

    // ---- 5. a small OMF model through the import; the samples «внутри блоков» of it ---------------------------------
    const models0 = (await coreApi<{ block_models: { id: number }[] }>(page, "GET", "/blocks")).body.block_models.length;
    const counts: [number, number, number] = [4, 4, 3];
    const values = Array.from({ length: 48 }, (_, i) => ((i + Math.floor(i / 4) + Math.floor(i / 16)) % 2 ? 0.5 + i / 10 : NaN));
    const file = writeOmfBlocks(path.join(dir, "Малая модель.omf"), { name: "Малая модель", corner: [5000, 8000, 300], size: [50, 50, 40], counts, fields: [{ name: "AU", values }] });
    await importFiles(page, [file]);
    await expect.poll(async () => (await coreApi<{ block_models: { id: number; name: string; cells: number }[] }>(page, "GET", "/blocks")).body.block_models.filter((b) => b.name === "Малая модель").map((b) => b.cells), { timeout: 30_000 }).toEqual([24]);
    expect((await coreApi<{ block_models: unknown[] }>(page, "GET", "/blocks")).body.block_models.length).toBe(models0 + 1);
    // the series: + the model, + the samples of the set; the samples inside the blocks of the new series
    const series = () => win.locator('[data-testid^="swath-series-"]');
    const n0 = await series().count();
    const addSeries = async (item: RegExp) => {
      await win.getByRole("button", { name: "+ Добавить ряд" }).click();
      await win.getByRole("menuitem", { name: item }).click();
    };
    await addSeries(/^Малая модель · модель$/);
    await expect(series()).toHaveCount(n0 + 1);
    await addSeries(/· пробы$/);
    await expect(series()).toHaveCount(n0 + 2);
    const samples = win.getByTestId(`swath-series-${n0 + 1}`);
    await samples.getByLabel("Отбор проб").selectOption({ label: `внутри блоков ряда ${n0 + 1}` });
    // saved, then computed by its id: the samples inside the blocks — fewer than in the cuboid, not none
    await expect.poll(async () => ((await swaths(page))[0].settings as { series: unknown[] }).series.length, { timeout: 15_000 }).toBe(n0 + 2);
    const r = await coreApi<Answer>(page, "POST", "/stats/swath", { swath_id: row.id });
    expect(r.status, JSON.stringify(r.body)).toBe(200);
    const inside = r.body.series[n0 + 1];
    const all = r.body.series.find((s, i) => i < n0 && s.kind === "samples")!;
    expect(inside.within).toBe(n0);
    expect(inside.total.count).toBeGreaterThan(0);
    expect(inside.total.count).toBeLessThan(all.total.count);
    // the row of the series in the table of the window
    await expect(win.getByTestId("swath-table")).toContainText(inside.name, { timeout: 15_000 });
    await page.screenshot({ path: shot("swath-inside.png") });
  } finally {
    await close(app);
  }
});
