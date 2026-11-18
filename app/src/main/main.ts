// BedrockForge 2 main process: splash, bf-core lifecycle, app:// protocol, one main window.
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, protocol, screen, session, shell, type WebContents } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { CoreProcess, type CoreInfo } from "./core";
import { coreCommand, uiRoot } from "./paths";
import { contentSecurityPolicy, isAllowedExternal, mimeOf, ORIGIN, staticFile } from "./protocol";
import { mergeSettings, readSettings, writeSettings } from "./userSettings";

const TITLE = "BedrockForge by Mikhail Bandurkin";
const UI_READY_TIMEOUT_MS = 30_000;

protocol.registerSchemesAsPrivileged([
  { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true, codeCache: true } },
]);

// «Программная отрисовка (медленно)» of the 3D diagnostic window relaunches with this switch (ADR 0004 §4).
const SOFTWARE_GL = process.env.BF_SOFTWARE_GL === "1" || process.argv.includes("--bf-software-gl");
if (SOFTWARE_GL) {
  // Software WebGL2 (SwiftShader): CI without GPU and a user fallback for broken drivers.
  app.commandLine.appendSwitch("use-gl", "angle");
  app.commandLine.appendSwitch("use-angle", "swiftshader");
  app.commandLine.appendSwitch("enable-unsafe-swiftshader");
  app.commandLine.appendSwitch("ignore-gpu-blocklist");
} else {
  app.commandLine.appendSwitch("force_high_performance_gpu");
}
app.setAppUserModelId("com.bandurkin.bedrockforge2");

function localDir(): string {
  if (process.env.BF_LOCAL_DIR) return process.env.BF_LOCAL_DIR;
  if (process.platform === "win32") return path.join(process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local"), "BedrockForge");
  return path.join(process.env.XDG_CACHE_HOME ?? path.join(os.homedir(), ".cache"), "BedrockForge");
}
const LOG_DIR = path.join(localDir(), "logs");
const SHELL_ROOT = path.join(__dirname, "..", "static");
// Brand (docs/brand/README.md): Anthracite background, the cube icon of the app.
const BRAND_BG = "#1b222b";
const APP_ICON = path.join(SHELL_ROOT, "icon.png");
const UI_ROOT = uiRoot(app.isPackaged, process.resourcesPath, path.join(__dirname, ".."));
if (process.env.BF_USER_DATA) app.setPath("userData", process.env.BF_USER_DATA);
const SETTINGS_FILE = () => path.join(app.getPath("userData"), "settings.json");

interface WindowPlacement {
  x?: number;
  y?: number;
  width: number;
  height: number;
  maximized: boolean;
}

function savedPlacement(): WindowPlacement | null {
  const s = readSettings(SETTINGS_FILE());
  const w = s && typeof s === "object" ? (s as { window?: Partial<WindowPlacement> }).window : undefined;
  if (!w || typeof w.width !== "number" || typeof w.height !== "number") return null;
  const b = { x: w.x ?? 0, y: w.y ?? 0, width: Math.max(1024, w.width), height: Math.max(600, w.height) };
  const area = screen.getDisplayMatching(b).workArea;
  const visible = b.x < area.x + area.width - 50 && b.x + b.width > area.x + 50 && b.y >= area.y - 10 && b.y < area.y + area.height - 50;
  return visible ? { ...b, maximized: Boolean(w.maximized) } : { width: b.width, height: b.height, maximized: Boolean(w.maximized) };
}

function log(line: string): void {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(path.join(LOG_DIR, "shell.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* never break on logging */
  }
}

/** Документы\BedrockForge\Проекты unless «Настройка» chose another folder (ТЗ 6.5). */
function projectsDir(): string {
  const s = readSettings(SETTINGS_FILE());
  const chosen = s && typeof s === "object" ? (s as Record<string, unknown>).projectsDir : undefined;
  if (typeof chosen === "string" && path.isAbsolute(chosen)) return chosen;
  return process.env.BF_PROJECTS_DIR || path.join(app.getPath("documents"), "BedrockForge", "Проекты");
}

const core = new CoreProcess({
  command: coreCommand(app.isPackaged, process.resourcesPath, path.join(__dirname, "..")),
  dataDir: app.getPath("userData"),
  projectsDir,
  logFile: path.join(LOG_DIR, "core.log"),
});

let splash: BrowserWindow | null = null;
let quitting = false;
let mainWin: BrowserWindow | null = null;

async function serveStatic(root: string, pathname: string): Promise<Response> {
  const file = staticFile(root, pathname);
  if (!file) return new Response("Forbidden", { status: 403 });
  let data: Buffer;
  try {
    data = await fs.promises.readFile(file);
  } catch {
    return new Response("Not found", { status: 404 });
  }
  const headers: Record<string, string> = { "content-type": mimeOf(file), "x-content-type-options": "nosniff" };
  if (file.endsWith(".html")) {
    headers["content-security-policy"] = contentSecurityPolicy(data.toString("utf8"), core.current()?.port ?? null);
    headers["cache-control"] = "no-cache";
  }
  return new Response(new Uint8Array(data), { headers });
}

function registerProtocol(): void {
  protocol.handle("app", (request) => {
    const url = new URL(request.url);
    if (url.host !== "bedrockforge") return new Response("Not found", { status: 404 });
    if (url.pathname.startsWith("/__shell/")) return serveStatic(SHELL_ROOT, url.pathname.slice("/__shell".length));
    return serveStatic(UI_ROOT, url.pathname);
  });
}

const securePrefs = (preload: string) => ({
  preload: path.join(__dirname, preload),
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webSecurity: true,
  spellcheck: false,
});

function lockDown(wc: WebContents): void {
  wc.on("will-navigate", (e, url) => {
    if (url.startsWith(ORIGIN + "/")) return;
    e.preventDefault();
    if (isAllowedExternal(url)) void shell.openExternal(url);
  });
  wc.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternal(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-attach-webview", (e) => e.preventDefault());
}

function splashState(text: string, error = false): void {
  if (splash && !splash.isDestroyed()) splash.webContents.send("bf:splash-state", { text, error });
}

function createSplash(): void {
  splash = new BrowserWindow({
    width: 560, height: 340, frame: false, resizable: false, center: true, show: false,
    title: TITLE, backgroundColor: BRAND_BG, icon: APP_ICON, webPreferences: securePrefs("splash-preload.js"),
  });
  lockDown(splash.webContents);
  splash.once("ready-to-show", () => splash?.show());
  void splash.loadURL(`${ORIGIN}/__shell/splash.html`);
}

function createMain(): void {
  const placed = savedPlacement();
  mainWin = new BrowserWindow({
    width: placed?.width ?? 1440, height: placed?.height ?? 900, x: placed?.x, y: placed?.y, minWidth: 1024, minHeight: 600, show: false,
    title: TITLE, backgroundColor: BRAND_BG, icon: APP_ICON, webPreferences: securePrefs("preload.js"),
  });
  // ТЗ 7.0.14: the minimum is 1024×600 of the page itself; on Windows the frame takes ~16 px of the outer size.
  const [ow, oh] = mainWin.getSize();
  const [cw, ch] = mainWin.getContentSize();
  mainWin.setMinimumSize(1024 + Math.max(0, ow - cw), 600 + Math.max(0, oh - ch));
  if (placed?.x === undefined) mainWin.center();
  const remember = () => {
    if (!mainWin || mainWin.isDestroyed() || mainWin.isMinimized() || mainWin.isFullScreen()) return;
    const maximized = mainWin.isMaximized();
    const b = maximized ? (savedPlacement() ?? mainWin.getNormalBounds()) : mainWin.getBounds();
    mergeSettings(SETTINGS_FILE(), { window: { x: b.x, y: b.y, width: b.width, height: b.height, maximized } });
  };
  mainWin.on("close", remember);
  mainWin.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown" || !mainWin) return;
    if (input.key === "F11") {
      mainWin.setFullScreen(!mainWin.isFullScreen());
      e.preventDefault();
    } else if (input.key === "F5") {
      mainWin.webContents.reload();
      e.preventDefault();
    } else if (input.control && input.shift && input.code === "KeyI") {
      mainWin.webContents.toggleDevTools();
      e.preventDefault();
    }
  });
  lockDown(mainWin.webContents);
  mainWin.on("page-title-updated", (e) => e.preventDefault());
  mainWin.on("closed", () => {
    mainWin = null;
    app.quit();
  });
  mainWin.webContents.on("render-process-gone", (_e, d) => log(`renderer gone: ${d.reason}`));

  // One splash only: the window is shown when the interface reports it has drawn (bf:ui-ready).
  let shown = false;
  const reveal = (why: string) => {
    if (shown || !mainWin) return;
    shown = true;
    clearTimeout(fallback);
    if (why !== "ready") log(`window shown by ${why}`);
    if (placed?.maximized) mainWin.maximize();
    mainWin.show();
    mainWin.focus();
    if (splash && !splash.isDestroyed()) splash.destroy();
  };
  ipcMain.on("bf:ui-ready", (e) => {
    if (mainWin && e.sender === mainWin.webContents) reveal("ready");
  });
  const fallback = setTimeout(() => reveal("timeout"), UI_READY_TIMEOUT_MS);
  mainWin.webContents.once("did-fail-load", () => reveal("load failure"));
  void mainWin.loadURL(`${ORIGIN}/index.html`);
}

/** Last `max` bytes of a log file without reading it whole. */
function tailFile(file: string, max = 200_000): string {
  let fd: number | null = null;
  try {
    fd = fs.openSync(file, "r");
    const size = fs.fstatSync(fd).size;
    const len = Math.min(size, max);
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, size - len);
    return buf.toString("utf8");
  } catch {
    return "Журнал ядра пуст";
  } finally {
    if (fd !== null) fs.closeSync(fd);
  }
}

function publicInfo(info: CoreInfo | null) {
  return info ? { baseUrl: info.baseUrl, token: info.token, version: info.version } : null;
}

function registerIpc(): void {
  ipcMain.on("bf:splash-close", (e) => {
    if (splash && e.sender === splash.webContents) app.quit();
  });
  ipcMain.on("bf:splash-copy-log", (e) => {
    if (!splash || e.sender !== splash.webContents) return;
    try {
      clipboard.writeText(tailFile(core.logPath()));
    } catch {
      clipboard.writeText("Журнал ядра пуст");
    }
  });
  ipcMain.on("bf:core-retry", (e) => {
    if (mainWin && e.sender === mainWin.webContents && !quitting) void core.restart(true);
  });
  ipcMain.on("bf:core-log", (e) => {
    e.returnValue = mainWin && e.sender === mainWin.webContents ? tailFile(core.logPath()) : "";
  });
  ipcMain.on("bf:core-info", (e) => {
    // The launch token goes to the main window of our own origin only.
    const ownWindow = mainWin !== null && e.sender === mainWin.webContents && (e.senderFrame?.url ?? "").startsWith(ORIGIN + "/");
    const info: CoreInfo | null = ownWindow ? core.current() : null;
    e.returnValue = info ? { baseUrl: info.baseUrl, token: info.token, version: info.version } : null;
  });
  ipcMain.on("bf:app-version", (e) => (e.returnValue = app.getVersion()));
  const fromMain = (sender: WebContents) => mainWin !== null && sender === mainWin.webContents;
  ipcMain.on("bf:settings-load", (e) => {
    const s = fromMain(e.sender) ? readSettings(SETTINGS_FILE()) : null;
    // The window placement belongs to the main process; the interface gets the rest.
    if (s && typeof s === "object") {
      const { window: _w, ...rest } = s as Record<string, unknown>;
      e.returnValue = rest;
    } else e.returnValue = s;
  });
  ipcMain.on("bf:settings-save", (e, data: unknown) => {
    if (!fromMain(e.sender) || !data || typeof data !== "object" || Array.isArray(data)) return;
    const cur = readSettings(SETTINGS_FILE());
    if (cur === "damaged") return; // do not overwrite a file the user may want to recover
    const win = cur && typeof cur === "object" ? (cur as Record<string, unknown>).window : undefined;
    writeSettings(SETTINGS_FILE(), { ...(data as Record<string, unknown>), ...(win ? { window: win } : {}) });
  });
  ipcMain.on("bf:copy-text", (e, text: string) => {
    if (fromMain(e.sender) && typeof text === "string" && text.length < 50_000_000) clipboard.writeText(text);
  });
  /** A file appears whole or not at all: written next to it, then renamed over it (a reader never sees half). */
  const writeWhole = async (file: string, data: string | Uint8Array): Promise<void> => {
    const part = `${file}.${process.pid}.part`;
    try {
      await fs.promises.writeFile(part, data, typeof data === "string" ? "utf8" : undefined);
      await fs.promises.rename(part, file);
    } catch (err) {
      await fs.promises.rm(part, { force: true }).catch(() => undefined);
      throw err;
    }
  };
  ipcMain.handle("bf:save-text", async (e, name: string, text: string) => {
    if (!mainWin || !fromMain(e.sender)) return false;
    const r = await dialog.showSaveDialog(mainWin, { defaultPath: path.basename(String(name)), filters: [{ name: "Текст", extensions: ["txt"] }] });
    if (r.canceled || !r.filePath) return false;
    await writeWhole(r.filePath, "\uFEFF" + String(text));
    return true;
  });
  // Exports of the interface (ADR 0007 §6): a chart as PNG/SVG, its data as CSV, styles as .bfstyle.json.
  const fileKinds: Record<string, { filters: Electron.FileFilter[]; bom: boolean }> = {
    png: { filters: [{ name: "Изображение PNG", extensions: ["png"] }], bom: false },
    svg: { filters: [{ name: "Рисунок SVG", extensions: ["svg"] }], bom: false },
    csv: { filters: [{ name: "CSV", extensions: ["csv"] }], bom: true },
    bfstyle: { filters: [{ name: "Стили BedrockForge", extensions: ["bfstyle.json", "json"] }], bom: false },
  };
  const kindOf = (kind: string) => (Object.hasOwn(fileKinds, kind) ? fileKinds[kind] : undefined);
  /** A file the system refuses: the reason in Russian (ТЗ 17), not the English text of Node. */
  const fileError = (err: unknown): string => {
    const code = (err as NodeJS.ErrnoException)?.code;
    if (code === "EBUSY" || code === "EPERM") return "Файл занят другой программой — закройте его и повторите.";
    if (code === "EACCES") return "Нет прав на запись в эту папку — выберите другую.";
    if (code === "ENOSPC") return "На диске нет места.";
    if (code === "ENOENT") return "Файл или папка не найдены.";
    return "Файл не записан или не прочитан: ошибка файловой системы.";
  };
  ipcMain.handle("bf:save-file", async (e, name: string, kind: string, data: string | Uint8Array) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const k = kindOf(String(kind));
    if (!k || (typeof data !== "string" && !(data instanceof Uint8Array))) return null;
    if ((typeof data === "string" ? data.length * 3 : data.byteLength) > 512 * 1024 * 1024) return null;
    const r = await dialog.showSaveDialog(mainWin, { defaultPath: path.join(app.getPath("documents"), path.basename(String(name))), filters: k.filters });
    if (r.canceled || !r.filePath) return null;
    try {
      await writeWhole(r.filePath, typeof data === "string" ? (k.bom ? "\uFEFF" : "") + data : data);
    } catch (err) {
      return { error: fileError(err) };
    }
    return r.filePath;
  });
  ipcMain.handle("bf:open-text", async (e, kind: string) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const k = kindOf(String(kind));
    if (!k || kind === "png") return null;
    const r = await dialog.showOpenDialog(mainWin, { properties: ["openFile"], filters: k.filters });
    if (r.canceled || !r.filePaths.length) return null;
    const file = r.filePaths[0];
    try {
      const st = await fs.promises.stat(file);
      if (st.size > 8 * 1024 * 1024) return { name: path.basename(file), text: null, error: "Файл больше 8 МБ" };
      return { name: path.basename(file), text: (await fs.promises.readFile(file, "utf8")).replace(/^\uFEFF/, "") };
    } catch (err) {
      return { name: path.basename(file), text: null, error: fileError(err) };
    }
  });
  // Flags of the interface: the E2E hook of the 3D window (BF_E2E=1) and software WebGL.
  ipcMain.on("bf:flags", (e) => (e.returnValue = { e2e: process.env.BF_E2E === "1", softwareGl: SOFTWARE_GL }));
  ipcMain.on("bf:relaunch-software-gl", (e) => {
    if (!fromMain(e.sender) || SOFTWARE_GL) return;
    log("relaunch with software WebGL (asked by the 3D diagnostic window)");
    app.relaunch({ args: [...process.argv.slice(1), "--bf-software-gl"] });
    app.quit();
  });
  // System file dialogs for .bfggis (ТЗ 7.4): the path goes to the core, which reads and writes the file itself.
  const bfggis = [{ name: "Проект BedrockForge", extensions: ["bfggis"] }];
  // Table export (ТЗ 9.12): DAT, CSV, XLSX — the filter follows the chosen format.
  const tableFilters: Record<string, Electron.FileFilter[]> = {
    dat: [{ name: "Micromine DAT", extensions: ["dat"] }],
    csv: [{ name: "CSV", extensions: ["csv"] }],
    xlsx: [{ name: "Excel", extensions: ["xlsx"] }],
  };
  ipcMain.handle("bf:pick-save", async (e, defaultName: string, kind?: string) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const filters = (kind && tableFilters[String(kind)]) || bfggis;
    const r = await dialog.showSaveDialog(mainWin, { defaultPath: path.join(app.getPath("documents"), path.basename(String(defaultName))), filters });
    return r.canceled || !r.filePath ? null : r.filePath;
  });
  ipcMain.handle("bf:pick-open", async (e) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const r = await dialog.showOpenDialog(mainWin, { properties: ["openFile"], filters: bfggis });
    return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
  });
  // Table files of the import wizards (ТЗ 7.7) and .prj of a coordinate system: several files at once.
  const lineFiles = ["str", "pit", "dxf", "shp", "dbf", "shx", "prj", "cpg", "mif", "mid", "xyz"];
  const rasterFiles = ["jpg", "jpeg", "png", "tif", "tiff", "bmp", "jgw", "jpgw", "pgw", "pngw", "tfw", "tifw", "bpw", "bmpw", "wld", "tab", "grf", "prj"];
  const fileFilters: Record<string, Electron.FileFilter[]> = {
    tables: [
      { name: "Таблицы скважин", extensions: ["dat", "csv", "txt", "tsv", "prn", "xlsx", "xlsm", "xls"] },
      { name: "Все файлы", extensions: ["*"] },
    ],
    prj: [{ name: "Система координат (.prj)", extensions: ["prj"] }],
    // «Импорт файлов» (ТЗ 7.8): every extension of the wizard; formats arrive with their stages.
    objects: [
      {
        name: "Все поддерживаемые",
        extensions: [...new Set(["omf", "tridb", "obj", "stl", ...lineFiles, ...rasterFiles, "dat", "csv", "txt", "xlsx", "xlsm", "xls", "bfggis"])],
      },
      { name: "Все файлы", extensions: ["*"] },
    ],
    // «Линии», «Растры», «Канавы» (ТЗ 7.18, 7.19, 7.35 Д-16; ADR 0011 §5): a main file with its companions.
    lines: [
      { name: "Линии и точки", extensions: [...lineFiles, "csv", "txt", "tsv", "prn", "dat"] },
      { name: "Все файлы", extensions: ["*"] },
    ],
    rasters: [
      { name: "Растры и файлы привязки", extensions: rasterFiles },
      { name: "Все файлы", extensions: ["*"] },
    ],
    trenches: [
      { name: "Линии канав", extensions: ["str", "pit", "dxf", "csv", "txt", "tsv", "prn", "dat", "xyz"] },
      { name: "Все файлы", extensions: ["*"] },
    ],
  };
  ipcMain.handle("bf:pick-files", async (e, kind: string, multi: boolean) => {
    if (!mainWin || !fromMain(e.sender)) return [];
    const r = await dialog.showOpenDialog(mainWin, {
      properties: multi ? ["openFile", "multiSelections"] : ["openFile"],
      filters: fileFilters[kind] ?? fileFilters.tables,
    });
    return r.canceled ? [] : r.filePaths;
  });
  ipcMain.handle("bf:pick-folder", async (e) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const r = await dialog.showOpenDialog(mainWin, { properties: ["openDirectory", "createDirectory"] });
    return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
  });
  ipcMain.on("bf:show-in-folder", (e, file: string) => {
    if (fromMain(e.sender) && typeof file === "string" && path.isAbsolute(file)) shell.showItemInFolder(file);
  });
}

async function boot(): Promise<void> {
  Menu.setApplicationMenu(null);
  session.defaultSession.setPermissionRequestHandler((_wc, permission, done) =>
    done(["clipboard-read", "clipboard-sanitized-write", "fullscreen"].includes(permission)),
  );
  registerProtocol();
  registerIpc();
  createSplash();
  log(`start ${app.getVersion()} ui=${UI_ROOT}`);
  try {
    await core.start();
  } catch (e) {
    log(`core failed: ${String(e)}`);
    splashState(`Вычислительное ядро не запускается: ${e instanceof Error ? e.message : String(e)}. Журнал: ${LOG_DIR}`, true);
    return;
  }
  core.on("state", (status: "online" | "offline") => {
    mainWin?.webContents.send("bf:core-state", { status, info: status === "online" ? publicInfo(core.current()) : null });
  });
  core.on("restarted", (info: CoreInfo, portChanged: boolean) => {
    log(`core restarted on port ${info.port}${portChanged ? " (new port: reloading the window)" : ""}`);
    if (portChanged) mainWin?.webContents.reload(); // CSP of the page names the old port
    else mainWin?.webContents.send("bf:core-state", { status: "online", info: publicInfo(info), message: "Вычислительное ядро перезапущено" });
  });
  core.on("failed", (e: unknown) => {
    log(`core failed permanently: ${String(e)}`);
    mainWin?.webContents.send("bf:core-state", { status: "offline", info: null, message: String(e) });
  });
  createMain();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = mainWin ?? splash;
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (e) => {
    if (quitting) return;
    e.preventDefault();
    quitting = true;
    void core.stop().finally(() => app.exit(0));
  });
  void app.whenReady().then(boot);
}
