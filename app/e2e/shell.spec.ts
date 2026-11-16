// Э1 shell acceptance (ТЗ 7.0.14) on the real Electron window.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { close, launch, setWindowSize, shot } from "./helpers";

test("first start: dark theme, tree and properties open, bottom closed; 1024 px without horizontal scroll", async () => {
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
    await expect(page.getByRole("tree")).toBeVisible();
    await expect(page.getByLabel("Правая панель")).toBeVisible();
    await expect(page.getByLabel("Нижняя панель")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Главная" })).toHaveAttribute("aria-selected", "true");

    await setWindowSize(app, 1024, 700);
    await page.waitForTimeout(500);
    const layout = await page.evaluate(() => {
      const r = (label: string) => document.querySelector(`[aria-label="${label}"]`)!.getBoundingClientRect();
      return {
        scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        width: window.innerWidth,
        right: r("Правая панель").right,
        ribbon: r("Лента").right,
      };
    });
    expect(layout.scroll).toBeLessThanOrEqual(0);
    expect(layout.right).toBeLessThanOrEqual(layout.width);
    expect(layout.ribbon).toBeLessThanOrEqual(layout.width);
    await page.screenshot({ path: shot("shell-1024.png") });
  } finally {
    await close(app);
  }
});

test("hotkeys in the Russian layout, palette-free command log, theme and layout survive a restart", async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-persist-"));
  let { app, page } = await launch({}, userData);
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    // Ctrl+Ё (Backquote) opens the bottom dock; a physical key, whatever the layout.
    await page.keyboard.press("Control+Backquote");
    await expect(page.getByLabel("Нижняя панель")).toBeVisible();
    await page.getByRole("tab", { name: "Журнал команд" }).click();
    await page.getByLabel("Тема").click();
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");
    await expect(page.getByLabel("Журнал команд")).toContainText("Тема");
    // Ctrl+B hides the tree.
    await page.keyboard.press("Control+KeyB");
    await expect(page.getByRole("tree")).toHaveCount(0);
    await page.waitForTimeout(700); // debounced settings write
  } finally {
    await close(app);
  }
  ({ app, page } = await launch({}, userData));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");
    await expect(page.getByRole("tree")).toHaveCount(0);
    await expect(page.getByLabel("Нижняя панель")).toBeVisible();
    await page.screenshot({ path: shot("shell-light-restored.png") });
  } finally {
    await close(app);
  }
});

test("palette runs commands; own hotkey: conflict dialog, «· изменено», survives a restart", async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-keys-"));
  let { app, page } = await launch({}, userData);
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.keyboard.press("Control+KeyK");
    const input = page.getByLabel("Поиск команды");
    await expect(input).toBeFocused();
    await input.fill("светлая тема");
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Палитра команд")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");

    // Single keys do not fire inside a text field.
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("настройка");
    await page.keyboard.press("Enter");
    const settings = page.getByRole("dialog", { name: "Настройка" });
    await expect(settings).toBeVisible();
    await settings.getByLabel("Поиск по функции или сочетанию").fill("Вписать");
    await settings.getByRole("button", { name: "Назначить" }).first().click();
    await page.keyboard.press("KeyT"); // «T» belongs to «В план»
    const conflict = page.getByRole("dialog", { name: "Сочетание уже занято" });
    await expect(conflict).toBeVisible();
    await conflict.getByRole("button", { name: "Переназначить" }).click();
    await expect(settings.getByText("· изменено")).toHaveCount(1);
    await expect(settings.getByRole("row", { name: /Вписать/ })).toContainText(/\bT\b/);
    await page.screenshot({ path: shot("settings-shortcuts.png") });
    await page.waitForTimeout(700);
  } finally {
    await close(app);
  }
  ({ app, page } = await launch({}, userData));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("вписать");
    await expect(page.getByRole("option").first()).toContainText("T");
  } finally {
    await close(app);
  }
});

test("«Лицензия» shows the PC code and the trial period", async () => {
  const { app, page } = await launch();
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("лицензия");
    await page.keyboard.press("Enter");
    const win = page.getByRole("dialog", { name: "Лицензия" });
    await expect(win.getByTestId("hwid")).toHaveText(/^[0-9A-F]{8}-[0-9A-F]{8}-[0-9A-F]{8}$/);
    await expect(win.getByTestId("license-message")).toContainText("Пробный период: осталось 30 дн.");
    await page.screenshot({ path: shot("license.png") });
  } finally {
    await close(app);
  }
});
