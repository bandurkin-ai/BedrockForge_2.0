// Э9, подшаг 9.35 (ТЗ 7.22; ADR 0015): the string editor end to end. A new layer from the ribbon → a polygon by
// typed coordinates → «площадь 100 м²» → Ctrl+S (version 2) → «Версии» → back to 1 (version 3, empty) → back to 2
// (version 4) → a click with the line tool next to a corner snaps to the vertex exactly. A save of somebody else
// (PUT straight into the core with the same base version) → Ctrl+S is refused with 409 and the own window with
// three actions: «Отмена» keeps the edit, «Перечитать слой» takes the other version, «Сохранить как новый слой»
// writes the edit next to it. An empty project: no other objects in the 3D window, a click hits only the layer.
import { expect, test, type Page } from "@playwright/test";
import { close, launch, setWindowSize, shot, waitFrame } from "./helpers";

type EdState = { layerId: number; version: number; dirty: boolean; tool: string; lines: number; message: string | null };
type Ed = { state(): EdState | null; points(i: number): number[][] | null };
const edState = (page: Page) => page.evaluate(() => (window as unknown as { __bfEditor: Ed }).__bfEditor.state());
const edPoints = (page: Page, i: number) => page.evaluate((k) => (window as unknown as { __bfEditor: Ed }).__bfEditor.points(k), i);
const run = (page: Page, id: string, args: Record<string, unknown> = {}) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

/** A save of «another user»: the layer becomes one new open line, straight through the core (BFA1 body). */
function foreignSave(page: Page, layerId: number, baseVersion: number, xyz: number[]) {
  return page.evaluate(
    async ([id, base, coords]) => {
      const arrays: [string, string, ArrayBufferView][] = [
        ["src", "<i4", new Int32Array([-1])],
        ["touched", "<u1", new Uint8Array([1])],
        ["count", "<u4", new Uint32Array([coords.length / 3])],
        ["closed", "<u1", new Uint8Array([0])],
        ["xyz", "<f8", new Float64Array(coords)],
      ];
      // the same layout as ui/src/edit/bfa1.ts: "BFA1", u32 header length, JSON, arrays at multiples of 8
      const a8 = (n: number) => Math.ceil(n / 8) * 8;
      let start = 0;
      let body: Uint8Array | null = null;
      while (!body) {
        let at = start;
        const table = arrays.map(([name, dtype, data]) => {
          const row = [name, dtype, (data as unknown as { length: number }).length, at];
          at = a8(at + data.byteLength);
          return row;
        });
        const head = new TextEncoder().encode(JSON.stringify({ base_version: base, note: "чужое сохранение", new_attrs: {}, arrays: table }));
        const need = a8(8 + head.length);
        if (need > start) {
          start = need;
          continue;
        }
        body = new Uint8Array(Math.max(at, need));
        body.set([0x42, 0x46, 0x41, 0x31]);
        new DataView(body.buffer).setUint32(4, head.length, true);
        body.set(head, 8);
        arrays.forEach(([, , data], k) => body!.set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), table[k][3] as number));
      }
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/lines/layers/${id}/geometry`, { method: "PUT", headers: { "X-BF-Token": c.token, "Content-Type": "application/octet-stream" }, body });
      return { status: r.status, body: (await r.json()) as { version?: number } };
    },
    [layerId, baseVersion, xyz] as const,
  );
}

async function emptyProject(page: Page, name: string): Promise<void> {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
  await page.getByLabel("Лента").getByRole("button", { name: "Новый проект" }).click();
  await page.getByRole("dialog").last().getByRole("textbox").fill(name);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("project-selector")).toContainText(name, { timeout: 60_000 });
  await waitFrame(page);
}

/** «Моделирование › Новый слой» with its name asked in the own window; the editor opens on it. */
async function newLayer(page: Page, name: string): Promise<number> {
  await page.getByRole("tab", { name: "Моделирование" }).click();
  await page.getByLabel("Лента").getByRole("button", { name: "Новый слой", exact: true }).click();
  const ask = page.getByRole("dialog", { name: "Новый слой линий" });
  await ask.getByRole("textbox").fill(name);
  await ask.getByRole("button", { name: "Создать" }).click();
  await expect(page.getByTestId("edit-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("edit-layer")).toHaveText(name);
  await expect(page.getByTestId("edit-version")).toHaveText("в. 1");
  const s = (await edState(page))!;
  expect(s.lines).toBe(0);
  return s.layerId;
}

/** Ctrl+S as the user presses it: the editor panel was the last place pressed (not a field of input). */
async function ctrlS(page: Page): Promise<void> {
  await page.getByTestId("edit-layer").click();
  await page.keyboard.press("Control+s");
}

async function typePoints(page: Page, texts: string[]): Promise<void> {
  const coord = page.getByTestId("edit-coord");
  for (const t of texts) {
    await coord.fill(t);
    await coord.press("Enter");
    await expect(page.getByTestId("edit-coord-error")).toHaveCount(0);
  }
}

async function restore(page: Page, version: number, expected: number): Promise<void> {
  await page.getByTestId(`version-restore-${version}`).click();
  await page.getByRole("dialog", { name: "Возврат к версии" }).getByRole("button", { name: "Вернуть", exact: true }).click();
  await expect(page.getByTestId("edit-version")).toHaveText(`в. ${expected}`, { timeout: 30_000 });
  await expect(page.getByTestId(`version-row-${expected}`)).toBeVisible({ timeout: 30_000 });
}

test("string editor: polygon by coordinates, Ctrl+S, versions and the return, a click snapped to a vertex", async () => {
  test.setTimeout(300_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await setWindowSize(app, 1024, 640); // as small as on the Windows runner: the panel covers half of the 3D window
    await emptyProject(page, "Редактор стрингов");
    const layerId = await newLayer(page, "Проба");
    await expect(page.getByTestId(`tree-layer-edited-${layerId}`)).toBeVisible();

    // ---- a polygon by typed coordinates: an absolute start, then @@ — relative to the last point ----------------
    await page.getByTestId("edit-tool-polygon").click();
    await typePoints(page, ["1000 1000 0", "@@10 0", "@@0 10", "@@-10 0"]);
    await page.getByTestId("edit-modify-finish_line").click();
    await expect(page.getByTestId("edit-measures")).toContainText("площадь 100 м²");
    await expect(page.getByTestId("edit-dirty")).toBeVisible();
    expect(await edPoints(page, 0)).toEqual([[1000, 1000, 0], [1010, 1000, 0], [1010, 1010, 0], [1000, 1010, 0]]);

    // ---- Ctrl+S: version 2 ----------------------------------------------------------------------------------------
    await ctrlS(page);
    await expect(page.getByTestId("edit-version")).toHaveText("в. 2", { timeout: 30_000 });
    await expect(page.getByTestId("edit-dirty")).toHaveCount(0);

    // ---- «Версии»: back to 1 is a new version 3 (empty — the layer was new), back to 2 — version 4 ------------------
    await page.getByTestId("edit-versions").click();
    const versions = page.getByTestId("lines-versions");
    await expect(versions).toBeVisible();
    await expect(page.getByTestId("version-row-2")).toBeVisible();
    await expect(page.getByTestId("version-row-1")).toBeVisible();
    await restore(page, 1, 3);
    expect((await edState(page))!.lines).toBe(0);
    await expect(page.getByTestId("edit-measures")).not.toContainText("площадь");
    await restore(page, 2, 4);
    const back = (await edState(page))!;
    expect(back).toMatchObject({ lines: 1, version: 4, dirty: false });
    expect(await edPoints(page, 0)).toEqual([[1000, 1000, 0], [1010, 1000, 0], [1010, 1010, 0], [1000, 1010, 0]]);
    await page.getByRole("dialog", { name: "Версии: Проба" }).getByRole("button", { name: "Закрыть" }).click();
    await expect(versions).toHaveCount(0);

    // ---- a click with the line tool a few pixels off a corner: the point is the vertex itself ----------------------
    expect((await run(page, "scene.view", { view: "top" })).ok).toBe(true);
    expect((await run(page, "scene.fit")).ok).toBe(true);
    await waitFrame(page);
    await page.getByTestId("edit-tool-line").click();
    // «Вписать» fills the 3D window with the square; on a small window (the Windows runner) its upper half is under
    // the editor panel. The wheel zooms about the centre of the window: zoom out until the lower right corner is
    // on the bare canvas, then hover and click a few pixels off it.
    const target = () =>
      page.evaluate(() => {
        const p = window.__bfScene!.screen(1010, 1000, 0);
        const r = document.querySelector('[data-testid="scene-host"]')!.getBoundingClientRect();
        // away from the edges: the hints and the axes over the canvas let the pointer through
        if (!p || p.x < r.left + 40 || p.x > r.right - 40 || p.y < r.top + 40 || p.y > r.bottom - 40) return null;
        const bare = [[6, 4], [0, 0], [-12, -12], [16, 16], [6, 22]].every(([dx, dy]) => {
          const hit = document.elementFromPoint(p.x + dx, p.y + dy);
          return !!hit && hit.tagName === "CANVAS" && !!hit.closest('[data-testid="scene-host"]');
        });
        return bare ? p : null;
      });
    const host = (await page.getByTestId("scene-host").boundingBox())!;
    const panel = (await page.getByTestId("edit-panel").boundingBox())!;
    const free = { x: host.x + host.width / 2, y: (Math.max(host.y, panel.y + panel.height) + host.y + host.height) / 2 };
    for (let k = 0; k < 20 && !(await target()); k++) {
      await page.mouse.move(free.x, free.y);
      await page.mouse.wheel(0, 240);
      await waitFrame(page);
    }
    const at = (await target())!;
    expect(at, "the corner of the square is not on the bare canvas").not.toBeNull();
    await page.screenshot({ path: shot("editor-polygon.png") });
    await page.mouse.move(at.x + 6, at.y + 4, { steps: 4 });
    await expect(page.getByTestId("edit-snap-label")).toContainText("вершина", { timeout: 10_000 });
    await page.mouse.click(at.x + 6, at.y + 4);
    await expect.poll(() => edPoints(page, -1), { timeout: 10_000 }).toEqual([[1010, 1000, 0]]);
    await page.screenshot({ path: shot("editor-snap.png") });

    // ---- «Завершить» with a line being drawn: the own question, «Не сохранять» leaves version 4 as it is -----------
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("lines-choice")).toBeVisible();
    await page.getByTestId("choice-discard").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);
    await expect(page.getByTestId(`tree-layer-edited-${layerId}`)).toHaveCount(0);
  } finally {
    await close(app);
  }
  console.log(`editor: versions and snap — ${((Date.now() - started) / 1000).toFixed(1)} s`);
});

test("string editor: a save of somebody else is refused with 409 and the own window of three actions", async () => {
  test.setTimeout(300_000);
  const started = Date.now();
  const { app, page } = await launch({ BF_E2E: "1" });
  const choice = page.getByTestId("lines-choice");
  try {
    await emptyProject(page, "Чужое сохранение");
    const layerId = await newLayer(page, "Борт");
    expect((await run(page, "lines.add_line", { points: "[[0, 0, 0], [20, 0, 0], [20, 20, 0]]" })).ok).toBe(true);
    await ctrlS(page);
    await expect(page.getByTestId("edit-version")).toHaveText("в. 2", { timeout: 30_000 });

    // my edit on version 2, while somebody else saves on the same version 2 → version 3 in the core
    expect((await run(page, "lines.add_line", { points: "[[0, 30, 0], [20, 30, 0]]" })).ok).toBe(true);
    await expect(page.getByTestId("edit-dirty")).toBeVisible();
    const other = await foreignSave(page, layerId, 2, [100, 100, 5, 120, 100, 5]);
    expect(other.status, JSON.stringify(other.body)).toBe(200);
    expect(other.body.version).toBe(3);
    // the same base once more: the core itself answers 409 — nothing is overwritten
    const again = await foreignSave(page, layerId, 2, [0, 0, 0, 1, 1, 0]);
    expect(again.status, JSON.stringify(again.body)).toBe(409);
    expect(JSON.stringify(again.body)).toContain("layer_changed");

    // ---- Ctrl+S: refused, the own window (not window.confirm) with three actions; «Отмена» — nothing changes ----
    await ctrlS(page);
    await expect(choice).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("lines-choice-message")).toContainText("версия 3");
    await expect(page.getByTestId("lines-choice-message")).toContainText("Ничего не записано");
    for (const id of ["new", "reload", "cancel"]) await expect(page.getByTestId(`choice-${id}`)).toBeVisible();
    await expect(choice.locator('[data-testid^="choice-"]')).toHaveCount(3);
    await page.getByTestId("choice-cancel").click();
    await expect(choice).toHaveCount(0);
    expect(await edState(page)).toMatchObject({ version: 2, dirty: true, lines: 2 });

    // ---- again → «Перечитать слой»: the version of the other one, my edit is gone ---------------------------------
    await ctrlS(page);
    await expect(choice).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("choice-reload").click();
    await expect(page.getByTestId("edit-version")).toHaveText("в. 3", { timeout: 30_000 });
    expect(await edState(page)).toMatchObject({ version: 3, dirty: false, lines: 1 });
    expect(await edPoints(page, 0)).toEqual([[100, 100, 5], [120, 100, 5]]);

    // ---- a new edit, another save of somebody else → «Сохранить как новый слой»: the edit is kept apart ----------
    expect((await run(page, "lines.add_line", { points: "[[0, 50, 0], [30, 50, 0]]" })).ok).toBe(true);
    expect((await foreignSave(page, layerId, 3, [200, 200, 0, 210, 200, 0])).status).toBe(200);
    await ctrlS(page);
    await expect(choice).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("lines-choice-message")).toContainText("версия 4");
    await page.getByTestId("choice-new").click();
    await expect(page.getByTestId("edit-layer")).toHaveText("Борт (правка)", { timeout: 30_000 });
    const made = (await edState(page))!;
    expect(made.layerId).not.toBe(layerId);
    expect(made).toMatchObject({ dirty: false, lines: 2 });
    await expect(page.getByTestId(`tree-layer-edited-${made.layerId}`)).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: shot("editor-conflict.png") });

    // the layer of the other one is left as he saved it
    const kept = await page.evaluate(async (id) => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/lines/layers/${id}/versions`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()) as { version: number };
    }, layerId);
    expect(kept.version).toBe(4);
    await page.getByTestId("edit-finish").click();
    await expect(page.getByTestId("edit-panel")).toHaveCount(0);
  } finally {
    await close(app);
  }
  console.log(`editor: conflict — ${((Date.now() - started) / 1000).toFixed(1)} s`);
});
