// BedrockForge desktop client (docs/DESKTOP.md, part 1): the built web/ interface in an Electron window.
// The window loads app://bedrockforge/; the main process serves the static files and forwards /api/* and /mcp
// to the server running in Docker (http://localhost:8000 by default).
"use strict";

const { app, BrowserWindow, protocol, ipcMain, shell, screen, session, Menu } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");
const { Readable } = require("node:stream");
const { isServerPath, staticFile, mimeOf, normalizeServer, contentSecurityPolicy, forwardHeaders, isAllowedExternal } = require("./proxy");

const TITLE = "BedrockForge by Mikhail Bandurkin";
const ORIGIN = "app://bedrockforge";
const DEFAULT_SERVER = "http://localhost:8000";
const RETRY_MS = 3000;
const MIN_SPLASH_MS = 1500;
const UI_READY_TIMEOUT_MS = 30000;

protocol.registerSchemesAsPrivileged([
  { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true, codeCache: true } },
]);

// Laptops with two GPUs: ask for the discrete one (docs/DESKTOP.md, P3).
app.commandLine.appendSwitch("force_high_performance_gpu");
app.setAppUserModelId("com.bandurkin.bedrockforge");

const WEB_ROOT = app.isPackaged ? path.join(process.resourcesPath, "web") : path.join(__dirname, "..", "..", "web", "dist");
const UI_ROOT = path.join(__dirname, "ui");
const ICON = path.join(__dirname, "ui", "icon.png");

// ---------- settings and log ----------

const settingsFile = () => path.join(app.getPath("userData"), "settings.json");
let settings = { server: DEFAULT_SERVER, bounds: null, maximized: false };
/** BF_SERVER applies to this run only and is not written to settings.json. */
let savedServer = DEFAULT_SERVER;

function loadSettings() {
  try {
    settings = { ...settings, ...JSON.parse(fs.readFileSync(settingsFile(), "utf8")) };
  } catch {
    /* first start */
  }
  settings.server = normalizeServer(settings.server) ?? DEFAULT_SERVER;
  savedServer = settings.server;
  const env = normalizeServer(process.env.BF_SERVER);
  if (env) settings.server = env;
}

function saveSettings() {
  try {
    fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
    fs.writeFileSync(settingsFile(), JSON.stringify({ ...settings, server: savedServer }, null, 2));
  } catch (e) {
    log(`settings not saved: ${e.message}`);
  }
}

function log(line) {
  try {
    const dir = path.join(app.getPath("userData"), "logs");
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, "desktop.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* logging must never break the app */
  }
}

// ---------- app:// protocol ----------

function errorResponse(status, detail) {
  return new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

async function serveStatic(root, pathname) {
  const file = staticFile(root, pathname);
  if (!file) return new Response("Forbidden", { status: 403 });
  let data;
  try {
    data = await fs.promises.readFile(file);
  } catch {
    return new Response("Not found", { status: 404 });
  }
  const headers = { "content-type": mimeOf(file), "x-content-type-options": "nosniff" };
  if (file.endsWith(".html")) {
    headers["content-security-policy"] = contentSecurityPolicy(data.toString("utf8"));
    headers["cache-control"] = "no-cache";
  }
  return new Response(data, { headers });
}

/** Forwards one request to the configured server only; bodies stream both ways (uploads, job events). */
function forward(request, url) {
  const target = new URL(url.pathname + url.search, settings.server);
  const client = target.protocol === "https:" ? https : http;
  return new Promise((resolve) => {
    const req = client.request(target, { method: request.method, headers: forwardHeaders(request.headers.entries()) }, (res) => {
      const headers = new Headers();
      for (const [k, v] of Object.entries(forwardHeaders(Object.entries(res.headers)))) {
        if (Array.isArray(v)) v.forEach((x) => headers.append(k, x));
        else if (v !== undefined) headers.set(k, String(v));
      }
      const empty = request.method === "HEAD" || res.statusCode === 204 || res.statusCode === 304;
      if (empty) res.resume();
      const status = res.statusCode >= 200 ? res.statusCode : 502;
      resolve(new Response(empty ? null : Readable.toWeb(res), { status, statusText: res.statusMessage, headers }));
    });
    req.on("error", (e) => {
      log(`forward ${request.method} ${url.pathname}: ${e.message}`);
      resolve(errorResponse(502, `Сервер BedrockForge недоступен (${settings.server}): ${e.message}. Проверьте, что Docker запущен.`));
    });
    if (request.body) {
      const body = Readable.fromWeb(request.body);
      body.on("error", (e) => req.destroy(e));
      body.pipe(req);
    } else {
      req.end();
    }
  });
}

function registerProtocol() {
  protocol.handle("app", (request) => {
    const url = new URL(request.url);
    if (url.host !== "bedrockforge") return new Response("Not found", { status: 404 });
    if (isServerPath(url.pathname)) return forward(request, url);
    if (url.pathname.startsWith("/__desktop/")) return serveStatic(UI_ROOT, url.pathname.slice("/__desktop".length));
    return serveStatic(WEB_ROOT, url.pathname);
  });
}

// ---------- server check ----------

function checkServer() {
  return new Promise((resolve) => {
    const target = new URL("/api/health", settings.server);
    const client = target.protocol === "https:" ? https : http;
    const req = client.get(target, { timeout: 2500 }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (text += c));
      res.on("end", () => {
        try {
          const body = JSON.parse(text);
          if (res.statusCode === 200 && body.status === "ok") return resolve({ ok: true, version: body.version ?? null });
        } catch {
          /* not BedrockForge */
        }
        resolve({ ok: false, error: `ответ ${res.statusCode}: это не сервер BedrockForge` });
      });
    });
    req.on("timeout", () => req.destroy(new Error("нет ответа за 2,5 с")));
    req.on("error", (e) => resolve({ ok: false, error: e.code === "ECONNREFUSED" ? "соединение отклонено (Docker не запущен?)" : e.message }));
  });
}

// ---------- windows ----------

let splash = null;
let main = null;
let retryTimer = null;
let splashShownAt = 0;

const secureWebPreferences = (preload) => ({
  preload: path.join(__dirname, preload),
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webSecurity: true,
  spellcheck: false,
});

/** No navigation away from the app; allowed https links open in the system browser. */
function lockDown(win) {
  win.webContents.on("will-navigate", (e, url) => {
    if (url.startsWith(ORIGIN + "/")) return;
    e.preventDefault();
    if (isAllowedExternal(url)) void shell.openExternal(url);
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternal(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-attach-webview", (e) => e.preventDefault());
}

function sendSplash(state) {
  if (splash && !splash.isDestroyed()) splash.webContents.send("bf:splash-state", state);
}

function createSplash() {
  splash = new BrowserWindow({
    width: 560,
    height: 340,
    frame: false,
    resizable: false,
    movable: true,
    center: true,
    show: false,
    title: TITLE,
    icon: ICON,
    backgroundColor: "#15181d",
    webPreferences: secureWebPreferences("splash-preload.js"),
  });
  lockDown(splash);
  splash.once("ready-to-show", () => {
    splash.show();
    splashShownAt = Date.now();
  });
  splash.on("closed", () => {
    splash = null;
    if (!main) app.quit();
  });
  void splash.loadURL(`${ORIGIN}/__desktop/splash.html`);
}

function visibleBounds(b) {
  if (!b) return null;
  const area = screen.getDisplayMatching(b).workArea;
  const overlaps = b.x < area.x + area.width - 50 && b.x + b.width > area.x + 50 && b.y >= area.y - 10 && b.y < area.y + area.height - 50;
  return overlaps ? b : null;
}

function handleKeys(win) {
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F11") {
      win.setFullScreen(!win.isFullScreen());
      e.preventDefault();
    } else if (input.key === "F5") {
      if (input.control) win.webContents.reloadIgnoringCache();
      else win.webContents.reload();
      e.preventDefault();
    } else if (input.control && input.shift && input.key.toLowerCase() === "i") {
      win.webContents.toggleDevTools();
      e.preventDefault();
    }
  });
}

function createMain(warning) {
  const bounds = visibleBounds(settings.bounds) ?? { width: 1440, height: 900 };
  main = new BrowserWindow({
    ...bounds,
    minWidth: 960,
    minHeight: 600,
    show: false,
    title: TITLE,
    icon: ICON,
    backgroundColor: "#15181d",
    webPreferences: secureWebPreferences("preload.js"),
  });
  if (!settings.bounds) main.center();
  lockDown(main);
  handleKeys(main);
  main.on("page-title-updated", (e) => e.preventDefault());

  const remember = () => {
    if (!main || main.isDestroyed()) return;
    settings.maximized = main.isMaximized();
    if (!settings.maximized && !main.isMinimized() && !main.isFullScreen()) settings.bounds = main.getBounds();
  };
  main.on("resize", remember);
  main.on("move", remember);
  main.on("close", () => {
    remember();
    saveSettings();
  });
  main.on("closed", () => {
    main = null;
    app.quit();
  });
  main.webContents.on("render-process-gone", (_e, d) => log(`renderer gone: ${d.reason}`));

  // The window is shown when the interface reports it has drawn the workspace (preload.js, "bf:ui-ready"),
  // so the only splash the user sees is this one; a fallback shows it anyway if that never comes.
  let shown = false;
  const reveal = (why) => {
    if (shown || !main) return;
    shown = true;
    clearTimeout(fallback);
    ipcMain.removeListener("bf:ui-ready", onReady);
    if (why !== "ready") log(`window shown by ${why}`);
    const wait = Math.max(0, MIN_SPLASH_MS - (Date.now() - splashShownAt)) + (warning ? 2500 : 0);
    setTimeout(() => {
      if (!main) return;
      if (settings.maximized) main.maximize();
      main.show();
      main.focus();
      if (splash && !splash.isDestroyed()) splash.destroy();
    }, wait);
  };
  const onReady = (e) => {
    if (main && e.sender === main.webContents) reveal("ready");
  };
  ipcMain.on("bf:ui-ready", onReady);
  const fallback = setTimeout(() => reveal("timeout"), UI_READY_TIMEOUT_MS);
  main.webContents.once("did-fail-load", () => reveal("load failure"));
  void main.loadURL(`${ORIGIN}/index.html`);
}

async function connect() {
  clearTimeout(retryTimer);
  sendSplash({ phase: "connecting", server: settings.server, version: app.getVersion() });
  const r = await checkServer();
  if (main) return;
  if (!r.ok) {
    log(`server ${settings.server} unavailable: ${r.error}`);
    sendSplash({ phase: "unavailable", server: settings.server, error: r.error, retryMs: RETRY_MS, version: app.getVersion() });
    retryTimer = setTimeout(connect, RETRY_MS);
    return;
  }
  let warning = null;
  if (r.version && r.version !== app.getVersion()) {
    warning = `Версия сервера ${r.version}, клиента ${app.getVersion()}. Обновите отстающую часть (BedrockForge.bat обновляет сервер).`;
    log(`version mismatch: ${warning}`);
  }
  sendSplash({ phase: "starting", server: settings.server, warning, version: app.getVersion() });
  createMain(warning);
}

// ---------- IPC ----------

function registerIpc() {
  ipcMain.on("bf:info", (e) => {
    e.returnValue = { server: settings.server, version: app.getVersion(), title: TITLE };
  });
  ipcMain.handle("bf:open-external", (_e, url) => {
    if (!isAllowedExternal(url)) return false;
    void shell.openExternal(url);
    return true;
  });
  ipcMain.on("bf:splash-ready", () => void connect());
  ipcMain.on("bf:splash-retry", () => void connect());
  ipcMain.handle("bf:set-server", (_e, input) => {
    const server = normalizeServer(input);
    if (!server) return { ok: false, error: "Адрес вида http://localhost:8000" };
    settings.server = server;
    savedServer = server;
    saveSettings();
    void connect();
    return { ok: true, server };
  });
  ipcMain.on("bf:quit", () => app.quit());
}

// ---------- start ----------

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = main ?? splash;
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    loadSettings();
    log(`start ${app.getVersion()}, server ${settings.server}, web ${WEB_ROOT}`);
    // Only clipboard and full screen; camera, location, notifications and the rest are refused.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, done) =>
      done(["clipboard-read", "clipboard-sanitized-write", "fullscreen"].includes(permission)),
    );
    registerProtocol();
    registerIpc();
    createSplash();
  });

  app.on("window-all-closed", () => app.quit());
}
