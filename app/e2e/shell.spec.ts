// Э1 shell acceptance (ТЗ 7.0.14) on the real Electron window.
import { expect, test, type Page } from "@playwright/test";
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

    // «Главная» without «Вид» and «Разрез» fits 1024 px; «Настройки проекта» is a tab (ADR 0008)
    await expect(page.getByRole("tab", { name: "Настройки проекта" })).toBeVisible();

    // «О производителе» (Д-12): ⓘ opens the plate, Esc closes it, a click elsewhere too; no window.open
    await page.evaluate(() => {
      (window as unknown as { __opened: number }).__opened = 0;
      window.open = () => {
        (window as unknown as { __opened: number }).__opened++;
        return null;
      };
    });
    const about = page.getByRole("dialog", { name: "О производителе" });
    await page.getByRole("banner").getByRole("button", { name: "О производителе" }).click();
    await expect(about).toContainText("Bandurkin Mikhail");
    await expect(about).toContainText(/Версия программы: \d/);
    await expect(about).toContainText(/Версия ядра: \d/);
    await page.screenshot({ path: shot("about.png") });
    await page.keyboard.press("Escape");
    await expect(about).toHaveCount(0);
    await page.getByRole("banner").getByRole("button", { name: "О производителе" }).click();
    await expect(about).toBeVisible();
    await page.getByRole("tree").click({ position: { x: 20, y: 200 } });
    await expect(about).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __opened: number }).__opened)).toBe(0);
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

// Э8, подшаг 8.21 (ADR 0013 §5–§6; ТЗ 7.35 Д-13): the path of ODA File Converter. A file with the name of the
// converter stands for it — the program is looked for, never started (the real one is checked by hand on Windows).
test("«Настройка › Внешние программы»: ODA File Converter — choose, check, a wrong path, restart, clear", async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "bf-e2e-oda-"));
  const folder = path.join(userData, "Программы и утилиты", "ODAFileConverter 26.10.0"); // Cyrillic, spaces, the version
  fs.mkdirSync(folder, { recursive: true });
  const exe = path.join(folder, process.platform === "win32" ? "ODAFileConverter.exe" : "ODAFileConverter");
  fs.writeFileSync(exe, "fake", { mode: 0o755 });
  const settingsFile = () => JSON.parse(fs.readFileSync(path.join(userData, "settings.json"), "utf8")) as { odaConverter: string | null };
  type Oda = { configured: string | null; found: boolean; path: string | null; auto: string | null };
  const core = (page: Page) =>
    page.evaluate(async () => {
      const c = window.bf!.core()!;
      return (await (await fetch(`${c.baseUrl}/api/v1/tools/oda`, { headers: { "X-BF-Token": c.token } })).json()) as Oda;
    });
  const openTab = async (page: Page) => {
    await page.keyboard.press("Control+KeyK");
    await page.getByLabel("Поиск команды").fill("настройка");
    await page.keyboard.press("Enter");
    const settings = page.getByRole("dialog", { name: "Настройка" });
    await settings.getByRole("tab", { name: "Внешние программы" }).click();
    return settings;
  };

  let { app, page } = await launch({}, userData);
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    const installed = (await core(page)).auto !== null; // a developer's PC may have the real converter
    const settings = await openTab(page);
    const field = settings.getByLabel("Путь к ODA File Converter");
    const state = settings.getByTestId("oda-status");
    await expect(field).toHaveValue("");
    await expect(state).toHaveAttribute("data-found", String(installed));
    if (!installed) {
      await expect(state).toHaveText("ODA File Converter не найден.");
      await expect(settings).toContainText("бесплатная программа Open Design Alliance");
      await expect(settings).toContainText("сохраните чертёж в DXF");
    }
    expect(await settings.locator("a").count()).toBe(0); // texts only, no links (ТЗ 17.4.1)

    // «Выбрать…»: the file dialog of the system, programs first on Windows
    await app.evaluate(({ dialog }, f) => {
      dialog.showOpenDialog = (async (_w: unknown, o: { filters?: { extensions: string[] }[] }) => {
        (globalThis as { bfOdaFilters?: string[][] }).bfOdaFilters = (o.filters ?? []).map((x) => x.extensions);
        return { canceled: false, filePaths: [f] };
      }) as unknown as typeof dialog.showOpenDialog;
    }, exe);
    await settings.getByRole("button", { name: "Выбрать…" }).click();
    await expect(state).toHaveAttribute("data-found", "true");
    await expect(state).toContainText("ODA File Converter найден, версия 26.10.0.");
    await expect(state).toContainText(`Программа: ${exe}`);
    await expect(field).toHaveValue(exe);
    const filters = await app.evaluate(() => (globalThis as { bfOdaFilters?: string[][] }).bfOdaFilters);
    expect(filters).toEqual(process.platform === "win32" ? [["exe"], ["*"]] : [["*"]]);
    expect(await core(page)).toMatchObject({ configured: exe, found: true, path: exe });

    // a typed path is not saved until it is checked; one that is not a full path is refused and changes nothing
    await field.fill("ODAFileConverter");
    await expect(settings.getByText("Путь изменён — нажмите «Проверить», чтобы запомнить его.")).toBeVisible();
    await settings.getByRole("button", { name: "Проверить" }).click();
    await expect(state).toContainText("Путь к ODA File Converter указан неверно.");
    expect((await core(page)).configured).toBe(exe);

    // the folder of the converter was updated away: said so; nothing else on the PC — not found
    const gone = path.join(userData, "ODAFileConverter 25.1.0");
    await field.fill(gone);
    await field.press("Enter");
    await expect(field).toHaveValue(gone);
    await expect(state).toContainText(installed ? "По указанному пути программы нет. Будет использована найденная" : "ODA File Converter не найден: по указанному пути программы нет.");

    // the folder of the program is enough
    await field.fill(`"${folder}"`); // «Копировать как путь» of Explorer
    await field.press("Enter");
    await expect(state).toContainText(`Программа: ${exe}`);
    await expect(field).toHaveValue(folder);
    await page.screenshot({ path: shot("settings-tools.png") });
    await page.waitForTimeout(700); // debounced settings write
    expect(settingsFile().odaConverter).toBe(folder);
  } finally {
    await close(app);
  }

  // a new core knows nothing: the interface sends the path of the settings when it comes up
  ({ app, page } = await launch({}, userData));
  try {
    await expect(page.getByTestId("core-status")).toContainText("Ядро работает", { timeout: 60_000 });
    await expect.poll(async () => (await core(page)).configured, { timeout: 30_000 }).toBe(folder);
    expect(await core(page)).toMatchObject({ found: true, path: exe });
    const settings = await openTab(page);
    await expect(settings.getByLabel("Путь к ODA File Converter")).toHaveValue(folder);
    await expect(settings.getByTestId("oda-status")).toContainText("версия 26.10.0");
    await settings.getByRole("button", { name: "Очистить" }).click();
    await expect(settings.getByLabel("Путь к ODA File Converter")).toHaveValue("");
    await expect(settings.getByRole("button", { name: "Очистить" })).toHaveCount(0);
    expect((await core(page)).configured).toBeNull();
    await page.waitForTimeout(700);
    expect(settingsFile().odaConverter).toBeNull();
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
