// Э10, подшаг 10.26 (ТЗ 8.1; ADR 0016): series of sections end to end in the real application.
//  1. An empty project (as editor.spec), a window of 1024 × 640 (the Windows runner): «Новая серия» by the case
//     Т8.1-1 (A = 90°, P1 = (1000; 5000), s0 = 0, s1 = 100, Δ = 25) → K = 5 and the names 5000N … 5100N in the status
//     line, «Менеджер серий» and its table, the mini-plan; `]`, `[`, `End`, `]` on the last one — k stays and the
//     message (Т8.1-8), `Home`, `Ctrl+G` with the hints; `L` — the view along v̂, `]` keeps a point with the same
//     (u, w) in its place (Т8.1-6); the shadow «предыдущий»; «Контур на разрезе» on three sections by typed
//     coordinates (squares 20 × 20 m, Ctrl+S on each, `]` between them) — the shadow shows the contour of the
//     section before; «Каркас по серии» → one body of three contours → a closed wireframe of 30 000 m³ (the default
//     caps reach Δ/2 beyond the end sections, as Т8.1-4); the tree «Серии разрезов» — a click goes to the section.
//  2. The demo project: «Серия через скважину» of the most inclined hole — every station of the hole is in the window of the section and drawn
//     (Т8.1-7); the step `]` timed to the mark of the data of the section (`Scene.debug().sectionData`) on a series of
//     26 sections over the ore body, cold and prefetched (k + 1 read in idle time) → bench/out/series_step.json
//     (budgets.json → external.series_step). The shadow of the ore series: the contours of the section before.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { close, coreApi, launch, newLineLayer, saveLines, setWindowSize, shot, waitFrame } from "./helpers";

const ROOT = path.resolve(__dirname, "..", "..");

type Mark = { series: number | null; k: number | null; seg: number | null; ready: boolean; ms: number | null; prepared: boolean; kept: number };
type Extra = { locked: boolean; sectionData: Mark | null; exact: { sections: { wireframe: number; lines: number }[]; series: Mark } };
type Row = { id: number; name: string; version: number; number: number; count: number; params: Record<string, unknown> };
type Wf = { id: number; name: string; stats: { closed: boolean; volume: number | null }; built?: { op?: string } | null };
type EdState = { layerId: number; version: number; dirty: boolean; lines: number };

const state = (page: Page) => page.evaluate(() => window.__bfScene!.state() as unknown as BfSceneState & Extra);
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);
const edState = (page: Page) => page.evaluate(() => (window as unknown as { __bfEditor: { state(): EdState | null } }).__bfEditor.state());
const seriesList = async (page: Page) => (await coreApi<{ series: Row[] }>(page, "GET", "/section-series")).body.series;
const status = (page: Page) => page.getByTestId("series-status");
const note = (page: Page, text: string | RegExp) => page.getByRole("status").filter({ hasText: text });
const windowOf = (page: Page, testId: string) => page.getByRole("dialog").filter({ has: page.getByTestId(testId) });

/** The view key `section.series` as the core keeps it (written 400 ms after the change). */
const storedSeries = async (page: Page) => {
  const v = (await coreApi<{ values: Record<string, { value: { series?: { id: number; k: number } | null } }> }>(page, "GET", "/view")).body.values;
  return v.section?.value?.series ?? null;
};

/** The header of a BFA1 answer of the core (`GET …/contours`): {lines, points, k, …}. */
const contoursHeader = (page: Page, seriesId: number, ks: number[]) =>
  page.evaluate(
    async ([id, k]) => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/section-series/${id}/contours?k=${k}`, { headers: { "X-BF-Token": c.token } });
      if (!r.ok) return { status: r.status, header: null };
      const buf = new Uint8Array(await r.arrayBuffer());
      const len = new DataView(buf.buffer).getUint32(4, true);
      return { status: r.status, header: JSON.parse(new TextDecoder().decode(buf.subarray(8, 8 + len))) as { lines: number; points: number } };
    },
    [seriesId, ks.join(",")] as const,
  );

async function emptyProject(page: Page, name: string): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill(name);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText(name, { timeout: 60_000 });
  await waitFrame(page);
}

/** A button of the ribbon tab «Разрезы». */
async function ribbon(page: Page, label: string): Promise<void> {
  await page.getByRole("tab", { name: "Разрезы" }).click();
  await page.getByLabel("Лента").getByRole("button", { name: label, exact: true }).first().click();
}

/** The keys of the 3D window go to the page itself: the focus leaves fields and lists (a click on the status line). */
async function sceneKey(page: Page, key: string): Promise<void> {
  await page.getByTestId("core-status").click();
  await page.keyboard.press(key);
}

/**
 * The section shown: «разрез n из 5 · name» in the status line; with wireframes in the project also the mark of the
 * data of section k (`sectionData`; none while there is no wireframe to cut).
 */
async function shown(page: Page, n: number, name: string, seriesId: number, data = false): Promise<void> {
  await expect(status(page)).toHaveText(`Серия «Рудное тело» · разрез ${n} из 5 · ${name}`, { timeout: 15_000 });
  if (!data) return;
  await expect.poll(async () => (await state(page)).sectionData, { timeout: 30_000 }).toMatchObject({ series: seriesId, k: n - 1, ready: true });
}

/** A closed square 20 × 20 m in the section Y = y (X 990…1010, Z −10…10), typed in: absolute start, then @@. */
async function squareOn(page: Page, y: number): Promise<void> {
  await page.getByTestId("edit-tool-polygon").click();
  const coord = page.getByTestId("edit-coord");
  for (const t of [`990 ${y} -10`, "@@20 0 0", "@@0 0 20", "@@-20 0 0"]) {
    await coord.fill(t);
    await coord.press("Enter");
    await expect(page.getByTestId("edit-coord-error")).toHaveCount(0);
  }
  await page.getByTestId("edit-modify-finish_line").click();
}

/** Ctrl+S and `]` as the user presses them: the editor panel was the last place pressed (not the field). */
async function editorKey(page: Page, key: string): Promise<void> {
  await page.getByTestId("edit-layer").click();
  await page.keyboard.press(key);
}

test("series: «Новая серия» K = 5, ] [ End Home Ctrl+G, L keeps the point, the shadow, contours on three sections → «Каркас по серии» — a closed body", async () => {
  test.setTimeout(420_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message}\n${e.stack}`));
    await emptyProject(page, "Серии разрезов");
    // the edges of a box 20 × 20 m along the series: the 3D window has something to fit and to clip
    const guide = await newLineLayer(page, "Опоры");
    const edges = [[990, -10], [1010, -10], [1010, 10], [990, 10]].map(([x, z]) => ({ points: [[x, 4980, z], [x, 5120, z]] }));
    await saveLines(page, guide.id, guide.version, edges, "опоры серии");
    await expect.poll(async () => (await state(page)).layers.includes(`ln:${guide.id}`), { timeout: 60_000 }).toBe(true);
    await waitFrame(page);

    // ---- «Новая серия»: Т8.1-1 — A = 90°, P1 = (1000; 5000), s0 = 0, s1 = 100, Δ = 25 --------------------------------
    await ribbon(page, "Новая серия");
    const form = page.getByTestId("series-window");
    await expect(form).toBeVisible();
    await page.getByTestId("series-type-vertical").check();
    await page.getByTestId("series-dir-az").check();
    const fields: [string, string][] = [["name", "Рудное тело"], ["p1x", "1000"], ["p1y", "5000"], ["az", "90"], ["s0", "0"], ["s1", "100"], ["step", "25"], ["towards", "12,5"], ["away", "12.5"]];
    for (const [id, v] of fields) await page.getByTestId(`series-field-${id}`).fill(v);
    await page.getByTestId("series-submit").click();
    await expect(form).toHaveCount(0, { timeout: 30_000 });
    const made = (await seriesList(page)).find((r) => r.name === "Рудное тело")!;
    expect(made, "the series is not in the core").toBeTruthy();
    expect(made.count).toBe(5);
    const id = made.id;
    await shown(page, 1, "5000N", id); // created — active, on its first section, clipping on
    expect((await state(page)).clipPlanes).toBe(2);

    // ---- «Менеджер серий»: the row with K, the table of names, the lines of the series on its plan ------------------
    await ribbon(page, "Менеджер серий");
    const manager = page.getByTestId("series-manager");
    await expect(manager).toBeVisible();
    await expect(page.getByTestId(`series-row-${id}`)).toContainText("Рудное тело");
    await expect(page.getByTestId(`series-row-${id}`)).toContainText("● 5");
    await expect(page.getByTestId("series-summary")).toBeVisible();
    await expect(page.getByTestId("series-plan")).toBeVisible();
    await expect(page.getByTestId("series-table")).toBeVisible();
    const names = ["5000N", "5025N", "5050N", "5075N", "5100N"];
    for (const [k, n] of names.entries()) await expect(page.getByTestId(`series-section-${k}`)).toContainText(n, { timeout: 15_000 });
    await expect(page.getByTestId("series-section-5")).toHaveCount(0);
    await page.screenshot({ path: shot("series-manager.png") });
    await windowOf(page, "series-manager").getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(manager).toHaveCount(0);

    // the mini-plan: five traces, the current one marked
    expect((await run(page, "section.plan", { open: true })).ok).toBe(true);
    await expect(page.getByTestId("series-traces")).toHaveAttribute("data-count", "5");
    await expect(page.getByTestId("series-trace-active")).toHaveCount(1);
    expect((await run(page, "section.plan", { open: false })).ok).toBe(true);

    // ---- ] [ End, ] on the last one (k stays, the message), Home, Ctrl+G with the hints --------------------------------
    await sceneKey(page, "]");
    await shown(page, 2, "5025N", id);
    await sceneKey(page, "[");
    await shown(page, 1, "5000N", id);
    await sceneKey(page, "End");
    await shown(page, 5, "5100N", id);
    await sceneKey(page, "]");
    await expect(note(page, "Последний разрез серии: дальше разрезов нет")).toBeVisible({ timeout: 10_000 });
    await shown(page, 5, "5100N", id);
    await sceneKey(page, "Home");
    await shown(page, 1, "5000N", id);
    await sceneKey(page, "Control+g");
    const goto = page.getByTestId("series-goto");
    await expect(goto).toBeVisible();
    await page.getByTestId("series-goto-input").fill("5050");
    await expect(page.getByTestId("series-goto-hint-2")).toContainText("5050N");
    await page.getByTestId("series-goto-input").press("Enter");
    await expect(goto).toHaveCount(0, { timeout: 10_000 });
    await shown(page, 3, "5050N", id);
    // the current section is a view key of the project, not a version of the series (ADR 0016 решение 13)
    await expect.poll(() => storedSeries(page), { timeout: 15_000 }).toMatchObject({ id, k: 2 });
    expect((await seriesList(page)).find((r) => r.id === id)!.version).toBe(made.version);

    // ---- L: the view along v̂ (+Y), Z up; ] keeps a point with the same (u, w) in its place (Т8.1-6) ---------------------
    await sceneKey(page, "l");
    await expect.poll(async () => (await state(page)).locked, { timeout: 10_000 }).toBe(true);
    await waitFrame(page);
    const locked = await state(page);
    expect(Math.abs(Math.abs(locked.camera.dir[1]) - 1)).toBeLessThan(1e-9);
    expect(locked.camera.up).toEqual([0, 0, 1]);
    const onScreen = (y: number) => page.evaluate((yy) => [window.__bfScene!.screen(1000, yy, 0), window.__bfScene!.screen(1007, yy, 6)], y);
    const before = await onScreen(5050);
    expect(before[0] && before[1], "the point is not on the screen").toBeTruthy();
    await sceneKey(page, "]");
    await shown(page, 4, "5075N", id);
    await waitFrame(page);
    const after = await onScreen(5075);
    for (const i of [0, 1]) {
      expect(Math.abs(after[i]!.x - before[i]!.x), "x on the screen").toBeLessThanOrEqual(0.5);
      expect(Math.abs(after[i]!.y - before[i]!.y), "y on the screen").toBeLessThanOrEqual(0.5);
    }
    for (let i = 0; i < 3; i++) expect((await state(page)).camera.dir[i]).toBeCloseTo(locked.camera.dir[i], 12);
    await page.screenshot({ path: shot("series-locked.png") });

    // ---- the shadow «предыдущий» (the ribbon button is a switch off / previous; a new series has «предыдущий»), kept in
    // the series ---------------------------------------------------------------------------------------------------------
    const shadowOf = async () => (await seriesList(page)).find((r) => r.id === id)!.params.shadow;
    await expect.poll(shadowOf, { timeout: 15_000 }).toBe("prev");
    await ribbon(page, "Тень");
    await expect.poll(shadowOf, { timeout: 15_000 }).toBe("none");
    await ribbon(page, "Тень");
    await expect.poll(shadowOf, { timeout: 15_000 }).toBe("prev");

    // ---- «Контур на разрезе» on 5025N, 5050N, 5075N by typed coordinates; ] between them, the shadow follows ------------
    await sceneKey(page, "Home");
    await shown(page, 1, "5000N", id);
    await sceneKey(page, "]");
    await shown(page, 2, "5025N", id);
    await ribbon(page, "Контур на разрезе");
    await expect(page.getByTestId("edit-panel")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("edit-layer")).toHaveText("Контуры Рудное тело");
    const layerId = (await edState(page))!.layerId;
    expect(layerId).not.toBe(guide.id);
    await expect.poll(async () => (await seriesList(page)).find((r) => r.id === id)!.params.contour_layer, { timeout: 15_000 }).toBe(layerId);
    expect((await state(page)).locked).toBe(true); // the lock stays while digitizing
    for (const [i, y] of [5025, 5050, 5075].entries()) {
      if (i > 0) {
        await editorKey(page, "]");
        await shown(page, i + 2, names[i + 1], id);
        // the contour of the section before (saved): grey in the scene layer of the shadow
        await expect.poll(async () => (await state(page)).layers.includes("shadow"), { timeout: 30_000 }).toBe(true);
      }
      await squareOn(page, y);
      await expect(page.getByTestId("edit-measures")).toContainText("площадь 400 м²");
      await editorKey(page, "Control+s");
      await expect(page.getByTestId("edit-version")).toHaveText(`в. ${i + 2}`, { timeout: 30_000 });
    }
    expect(await edState(page)).toMatchObject({ layerId, lines: 3, dirty: false, version: 4 });
    await waitFrame(page);
    await page.screenshot({ path: shot("series-digitized.png") });
    // on the sections 2, 3, 4 (k = 1, 2, 3) one contour each; the shadow of 5075N is the contour of 5050N
    for (const k of [1, 2, 3]) expect((await contoursHeader(page, id, [k])).header).toMatchObject({ lines: 1, points: 4 });
    expect((await contoursHeader(page, id, [0, 4])).header).toMatchObject({ lines: 0 });
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);

    // ---- «Каркас по серии»: three contours, two automatic links, one body → a closed wireframe -----------------------------
    await ribbon(page, "Каркас по серии");
    const stitch = page.getByTestId("series-stitch");
    await expect(stitch).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("stitch-table").locator('[data-testid^="stitch-contour-"]')).toHaveCount(3);
    await expect(page.getByTestId("stitch-bodies")).toHaveText("1");
    await expect(page.getByTestId("stitch-conflict")).toHaveCount(0);
    await expect(stitch).toContainText("связей 2 (автоматические)");
    await page.screenshot({ path: shot("series-stitch.png") });
    const t0 = Date.now();
    await page.getByTestId("stitch-run").click();
    const follow = page.getByTestId("stitch-follow");
    await expect(follow).toHaveAttribute("data-status", "done", { timeout: 120_000 });
    const stitchS = (Date.now() - t0) / 1000;
    console.log(`series: «Создать каркас» → задание готово за ${stitchS.toFixed(1)} с`);
    const wfs = (await coreApi<{ wireframes: Wf[] }>(page, "GET", "/wireframes")).body.wireframes;
    expect(wfs).toHaveLength(1);
    expect(wfs[0].stats.closed).toBe(true);
    // the caps «продлить» reach Δ/2 = 12.5 m beyond the end sections: 20 × 20 × (50 + 25) m
    expect(wfs[0].stats.volume!).toBeCloseTo(30_000, 6);
    await windowOf(page, "stitch-follow").getByRole("button", { name: "Закрыть", exact: true }).first().click();
    await expect(follow).toHaveCount(0);
    // the body is cut by the section shown: its line comes from the core
    await expect.poll(async () => (await state(page)).exact.sections.find((s) => s.wireframe === wfs[0].id)?.lines ?? 0, { timeout: 60_000 }).toBe(1);

    // ---- the tree «Серии разрезов»: a click on a section goes to it ---------------------------------------------------------
    const node = page.getByTestId(`series-tree-${id}`);
    await node.scrollIntoViewIfNeeded();
    await node.click();
    await page.getByTestId(`series-tree-${id}-3`).click();
    await shown(page, 4, "5075N", id, true);
    expect((await state(page)).exact.sections.find((s) => s.wireframe === wfs[0].id)?.lines).toBe(1);
    await page.screenshot({ path: shot("series-tree.png") });
  } finally {
    await close(app);
  }
  console.log(`series: empty project — ${((Date.now() - started) / 1000).toFixed(1)} s`);
});

// ---------------------------------------------------------------- the demo: through a hole, the step ] timed

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const p95 = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(xs.length * 0.95))];
const r1 = (v: number) => Math.round(v * 10) / 10;

/**
 * `]` as the user presses it, timed in the page from its keydown to the mark of the data of section k:
 * `sectionData {series, k, ready}` (ms; the mark itself — its `ms` from the change of the view key, `prepared`).
 */
async function timedStep(page: Page, seriesId: number, k: number): Promise<{ ms: number; mark: Mark; lines: number }> {
  await page.evaluate(([sid, kk]) => {
    const w = window as unknown as { __bfStep?: Promise<unknown> };
    w.__bfStep = new Promise((res) => {
      let t0 = 0;
      const until = performance.now() + 30_000;
      const poll = () => {
        const st = window.__bfScene!.state() as unknown as Extra;
        const m = st.sectionData;
        if (t0 && m && m.series === sid && m.k === kk && m.ready) return res({ ms: performance.now() - t0, mark: m, lines: st.exact.sections.reduce((a, s) => a + s.lines, 0) });
        if (performance.now() > until) return res(null);
        setTimeout(poll, 1);
      };
      window.addEventListener("keydown", () => ((t0 = performance.now()), poll()), { capture: true, once: true });
    });
  }, [seriesId, k] as const);
  await page.keyboard.press("]");
  const r = await page.evaluate(() => (window as unknown as { __bfStep: Promise<unknown> }).__bfStep);
  expect(r, `the section ${k + 1} is not ready`).not.toBeNull();
  return r as { ms: number; mark: Mark; lines: number };
}

test("series: the demo — «Серия через скважину» shows the whole hole; the step ] to the data of the section → bench/out/series_step.json", async () => {
  test.setTimeout(420_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page, 180_000);

    // ---- «Серия через скважину»: the most inclined hole of the demo, its stations in the window and drawn (Т8.1-7) ----------
    const sets = (await coreApi<{ sets: { id: number; name: string }[] }>(page, "GET", "/drillholes/sets")).body.sets;
    const set = sets.find((s) => s.name === "Скважины 2025") ?? sets[0];
    const traj = (await coreApi<{ holes: { name: string; stations: number[][] }[] }>(page, "GET", `/drillholes/sets/${set.id}/trajectories`)).body.holes;
    const reach = (h: { stations: number[][] }) => {
      const [a, b] = [h.stations[0], h.stations[h.stations.length - 1]];
      return Math.hypot(b[1] - a[1], b[2] - a[2]);
    };
    // a hole, not the trench «К-01» (horizontal at the surface): deeper than 20 m, the farthest in plan
    const drop = (h: { stations: number[][] }) => Math.abs(h.stations[0][3] - h.stations[h.stations.length - 1][3]);
    const hole = traj.filter((h) => drop(h) > 20).sort((a, b) => reach(b) - reach(a))[0];
    expect(reach(hole), "no inclined hole in the demo").toBeGreaterThan(5);
    expect((await run(page, "series.through_hole", { hole_id: hole.name, set_id: set.id })).ok).toBe(true);
    const form = page.getByTestId("series-window");
    await expect(form).toBeVisible();
    await expect(page.getByTestId("series-field-name")).toHaveValue(`Через скважину ${hole.name}`);
    await page.getByTestId("series-submit").click();
    await expect(form).toHaveCount(0, { timeout: 30_000 });
    const through = (await seriesList(page)).find((r) => r.name === `Через скважину ${hole.name}`)!;
    expect(through).toBeTruthy();
    await expect(status(page)).toContainText(`Серия «${through.name}»`, { timeout: 15_000 });
    await expect.poll(async () => (await state(page)).sectionData?.ready ?? false, { timeout: 60_000 }).toBe(true);
    const k = (await state(page)).sectionData!.k!;
    const pr = through.params as { p1: number[]; p2?: number[] | null; az?: number | null; s0?: number; step?: number; towards?: number; away?: number };
    const sdir = pr.p2 ? [pr.p2[0] - pr.p1[0], pr.p2[1] - pr.p1[1]] : [Math.sin((pr.az! * Math.PI) / 180), Math.cos((pr.az! * Math.PI) / 180)];
    const len = Math.hypot(sdir[0], sdir[1]);
    const v = [-sdir[1] / len, sdir[0] / len]; // v̂ = (−ŝy, ŝx, 0)
    const sk = (pr.s0 ?? 0) + k * (pr.step ?? 0);
    const depths = hole.stations.map(([, x, y]) => (x - pr.p1[0]) * v[0] + (y - pr.p1[1]) * v[1] - sk);
    const worst = Math.max(...depths.map(Math.abs));
    expect(worst, "a station of the hole is out of the window").toBeLessThan(Math.min(pr.towards ?? 25, pr.away ?? 25));
    expect((await run(page, "scene.view", { view: "section" })).ok).toBe(true);
    await waitFrame(page);
    const seen = await page.evaluate((stations) => {
      const host = document.querySelector('[data-testid="scene-host"] canvas')!.getBoundingClientRect();
      const bg = window.__bfScene!.pixel(host.right - 6, host.top + 6) ?? [0, 0, 0, 0];
      let inView = 0;
      let drawn = 0;
      for (const [, x, y, z] of stations) {
        const p = window.__bfScene!.screen(x, y, z);
        if (!p || p.x < host.left || p.x > host.right || p.y < host.top || p.y > host.bottom) continue;
        inView++;
        // a pixel of the trace within 2 px of the station that is not the background
        let hit = false;
        for (let dx = -2; dx <= 2 && !hit; dx++)
          for (let dy = -2; dy <= 2 && !hit; dy++) {
            const c = window.__bfScene!.pixel(p.x + dx, p.y + dy);
            hit = !!c && Math.abs(c[0] - bg[0]) + Math.abs(c[1] - bg[1]) + Math.abs(c[2] - bg[2]) > 30;
          }
        if (hit) drawn++;
      }
      return { inView, drawn, stations: stations.length };
    }, hole.stations);
    expect(seen.inView, JSON.stringify(seen)).toBe(seen.stations);
    expect(seen.drawn, JSON.stringify(seen)).toBeGreaterThanOrEqual(Math.ceil(seen.stations * 0.8));
    await page.screenshot({ path: shot("series-through-hole.png") });

    // ---- the shadow of «Разрезы рудного тела 3»: on 8050N the contours of 8000N … of the section before ---------------------
    const ore = (await seriesList(page)).find((r) => r.name === "Разрезы рудного тела 3")!;
    expect(ore, "no ore series in the demo").toBeTruthy();
    expect(ore.params.shadow).toBe("prev");
    expect((await run(page, "series.activate", { series_id: ore.id, k: 2 })).ok).toBe(true);
    await expect(status(page)).toContainText(`Серия «${ore.name}» · разрез 2 из ${ore.count}`, { timeout: 15_000 });
    await expect.poll(async () => (await state(page)).layers.includes("shadow"), { timeout: 30_000 }).toBe(true);
    const shadow = (await contoursHeader(page, ore.id, [0])).header!;
    expect(shadow.lines).toBeGreaterThan(0);

    // ---- the step ]: a series of 26 sections every 10 m over the ore body (windows 5 / 5 m) -------------------------------
    const p1 = ore.params.p1 as number[];
    const made = await coreApi<Row>(page, "POST", "/section-series", {
      name: "Замер шага",
      params: { type: "vertical", p1: [p1[0], p1[1], 0], az: 90, s0: 0, s1: 250, step: 10, towards: 5, away: 5, shadow: "prev", contour_layer: ore.params.contour_layer },
    });
    expect(made.status, JSON.stringify(made.body)).toBe(200);
    const bench = made.body;
    expect(bench.count).toBe(26);
    await expect.poll(async () => (await run(page, "series.activate", { series_id: bench.id, k: 1 })).ok, { timeout: 15_000 }).toBe(true);
    await expect.poll(async () => (await state(page)).sectionData, { timeout: 60_000 }).toMatchObject({ series: bench.id, k: 0, ready: true });
    await waitFrame(page, 120_000);

    // cold: ] at once after the section is ready — k + 1 is not read yet (idle 150 ms)
    const cold: { ms: number; mark: Mark; lines: number }[] = [];
    for (let kk = 1; kk <= 15; kk++) cold.push(await timedStep(page, bench.id, kk));
    // prefetched: the idle time reads k + 1 first — ] then shows it without a request
    const warm: { ms: number; mark: Mark; lines: number }[] = [];
    for (let kk = 16; kk <= 25; kk++) {
      await page.waitForTimeout(300); // the neighbour k + 1 is asked 150 ms after the section is ready…
      await waitFrame(page); // … and read: the scene is idle with no request in flight
      warm.push(await timedStep(page, bench.id, kk));
    }
    await sceneKey(page, "]");
    await expect(note(page, "Последний разрез серии: дальше разрезов нет")).toBeVisible({ timeout: 10_000 });
    await waitFrame(page);
    await page.screenshot({ path: shot("series-step.png") });
    const coldMs = cold.filter((s) => !s.mark.prepared).map((s) => s.ms);
    const warmMs = warm.filter((s) => s.mark.prepared).map((s) => s.ms);
    expect(coldMs.length, "every cold step was prefetched").toBeGreaterThanOrEqual(8);
    expect(warmMs.length, "the prefetched steps were not prefetched").toBeGreaterThanOrEqual(8);
    const lines = cold.concat(warm).map((s) => s.lines);
    const st = await state(page);
    const out = {
      sections: bench.count,
      step_median_ms: r1(median(coldMs)),
      step_p95_ms: r1(p95(coldMs)),
      step_max_ms: r1(Math.max(...coldMs)),
      step_mark_median_ms: r1(median(cold.filter((s) => !s.mark.prepared).map((s) => s.mark.ms ?? 0))),
      step_prefetched_median_ms: r1(median(warmMs)),
      step_prefetched_p95_ms: r1(p95(warmMs)),
      steps_cold: coldMs.length,
      steps_prefetched: warmMs.length,
      section_lines: median(lines),
      section_lines_max: Math.max(...lines),
      wireframes: new Set(st.exact.sections.map((s) => s.wireframe)).size,
      shadow_lines: shadow.lines,
      renderer: st.gpu.renderer,
      platform: process.platform,
    };
    fs.mkdirSync(path.join(ROOT, "bench", "out"), { recursive: true });
    fs.writeFileSync(path.join(ROOT, "bench", "out", "series_step.json"), JSON.stringify(out, null, 2));
    console.log(`series_step: ${JSON.stringify(out)}`);
    expect(out.section_lines).toBeGreaterThan(0);
    // budgets.json → external.series_step holds the medians (300 / 100 ms; p95 of ~15 steps is their maximum, noisy
    // under SwiftShader); the test holds ×4 of them — the same file runs on the packaged .exe of the Windows runner
    expect(out.step_median_ms).toBeLessThanOrEqual(1200);
    expect(out.step_prefetched_median_ms).toBeLessThanOrEqual(400);
  } finally {
    await close(app);
  }
  console.log(`series: demo — ${((Date.now() - started) / 1000).toFixed(1)} s`);
});
