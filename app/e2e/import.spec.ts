// Э3 acceptance on the real window (ТЗ 7.7, 7.9): the drillhole wizard from files to a set, the operation log.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot } from "./helpers";

test("wizard «Импорт скважин»: CSV cp1251, TXT and Excel-like CSV → one set; force load; log", async () => {
  test.setTimeout(180_000);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-import-"));
  const collar = path.join(dir, "Устья.csv");
  const cp = (s: string) => Buffer.from([...s].map((ch) => { const c = ch.charCodeAt(0); return c >= 0x410 && c <= 0x44f ? c - 0x410 + 0xc0 : c === 0x401 ? 0xa8 : c === 0x451 ? 0xb8 : c; }));
  fs.writeFileSync(collar, cp("Скважина;X, м;Y, м;Z, м;Глубина, м\r\nC1;100;200;300;50\r\nC2;110;210;310;40\r\n"));
  const survey = path.join(dir, "survey.txt");
  fs.writeFileSync(survey, "HOLEID\tDEPTH\tAZIMUTH\tDIP\nC1\t0\t90\t-60\nC2\t0\t0\t-90\n");
  const assay = path.join(dir, "assay.csv");
  fs.writeFileSync(assay, "Hole_ID,From_m,To_m,Au_ppm\nC1,0,1,<0.01\nC1,1,2,1.5\nC2,0,2,abc\n");

  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(page.getByTestId("project-selector")).toContainText("Демо", { timeout: 30_000 });
    const before = await page.evaluate(async () => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/projects/current`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()).project.summary;
    });
    await app.evaluate(({ dialog }, files) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: files })) as typeof dialog.showOpenDialog;
    }, [assay, collar, survey]);
    await page.getByRole("button", { name: "Скважины", exact: true }).first().click();
    const wiz = page.getByRole("dialog", { name: "Импорт скважин" });
    await wiz.getByRole("button", { name: "Добавить файлы…" }).click();
    // the collar file stays on step 1; the others went to their steps. Files are read one after another: a card
    // still being read may stand alone on step 1 for a moment, so wait for the final state (the collar card there).
    await expect(wiz.getByTestId("import-file").filter({ hasText: "Устья.csv" })).toHaveCount(1);
    await expect(wiz.getByTestId("import-file")).toHaveCount(1);
    await expect(wiz.getByTestId("import-file")).toContainText("Устья.csv");
    await expect(wiz.getByLabel("Тип таблицы")).toHaveValue("collar");
    await wiz.getByRole("button", { name: /2\. Инклинометрия/ }).click();
    await expect(wiz.getByTestId("import-file")).toContainText("survey.txt");
    await wiz.getByRole("button", { name: /3\. Интервалы/ }).click();
    await expect(wiz.getByTestId("import-issues")).toContainText("нечисловых");
    await wiz.getByText("Загрузить с ошибками — исправлю в BedrockForge").click();
    await expect(wiz.getByRole("table", { name: "Предпросмотр" })).toContainText("0.005");
    await page.screenshot({ path: shot("import-wizard.png") });
    await wiz.getByRole("button", { name: /4\. Импорт/ }).click();
    await wiz.getByLabel("Название нового набора").fill("Набор Э3");
    await wiz.getByRole("button", { name: "Импортировать" }).click();
    const row = page.getByTestId("job-import.drillholes").first();
    await expect(row.getByTestId("job-status")).toHaveText("Готово", { timeout: 60_000 });
    const summary = await page.evaluate(async () => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/projects/current`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()).project.summary;
    });
    // the demo has its own set «Скважины 2025»: the import adds one set with 2 holes, 2 surveys, 3 samples
    expect({ sets: summary.sets - before.sets, holes: summary.holes - before.holes, surveys: summary.surveys - before.surveys, assays: summary.assays - before.assays }).toEqual({ sets: 1, holes: 2, surveys: 2, assays: 3 });

    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("журналы");
    await expect(page.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Enter");
    await page.getByRole("dialog", { name: "Журналы" }).getByRole("button", { name: /Импорт скважин/ }).click();
    await expect(page.getByTestId("op-log-text")).toContainText("POST /drillholes/import");
  } finally {
    await close(app);
  }
});
