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
