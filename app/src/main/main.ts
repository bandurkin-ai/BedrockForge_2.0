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

if (process.env.BF_SOFTWARE_GL === "1") {
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
    title: TITLE, backgroundColor: "#15181d", webPreferences: securePrefs("splash-preload.js"),
  });
  lockDown(splash.webContents);
  splash.once("ready-to-show", () => splash?.show());
  void splash.loadURL(`${ORIGIN}/__shell/splash.html`);
}

function createMain(): void {
  const placed = savedPlacement();
  mainWin = new BrowserWindow({
    width: placed?.width ?? 1440, height: placed?.height ?? 900, x: placed?.x, y: placed?.y, minWidth: 1024, minHeight: 600, show: false,
    title: TITLE, backgroundColor: "#15181d", webPreferences: securePrefs("preload.js"),
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
  ipcMain.handle("bf:save-text", async (e, name: string, text: string) => {
    if (!mainWin || !fromMain(e.sender)) return false;
    const r = await dialog.showSaveDialog(mainWin, { defaultPath: path.basename(String(name)), filters: [{ name: "Текст", extensions: ["txt"] }] });
    if (r.canceled || !r.filePath) return false;
    await fs.promises.writeFile(r.filePath, "\uFEFF" + String(text), "utf8");
    return true;
  });
  ipcMain.on("bf:spike", (e) => (e.returnValue = process.env.BF_SPIKE ?? null));
  // System file dialogs for .bfggis (ТЗ 7.4): the path goes to the core, which reads and writes the file itself.
  const bfggis = [{ name: "Проект BedrockForge", extensions: ["bfggis"] }];
  ipcMain.handle("bf:pick-save", async (e, defaultName: string) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const r = await dialog.showSaveDialog(mainWin, { defaultPath: path.join(app.getPath("documents"), path.basename(String(defaultName))), filters: bfggis });
    return r.canceled || !r.filePath ? null : r.filePath;
  });
  ipcMain.handle("bf:pick-open", async (e) => {
    if (!mainWin || !fromMain(e.sender)) return null;
    const r = await dialog.showOpenDialog(mainWin, { properties: ["openFile"], filters: bfggis });
    return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
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
