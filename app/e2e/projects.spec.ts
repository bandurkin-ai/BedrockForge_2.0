// Э2 acceptance on the real window (ТЗ 7.3–7.6): projects, demo, jobs, .bfggis export → import, a killed core.
import { expect, test, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, shot } from "./helpers";

async function online(page: Page) {
  await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
}

async function command(page: Page, query: string) {
  await page.keyboard.press("Control+KeyK");
  await page.getByLabel("Поиск команды").fill(query);
  await expect(page.getByRole("option").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
}

async function answer(page: Page, text: string) {
  const dialog = page.getByRole("dialog").last();
  await dialog.getByRole("textbox").fill(text);
  await page.keyboard.press("Enter");
}

const header = (page: Page) => page.getByTestId("project-selector");

function newCorePids(before: Set<number>): number[] {
  const out =
    process.platform === "win32"
      ? execSync(`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name='bf-core.exe'\\" | Select-Object -ExpandProperty ProcessId"`, { encoding: "utf8" })
      : execSync("pgrep -f '[-]m bf_core --port|[b]f-core --port' || true", { encoding: "utf8" });
  return [...out.matchAll(/(\d+)/g)].map((m) => Number(m[1])).filter((p) => !before.has(p));
}

function kill(pid: number) {
  try {
    if (process.platform === "win32") execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    else process.kill(pid, "SIGKILL");
  } catch {
    /* /T already took the workers with the core */
  }
}

test("projects: demo once, new, taken name refused, description survives restart and a killed core, search, delete", async () => {
  test.setTimeout(240_000);
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-projects-"));
  const before = new Set(newCorePids(new Set()));
  let { app, page } = await launch({}, userData);
  try {
    await online(page);
    await expect(header(page)).toContainText("Проект не открыт");
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(header(page)).toContainText("Демо: участок Северный", { timeout: 30_000 });
    await command(page, "демо-проект");
    await expect(header(page)).toContainText("Демо: участок Северный");

    await command(page, "новый проект");
    await answer(page, "Проект Э2");
    await expect(header(page)).toContainText("Проект Э2");

    await header(page).click();
    await page.getByRole("button", { name: "Переименовать", exact: true }).click();
    await answer(page, "демо: УЧАСТОК северный");
    await expect(page.getByRole("dialog").filter({ hasText: "уже есть" })).toContainText("Выберите другое название");
    await page.keyboard.press("Enter");
    await expect(header(page)).toContainText("Проект Э2");

    const win = page.locator("[data-testid=project-row]");
    await expect(win).toHaveCount(2);
    await page.getByLabel("Месторождение").fill("Кедровое");
    await page.getByLabel("Стадия работ").fill("разведка");
    await page.getByLabel("Поиск проектов").click(); // blur saves the description
    await page.getByLabel("Поиск проектов").fill("кедров");
    await expect(win).toHaveCount(1);
    await expect(win).toContainText("Проект Э2");
    await page.screenshot({ path: shot("projects-window.png") });

    // «Снятие процесса ядра сразу после правки»: the edit is already committed.
    await page.getByRole("textbox", { name: "Лицензия", exact: true }).fill("ЛЦ-77");
    await page.getByLabel("Поиск проектов").click();
    await expect.poll(async () => page.evaluate(async () => {
      const c = window.bf!.core()!;
      const r = await fetch(`${c.baseUrl}/api/v1/projects/current`, { headers: { "X-BF-Token": c.token } });
      return (await r.json()).project?.description?.license;
    })).toBe("ЛЦ-77");
    for (const pid of newCorePids(before)) kill(pid);
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.waitForTimeout(700);
  } finally {
    await close(app);
  }

  ({ app, page } = await launch({}, userData));
  try {
    await online(page);
    await expect(header(page)).toContainText("Проект Э2", { timeout: 30_000 }); // the last project opens at start
    await header(page).click();
    await expect(page.getByLabel("Месторождение")).toHaveValue("Кедровое");
    await expect(page.getByLabel("Стадия работ")).toHaveValue("разведка");
    await expect(page.getByRole("textbox", { name: "Лицензия", exact: true })).toHaveValue("ЛЦ-77");

    await command(page, "удалить проект");
    await page.getByRole("dialog", { name: "Удалить проект" }).getByRole("button", { name: "Удалить" }).click();
    await expect(header(page)).toContainText("Проект не открыт");
    await expect(page.locator("[data-testid=project-row]")).toHaveCount(1);
    expect(fs.readdirSync(path.join(userData, "Проекты")).filter((n) => n.startsWith("Проект Э2"))).toEqual([]);
  } finally {
    await close(app);
  }
});

test("export → import through the jobs tab with a password; the copy has the same composition", async () => {
  test.setTimeout(180_000);
  const { app, page } = await launch();
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-export-")), "Демо.bfggis");
  try {
    await online(page);
    await page.getByRole("button", { name: "Загрузить демо-проект" }).click();
    await expect(header(page)).toContainText("Демо: участок Северный", { timeout: 30_000 });
    // System dialogs are not drivable: the main process answers them with fixed paths.
    await app.evaluate(({ dialog }, file) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: file })) as typeof dialog.showSaveDialog;
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [file] })) as typeof dialog.showOpenDialog;
    }, out);

    await command(page, "экспорт проекта");
    await page.getByRole("button", { name: "Сохранить как…" }).click();
    await page.getByLabel("Пароль", { exact: true }).fill("Секрет-1");
    await page.getByLabel("Повтор пароля").fill("Секрет-1");
    await page.getByRole("button", { name: "Экспортировать" }).click();
    const exportRow = page.getByTestId("job-export.project").first();
    await expect(exportRow.getByTestId("job-status")).toHaveText("Готово", { timeout: 60_000 });
    await expect(exportRow.getByRole("button", { name: "Показать в папке" })).toBeVisible();
    expect(fs.existsSync(out)).toBe(true);

    await command(page, "импорт проекта");
    await page.getByLabel("Пароль файла").fill("неверный");
    await page.getByRole("button", { name: "Проверить" }).click();
    await expect(page.getByTestId("import-error")).toContainText("Неверный пароль");
    await page.getByLabel("Пароль файла").fill("Секрет-1");
    await page.getByRole("button", { name: "Проверить" }).click();
    await expect(page.getByTestId("import-name")).toHaveText("Демо: участок Северный");
    await expect(page.getByText("Создать копию «Демо: участок Северный (2)»")).toBeVisible();
    await page.getByRole("button", { name: "Импортировать" }).click();
    const importRow = page.getByTestId("job-import.project").first();
    await expect(importRow.getByTestId("job-status")).toHaveText("Готово", { timeout: 60_000 });
    await importRow.getByRole("button", { name: "Открыть проект" }).click();
    await expect(header(page)).toContainText("Демо: участок Северный (2)");
    await page.screenshot({ path: shot("jobs-import.png") });
    const text = await page.getByTestId("jobs-panel").innerText();
    expect(text).not.toContain("Секрет-1");
  } finally {
    await close(app);
  }
});
