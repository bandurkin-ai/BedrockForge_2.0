// The interface window gets only this small, read-mostly bridge (contextIsolation + sandbox).
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

const info = ipcRenderer.sendSync("bf:info");

contextBridge.exposeInMainWorld("bfDesktop", {
  /** server address the client forwards /api to, e.g. http://localhost:8000 */
  serverUrl: info.server,
  version: info.version,
  title: info.title,
  /** opens an allowed https address (Claude sign-in) in the system browser */
  openExternal: (url) => ipcRenderer.invoke("bf:open-external", String(url)),
});

// One splash only: the window stays hidden behind the Electron splash until the interface has drawn
// the workspace (its title bar), an error, or the startup error box; the web splash is never seen.
const READY = ".titlebar, .alert.error, #startup-error";
window.addEventListener("DOMContentLoaded", () => {
  const done = () => {
    observer.disconnect();
    ipcRenderer.send("bf:ui-ready");
  };
  const observer = new MutationObserver(() => {
    if (document.querySelector(READY)) done();
  });
  if (document.querySelector(READY)) return done();
  observer.observe(document.body, { childList: true, subtree: true });
});
