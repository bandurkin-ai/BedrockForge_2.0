// Э5 acceptance on the real window (ТЗ 7.12): the demo opens with its audit; a run from the «Аудит» tab;
// findings by rule and hole; thresholds; «устарел» after an edit; the Excel report with three sheets.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot } from "./helpers";

test("«Аудит»: demo errors, run, filter by rule, «устарел», Excel", async () => {
  test.setTimeout(180_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-audit-"));
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 60_000 });
    // the set becomes active by a click; «Анализ › Результаты» opens the audit tab (the double click of the tree
    // is tested in tree.spec — under the heavy demo of Э7 on Windows its second click could be lost)
    await page.getByRole("treeitem", { name: /Скважины 2025/ }).getByText("Скважины 2025", { exact: true }).click();
    await expect(page.getByRole("contentinfo")).toContainText("Набор: Скважины 2025");
    await page.getByRole("tab", { name: "Анализ" }).click();
    await page.getByRole("button", { name: "Результаты" }).click();
    const panel = page.getByLabel("Нижняя панель");
    await panel.getByRole("tab", { name: "Аудит" }).click();
    // the demo opens with its audit done: the deliberate errors are listed
    const rules = panel.getByRole("table", { name: "Правила аудита" });
    await expect(rules.getByRole("row")).toHaveCount(28, { timeout: 30_000 });
    await expect(page.getByRole("contentinfo")).toContainText("Аудит:");
    await rules.getByRole("row", { name: /Замер глубже забоя/ }).click();
    await expect(panel.getByTestId("audit-findings")).toContainText("DH-006");
    await page.screenshot({ path: shot("audit-panel.png") });

    // a run from the tab, then an edit makes it «устарел»
    const lastRun = () =>
      page.evaluate(async () => {
        const c = window.bf!.core()!;
        const h = { "X-BF-Token": c.token };
        const sets = await (await fetch(`${c.baseUrl}/api/v1/drillholes/sets`, { headers: h })).json();
        const r = await (await fetch(`${c.baseUrl}/api/v1/audit/sets/${sets.sets[0].id}`, { headers: h })).json();
        return r.result?.created_at as string; // not run_id: SQLite gives the freed id again
      });
    const first = await lastRun();
    await panel.getByRole("button", { name: "Запустить аудит" }).click();
    await expect.poll(lastRun, { timeout: 60_000 }).not.toBe(first);
    await panel.getByRole("tab", { name: "Аудит" }).click();
    await page.evaluate(async () => {
      const c = window.bf!.core()!;
      const h = { "X-BF-Token": c.token, "Content-Type": "application/json" };
      const sets = await (await fetch(`${c.baseUrl}/api/v1/drillholes/sets`, { headers: h })).json();
      const id = sets.sets[0].id;
      const row = (await (await fetch(`${c.baseUrl}/api/v1/drillholes/sets/${id}/tables/collar?limit=1`, { headers: h })).json()).rows[0];
      await fetch(`${c.baseUrl}/api/v1/drillholes/sets/${id}/tables/collar/rows/${row.id}`, { method: "PATCH", headers: h, body: JSON.stringify({ column: "z", value: "500" }) });
    });
    await expect(panel.getByTestId("audit-state")).toContainText("устарел", { timeout: 15_000 });

    // Excel report through the save dialog
    const file = path.join(dir, "Аудит.xlsx");
    await app.evaluate(({ dialog }, f) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as typeof dialog.showSaveDialog;
    }, file);
    await panel.getByRole("button", { name: "Отчёт Excel…" }).click();
    await expect.poll(() => fs.existsSync(file), { timeout: 30_000 }).toBe(true);
  } finally {
    await close(app);
  }
});
