// BedrockForge 2 main process: splash, bf-core lifecycle, app:// protocol, one main window.
import { app, BrowserWindow, ipcMain, Menu, protocol, session, shell, type WebContents } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { CoreProcess, type CoreInfo } from "./core";
import { coreCommand, uiRoot } from "./paths";
import { contentSecurityPolicy, isAllowedExternal, mimeOf, ORIGIN, staticFile } from "./protocol";

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

function log(line: string): void {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(path.join(LOG_DIR, "shell.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* never break on logging */
  }
}

const core = new CoreProcess({
  command: coreCommand(app.isPackaged, process.resourcesPath, path.join(__dirname, "..")),
  dataDir: app.getPath("userData"),
  logFile: path.join(LOG_DIR, "core.log"),
});

let splash: BrowserWindow | null = null;
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
  mainWin = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1024, minHeight: 600, show: false,
    title: TITLE, backgroundColor: "#15181d", webPreferences: securePrefs("preload.js"),
  });
  mainWin.center();
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

function registerIpc(): void {
  ipcMain.on("bf:core-info", (e) => {
    const info: CoreInfo | null = core.current();
    e.returnValue = info ? { baseUrl: info.baseUrl, token: info.token, version: info.version } : null;
  });
  ipcMain.on("bf:app-version", (e) => (e.returnValue = app.getVersion()));
  ipcMain.on("bf:spike", (e) => (e.returnValue = process.env.BF_SPIKE ?? null));
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
  core.on("restarted", (info: CoreInfo) => {
    log("core restarted");
    mainWin?.webContents.send("bf:core-restarted", { baseUrl: info.baseUrl, token: info.token, version: info.version, message: "Вычислительное ядро перезапущено" });
  });
  core.on("failed", (e: unknown) => log(`core failed permanently: ${String(e)}`));
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
  let quitting = false;
  app.on("before-quit", (e) => {
    if (quitting) return;
    e.preventDefault();
    quitting = true;
    void core.stop().finally(() => app.exit(0));
  });
  void app.whenReady().then(boot);
}
