// Э8, подшаг 8.25 (ADR 0013 §5; ТЗ 7.21, 9.4): the window «Экспорт каркасов» on the demo project. The command
// `wireframe.export` opens it with the wireframe and the format; «Сохранить как…» → «Экспортировать» starts
// the job `export.wireframes`, the file appears. A Surpac DTM written twice: its .str (not asked about by the
// save window) is listed in our own window «Заменить файлы?», the export goes on after «Заменить». TRIDB from
// the menu of a row of the tree.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, waitFrame } from "./helpers";

test("wireframes export: OBJ through the window, the question before a file is replaced, TRIDB from the tree", async () => {
  test.setTimeout(300_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-wf-export-"));
  const { app, page } = await launch({ BF_E2E: "1" });
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    await waitFrame(page);

    const wireframes = await page.evaluate(async () => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/wireframes`, { headers: { "X-BF-Token": c.token } });
      return ((await r.json()) as { wireframes: { id: number; name: string; dtm: unknown; triangle_count: number }[] }).wireframes;
    });
    // the demo holds the DTM «Рельеф»; wireframes of the ore body come with подшаг 8.26
    const solid = wireframes.find((w) => w.dtm === null && w.triangle_count > 0) ?? wireframes.find((w) => w.triangle_count > 0)!;
    expect(solid).toBeDefined();
    const save = (file: string) =>
      app.evaluate(({ dialog }, f) => {
        dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
      }, file);
    const run = (id: string, args: Record<string, unknown>) => page.evaluate(([i, a]) => window.__bfScene!.run(i as string, a as Record<string, unknown>), [id, args] as const);

    // ---- OBJ: the window with the wireframe checked ------------------------------------------------------------
    await run("wireframe.export", { format: "obj", wireframe_id: solid.id });
    const win = page.getByRole("dialog", { name: "Экспорт каркасов" });
    await expect(win).toBeVisible();
    await expect(win.getByLabel("Формат файла")).toHaveValue("obj");
    await expect(win.getByLabel(`Каркас «${solid.name}»`)).toBeChecked();
    await expect(win.getByRole("button", { name: "Экспортировать" })).toBeDisabled(); // no file yet
    const obj = path.join(dir, "тело.obj");
    await save(obj);
    await win.getByRole("button", { name: "Сохранить как…" }).click();
    await expect(win.getByLabel("Файл экспорта")).toHaveValue(obj);
    await win.getByRole("button", { name: "Экспортировать" }).click();
    await expect(win).toHaveCount(0);
    await expect.poll(() => fs.existsSync(obj) && fs.statSync(obj).size > 0, { timeout: 60_000 }).toBe(true);
    const text = fs.readFileSync(obj, "utf8");
    expect(text).toMatch(/^v /m);
    expect(text).toMatch(/^f /m);

    // ---- Surpac DTM twice: the save window asked about the .dtm only, its .str is asked about in our own window --
    const dtm = path.join(dir, "тело.dtm");
    const str = path.join(dir, "тело.str");
    await save(dtm);
    for (const again of [false, true]) {
      await run("wireframe.export", { format: "dtm", wireframe_id: solid.id });
      await expect(win).toBeVisible();
      await win.getByRole("button", { name: "Сохранить как…" }).click();
      await win.getByRole("button", { name: "Экспортировать" }).click();
      if (again) {
        const ask = page.getByRole("dialog", { name: "Заменить файлы?" });
        await expect(ask).toContainText("тело.str");
        await expect(ask).not.toContainText("тело.dtm");
        await ask.getByRole("button", { name: "Заменить" }).click();
      }
      await expect(win).toHaveCount(0);
      await expect.poll(() => fs.existsSync(dtm) && fs.existsSync(str), { timeout: 60_000 }).toBe(true);
    }

    // ---- TRIDB from the menu of the row of the tree ---------------------------------------------------------------
    const tridb = path.join(dir, "тело.tridb");
    await save(tridb);
    await page.getByTestId(`tree-${solid.dtm === null ? "wireframe" : "dtm"}-${solid.id}`).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Экспорт…" }).click();
    await expect(win).toBeVisible();
    await expect(win.getByLabel("Формат файла")).toHaveValue("tridb");
    await win.getByRole("button", { name: "Сохранить как…" }).click();
    await win.getByRole("button", { name: "Экспортировать" }).click();
    await expect(win).toHaveCount(0);
    await expect.poll(() => fs.existsSync(tridb) && fs.statSync(tridb).size > 0, { timeout: 60_000 }).toBe(true);
    expect(fs.readFileSync(tridb).subarray(0, 15).toString("latin1")).toBe("SQLite format 3");
  } finally {
    await close(app);
  }
});
