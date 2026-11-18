// Э6 acceptance of the displays along holes (ТЗ 7.16): the form «Содержания…» on the demo project — a draft is not
// in the tree until «Сохранить»; «Отменить изменения» and Ctrl+Z; a layer style saved, linked to two displays and
// changed by «Изменить стиль»; the library exported to .bfstyle.json and imported into another project.
import { expect, test, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, setWindowSize, shot } from "./helpers";

async function command(page: Page, query: string) {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByLabel("Палитра команд").getByRole("option").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

async function answer(page: Page, title: string, text: string) {
  const dialog = page.getByRole("dialog", { name: title });
  await dialog.getByRole("textbox").fill(text);
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
}

async function core<T>(page: Page, method: string, url: string, body?: unknown): Promise<T> {
  return page.evaluate(async ([m, u, b]) => {
    const c = window.bf!.core()!;
    const r = await fetch(`${c.baseUrl}/api/v1${u}`, { method: m as string, headers: { "X-BF-Token": c.token, "Content-Type": "application/json" }, body: b === undefined ? undefined : JSON.stringify(b) });
    return r.json();
  }, [method, url, body] as const) as Promise<T>;
}

/** The form of a set from the menu of the set in the data tree; `index` — its window among the open forms. */
async function openForm(page: Page, item: string, index: number): Promise<Locator> {
  await page.getByRole("treeitem", { name: /Скважины 2025/ }).click({ button: "right" });
  await page.getByRole("menuitem", { name: item }).click();
  await expect(page.getByRole("dialog", { name: /Содержания \(новое\) · Скважины 2025/ })).toBeVisible();
  const form = page.getByRole("form", { name: "Отображение: Содержания" }).nth(index);
  await expect(form.getByRole("listbox", { name: "Слои отображения" })).toContainText("AU, г/т", { timeout: 30_000 });
  return form;
}

const kindOf = (win: Locator) => win.getByRole("listbox", { name: "Слои отображения" }).getByRole("option").first();
const note = (win: Locator) => win.locator(".dh-note");

test("displays along holes: draft not in the tree until saved, revert, linked style, .bfstyle.json to another project", async () => {
  test.setTimeout(240_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-downhole-"));
  const { app, page } = await launch();
  try {
    await setWindowSize(app, 1500, 950);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 30_000 });
    await expect(page.getByRole("treeitem", { name: /Скважины 2025/ })).toBeVisible({ timeout: 30_000 });
    const tree = page.getByRole("group", { name: "Отображения по стволу" });

    // «Содержания…»: AU as bars; the draft is not in the tree
    const form1 = await openForm(page, "Содержания…", 0);
    await expect(kindOf(form1)).toContainText("столбцы");
    await expect(note(form1)).toContainText("в дерево попадёт по кнопке");
    await expect(tree).toHaveCount(0);

    // «Сохранить»: a row under the set, switched on
    await form1.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(note(form1)).toContainText("Сохранено в дереве: Скважины 2025 → «AU · столбцы»");
    await expect(tree.getByRole("treeitem", { name: /AU · столбцы/ })).toBeVisible();
    await expect(tree.getByRole("checkbox", { name: /AU · столбцы/ })).toBeChecked();
    await expect(page.getByRole("dialog", { name: "AU · столбцы · Скважины 2025" })).toBeVisible();

    // a change, Ctrl+Z, again, then «Отменить изменения» returns the saved state
    const kinds = form1.getByRole("radiogroup", { name: "Вид слоя" });
    await kinds.getByRole("radio", { name: "Линия" }).click();
    await expect(kindOf(form1)).toContainText("линия");
    await form1.getByRole("listbox", { name: "Слои отображения" }).getByRole("option").first().focus();
    await page.keyboard.press("Control+KeyZ");
    await expect(kindOf(form1)).toContainText("столбцы");
    await page.keyboard.press("Control+KeyY");
    await expect(kindOf(form1)).toContainText("линия");
    await expect(form1.getByRole("button", { name: "Сохранить", exact: true })).toBeEnabled();
    await form1.getByRole("button", { name: "Отменить изменения" }).click();
    await expect(kindOf(form1)).toContainText("столбцы");
    await expect(note(form1)).toContainText("Сохранено.");
    const saved = await core<{ charts: { name: string; settings: { tracks: { type: string }[] } }[] }>(page, "GET", "/charts?kind=striplog");
    expect(saved.charts.map((c) => [c.name, c.settings.tracks[0].type])).toEqual([["AU · столбцы", "bars"]]);

    // the layer style: saved, linked, the display saved with the link
    await form1.getByRole("button", { name: "Стиль слоя: действия" }).click();
    await form1.getByRole("menuitem", { name: "Сохранить как…" }).click();
    await answer(page, "Стиль слоя: сохранить как", "Au столбцы");
    await expect(note(form1)).toContainText("Стиль «Au столбцы» сохранён");
    await form1.getByRole("button", { name: "Стиль слоя: связать" }).click();
    await expect(form1.getByRole("button", { name: "Стиль слоя: отвязать" })).toBeVisible();
    await form1.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(note(form1)).toContainText("Сохранено: Скважины 2025");

    // a second display linked to the same style
    const form2 = await openForm(page, "Содержания…", 1);
    await form2.getByRole("button", { name: "Стиль слоя: выбрать" }).click();
    await form2.getByRole("menuitem", { name: /Au столбцы/ }).click();
    await form2.getByRole("button", { name: "Стиль слоя: связать" }).click();
    await form2.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(note(form2)).toContainText("«AU · столбцы (2)»");
    await expect(tree.getByRole("treeitem")).toHaveCount(2);

    // a change of a linked layer offers «Изменить стиль»: both displays are rebuilt
    await page.getByRole("dialog", { name: "AU · столбцы · Скважины 2025" }).click({ position: { x: 12, y: 10 } }); // the first window to the front
    await form1.getByRole("radiogroup", { name: "Вид слоя" }).getByRole("radio", { name: "Ступени" }).click();
    await expect(form1.getByRole("status").filter({ hasText: "Был связан со стилем «Au столбцы»" })).toBeVisible();
    await expect(kindOf(form2)).toContainText("столбцы");
    await form1.getByRole("button", { name: "Изменить стиль" }).click();
    await expect(note(form1)).toContainText("связанные отображения перестроены");
    await expect(kindOf(form2)).toContainText("ступени");
    const styles = await core<{ styles: { name: string; uid: string; body: { type: string } }[] }>(page, "GET", "/styles");
    expect(styles.styles.map((s) => [s.name, s.body.type])).toEqual([["Au столбцы", "steps"]]);
    await page.screenshot({ path: shot("downhole-form.png") });

    // export of the library through the save dialog
    const file = path.join(dir, "styles.bfstyle.json");
    await app.evaluate(({ dialog }, f) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [f] })) as typeof dialog.showOpenDialog;
    }, file);
    await command(page, "Экспорт стилей");
    // the file is renamed into place whole (main.ts writeWhole): it exists only once written
    await expect.poll(() => fs.existsSync(file), { timeout: 30_000 }).toBe(true);
    const exported = JSON.parse(fs.readFileSync(file, "utf8")) as { format: string; styles: { name: string; level: string; data_kind: string; body: unknown; default_for: unknown }[] };
    expect(exported.format).toBe("bedrockforge-styles");
    expect(exported.styles.map((s) => s.name)).toEqual(["Au столбцы"]);

    // another project: the forms close, the import gives the same styles
    await command(page, "новый проект");
    await answer(page, "Новый проект", "Проект Э6 стили");
    await expect(page.getByTestId("project-selector")).toContainText("Проект Э6 стили", { timeout: 30_000 });
    await expect(page.getByRole("dialog", { name: /Скважины 2025/ })).toHaveCount(0);
    expect((await core<{ styles: unknown[] }>(page, "GET", "/styles")).styles).toEqual([]);
    await command(page, "Импорт стилей");
    await expect.poll(async () => (await core<{ styles: unknown[] }>(page, "GET", "/styles")).styles.length, { timeout: 30_000 }).toBe(1);
    const imported = (await core<{ styles: { name: string; level: string; data_kind: string; body: unknown; default_for: unknown }[] }>(page, "GET", "/styles")).styles;
    const pick = (s: { name: string; level: string; data_kind: string; body: unknown; default_for: unknown }) => ({ name: s.name, level: s.level, data_kind: s.data_kind, body: s.body, default_for: s.default_for });
    expect(imported.map(pick)).toEqual(exported.styles.map(pick));

    // the same file again: the name is taken — the window asks, «оставить оба» keeps both
    await command(page, "Импорт стилей");
    const win = page.getByRole("dialog", { name: "Импорт стилей" }).first();
    await expect(win.getByRole("combobox", { name: "Конфликт: Au столбцы" })).toHaveValue("keep_both");
    await win.getByRole("button", { name: "Импортировать" }).click();
    await expect(win.getByRole("status")).toContainText("Добавлено: 1");
    expect((await core<{ styles: { name: string }[] }>(page, "GET", "/styles")).styles.map((s) => s.name).sort()).toEqual(["Au столбцы", "Au столбцы (2)"]);
  } finally {
    await close(app);
  }
});
