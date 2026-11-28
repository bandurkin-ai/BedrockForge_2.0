// Э27, 27.8 (Д-47 А8): «Провести разрез» during the creation of a polyline. A polyline of three points is being
// drawn with the line tool → the button «Разрез» of the 3D panel → the polyline is finished and kept (as with Enter),
// the editor goes to «Выбор», the next two clicks of the canvas are the section line (no vertex is added to the
// layer) and the section is set. A second pass: the button with an empty polyline (the tool chosen, no point yet).
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, waitFrame } from "./helpers";

type EdState = { layerId: number; version: number; dirty: boolean; tool: string; lines: number; message: string | null };
type Ed = { state(): EdState | null; points(i: number): number[][] | null };
const edState = (page: Page) => page.evaluate(() => (window as unknown as { __bfEditor: Ed }).__bfEditor.state());
const edPoints = (page: Page, i: number) => page.evaluate((k) => (window as unknown as { __bfEditor: Ed }).__bfEditor.points(k), i);
const section = (page: Page) => page.evaluate(() => window.__bfScene!.state().section);

async function typePoints(page: Page, texts: string[]): Promise<void> {
  const coord = page.getByTestId("edit-coord");
  for (const t of texts) {
    await coord.fill(t);
    await coord.press("Enter");
    await expect(page.getByTestId("edit-coord-error")).toHaveCount(0);
  }
}

test("string editor: «Разрез» during a polyline finishes it and draws the section instead of one more polyline", async () => {
  test.setTimeout(300_000);
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1280, 800);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Разрез при правке");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("project-selector")).toContainText("Разрез при правке", { timeout: 60_000 });
    await waitFrame(page);

    await page.getByRole("tab", { name: "Моделирование" }).click();
    await page.getByLabel("Лента").getByRole("button", { name: "Новый слой", exact: true }).click();
    const ask = page.getByRole("dialog", { name: "Новый слой линий" });
    await ask.getByRole("textbox").fill("Контур");
    await ask.getByRole("button", { name: "Создать" }).click();
    await expect(page.getByTestId("edit-panel")).toBeVisible({ timeout: 30_000 });

    // ---- a polyline of three points is being drawn --------------------------------------------------------------
    await page.getByTestId("edit-tool-line").click();
    await typePoints(page, ["1000 1000 0", "@@50 0", "@@0 50"]);
    expect(await edState(page)).toMatchObject({ tool: "line", lines: 1 });
    expect(await edPoints(page, 0)).toHaveLength(3);

    // ---- «Разрез» of the panel over the 3D window ---------------------------------------------------------------
    const bar = page.getByRole("toolbar", { name: "Панель 3D-окна" });
    await bar.getByRole("button", { name: "Разрез", exact: true }).click();
    await expect(page.getByText("Укажите начало профиля")).toBeVisible();
    expect(await edState(page)).toMatchObject({ tool: "select", lines: 1 }); // finished and kept, the tool is «Выбор»
    expect(await edPoints(page, 0)).toEqual([[1000, 1000, 0], [1050, 1000, 0], [1050, 1050, 0]]);

    // ---- two clicks on the bare canvas: the section line, not vertices ------------------------------------------
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    const panel = (await page.getByTestId("edit-panel").boundingBox())!;
    const top = Math.max(host.y, panel.y + panel.height) + 20;
    const y = (top + host.y + host.height) / 2;
    await page.mouse.click(host.x + host.width * 0.3, y);
    await expect(page.getByText("Укажите конец профиля")).toBeVisible();
    await page.mouse.click(host.x + host.width * 0.7, y - 10);
    await waitFrame(page);
    await expect.poll(() => section(page), { timeout: 15_000 }).not.toBeNull();
    expect(await edState(page)).toMatchObject({ tool: "select", lines: 1 });
    expect(await edPoints(page, 0)).toHaveLength(3);

    // ---- an empty polyline: the tool is chosen, no point yet — the section is drawn, nothing is left behind ------
    await page.getByTestId("edit-tool-polygon").click();
    expect(await edState(page)).toMatchObject({ tool: "polygon", lines: 1 });
    await bar.getByRole("button", { name: "Разрез", exact: true }).click();
    await expect(page.getByText("Укажите начало профиля")).toBeVisible();
    expect(await edState(page)).toMatchObject({ tool: "select", lines: 1 });
    // the tool of the editor called while the section is being drawn calls the section off
    await page.getByTestId("edit-tool-line").click();
    await expect(page.getByText("Укажите начало профиля")).toHaveCount(0);
    expect(await edState(page)).toMatchObject({ tool: "line" });
    await page.getByTestId("edit-tool-select").click();
  } finally {
    await close(app);
  }
});
