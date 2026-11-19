// Э9, подшаг 9.36 (ТЗ 7.23 «Собрать из контуров»; ADR 0015 §6): two section contours 10 × 10 ten metres apart are
// typed into the string editor and left unsaved; «Собрать из контуров» → the contours are clicked in the 3D window
// one after the other, the panel counts them and the preview grows («торцы открыты»); «Убрать последний контур»
// and the click again; «Замкнуть торцы» → «объём 1 000 м³»; «Создать каркас» saves the layer and runs «Сшить
// контуры» → a closed body of 1000 m³ with its record of the operation. An empty project, a window of 1024 × 640
// (as the Windows runner): the clicks go to the bare canvas below the panels, the view from above, zoomed out.
import { expect, test, type Page } from "@playwright/test";
import { close, coreApi, launch, setWindowSize, shot, waitFrame } from "./helpers";

type Wf = { id: number; name: string; stats: { closed: boolean; volume: number | null }; built?: { op?: string; sources?: { id: number | null; version?: number; lines?: number[] | null }[] } | null };
type EdState = { layerId: number; version: number; dirty: boolean; lines: number };
const edState = (page: Page) => page.evaluate(() => (window as unknown as { __bfEditor: { state(): EdState | null } }).__bfEditor.state());
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

async function emptyProject(page: Page, name: string): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill(name);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText(name, { timeout: 60_000 });
  await waitFrame(page);
}

/** A closed contour in the section Y = y: the square 10 × 10 in X and Z, typed in (absolute start, then @@). */
async function sectionContour(page: Page, y: number): Promise<void> {
  await page.getByTestId("edit-tool-polygon").click();
  const coord = page.getByTestId("edit-coord");
  for (const t of [`3000 ${y} 0`, "@@10 0 0", "@@0 0 10", "@@-10 0 0"]) {
    await coord.fill(t);
    await coord.press("Enter");
    await expect(page.getByTestId("edit-coord-error")).toHaveCount(0);
  }
  await page.getByTestId("edit-modify-finish_line").click();
}

/** The middle of the lower edge of the contour Y = y on the screen, when it and its surroundings are bare canvas. */
const edgeAt = (page: Page, y: number) =>
  page.evaluate((yy) => {
    const p = window.__bfScene!.screen(3005, yy, 0);
    const host = document.querySelector('[data-testid="scene-host"]')!.getBoundingClientRect();
    if (!p || p.x < host.left + 40 || p.x > host.right - 40 || p.y < host.top + 40 || p.y > host.bottom - 40) return null;
    const bare = [[0, 0], [-10, -6], [10, 6], [0, 12], [0, -12]].every(([dx, dy]) => {
      const hit = document.elementFromPoint(p.x + dx, p.y + dy);
      return !!hit && hit.tagName === "CANVAS" && !!hit.closest('[data-testid="scene-host"]');
    });
    return bare ? p : null;
  }, y);

test("«Собрать из контуров»: unsaved contours clicked in order, the preview, «Замкнуть торцы», «Создать каркас» → 1000 m³", async () => {
  test.setTimeout(300_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner
    await emptyProject(page, "Сборка из контуров");
    // the 3D window is under two panels here: the ribbon is folded and the right panel closed to make room
    expect((await run(page, "ribbon.toggle")).ok).toBe(true);
    await page.getByRole("button", { name: "Закрыть правую панель" }).click();
    await expect(page.getByLabel("Правая панель")).toHaveCount(0);
    const made = await coreApi<{ id: number }>(page, "POST", "/lines/layers", { name: "Разрезы" });
    expect(made.status, JSON.stringify(made.body)).toBe(201);
    const layerId = made.body.id;
    await expect(page.getByTestId(`tree-layer-${layerId}`)).toBeVisible({ timeout: 30_000 });
    const opened = await run(page, "lines.edit", { layer_id: layerId });
    expect(opened.ok, JSON.stringify(opened)).toBe(true);
    await expect(page.getByTestId("edit-panel")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("edit-version")).toHaveText("в. 1");

    // ---- two section contours, not saved ------------------------------------------------------------------------
    await sectionContour(page, 3000);
    await sectionContour(page, 3010);
    expect(await edState(page)).toMatchObject({ layerId, lines: 2, dirty: true, version: 1 });

    // ---- the assembly in the layer on edit ----------------------------------------------------------------------
    expect((await run(page, "model.assemble")).ok).toBe(true);
    const panel = page.getByTestId("assemble-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("assemble-info")).toHaveText("Щёлкните первый контур");
    await expect(page.getByTestId("assemble-create")).toHaveAttribute("aria-disabled", "true");

    // the view from above: a section contour is a segment; zoom out until both are on the bare canvas
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit")).ok).toBe(true);
    await waitFrame(page);
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    const edit = (await page.getByTestId("edit-panel").boundingBox())!;
    const bar = (await panel.boundingBox())!;
    // the panel of the editor is above, the panel of the assembly below: the canvas between them
    const free = { x: host.x + host.width / 2, y: (edit.y + edit.height + bar.y) / 2 };
    expect(bar.y - (edit.y + edit.height), "no canvas between the panels").toBeGreaterThan(120);
    const both = async () => {
      const a = await edgeAt(page, 3000);
      const b = await edgeAt(page, 3010);
      return a && b && Math.abs(a.y - b.y) >= 30 ? { a, b } : null;
    };
    for (let k = 0; k < 20 && !(await both()); k++) {
      await page.mouse.move(free.x, free.y);
      await page.mouse.wheel(0, 240);
      await waitFrame(page);
    }
    const at = (await both())!;
    expect(at, "the contours are not on the bare canvas").not.toBeNull();
    await page.screenshot({ path: shot("assemble-start.png") });

    // ---- clicks in order: the panel counts, the preview grows; «Убрать последний контур» and the click again --------
    const count = page.getByTestId("assemble-count");
    const info = page.getByTestId("assemble-info");
    await page.mouse.click(at.a.x, at.a.y);
    await expect(count).toContainText("контуров 1");
    await expect(info).toHaveText("Щёлкните следующий контур");
    await page.mouse.click(at.b.x, at.b.y);
    await expect(count).toContainText("контуров 2");
    await expect(info).toContainText("торцы открыты", { timeout: 30_000 });
    await expect(info).toContainText(/треугольников \d+/);
    await expect(page.getByTestId("assemble-create")).toHaveAttribute("aria-disabled", "false");
    await page.getByTestId("assemble-drop").click();
    await expect(count).toContainText("контуров 1");
    await expect(info).toHaveText("Щёлкните следующий контур");
    await page.mouse.click(at.b.x, at.b.y);
    await expect(count).toContainText("контуров 2");
    await expect(info).toContainText("торцы открыты", { timeout: 30_000 });

    // ---- «Замкнуть торцы»: flat caps — a closed body with its volume ---------------------------------------------
    await page.getByTestId("assemble-close").click();
    await expect(page.getByTestId("assemble-close")).toHaveAttribute("aria-pressed", "true");
    await expect(info).toContainText(/объём 1\s000 м³/, { timeout: 30_000 });
    await waitFrame(page);
    await page.screenshot({ path: shot("assemble-preview.png") });
    expect((await edState(page))!.dirty).toBe(true); // the preview needs no save

    // ---- «Создать каркас»: the layer is saved (version 2), the operation «Сшить контуры» makes the body -------------
    const t0 = Date.now();
    await page.getByTestId("assemble-create").click();
    await expect(panel).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByTestId("edit-version")).toHaveText("в. 2");
    expect((await edState(page))!.dirty).toBe(false);
    const report = page.getByTestId("op-report");
    await expect(report).toBeVisible({ timeout: 120_000 });
    const seconds = (Date.now() - t0) / 1000;
    await expect(report).toHaveAttribute("data-op", "stitch");
    await expect(page.getByTestId("op-report-made")).toContainText(/объём 1\s000 м³/);
    const wfs = (await coreApi<{ wireframes: Wf[] }>(page, "GET", "/wireframes")).body.wireframes;
    expect(wfs).toHaveLength(1);
    const body = wfs[0];
    expect(body.stats.closed).toBe(true);
    expect(body.stats.volume!).toBeCloseTo(1000, 6);
    expect(body.built?.op).toBe("stitch");
    expect(body.built?.sources?.[0]).toMatchObject({ id: layerId, version: 2 });
    await expect(page.getByTestId(`tree-wireframe-${body.id}`)).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: shot("assemble-made.png") });
    await report.getByRole("button", { name: "Закрыть", exact: true }).click();
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);
    console.log(`assemble: «Создать каркас» → отчёт ${seconds.toFixed(1)} с; всего ${((Date.now() - started) / 1000).toFixed(1)} с`);
  } finally {
    await close(app);
  }
});
