"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bfSplash", {
  onState: (cb) => ipcRenderer.on("bf:splash-state", (_e, state) => cb(state)),
  ready: () => ipcRenderer.send("bf:splash-ready"),
  retry: () => ipcRenderer.send("bf:splash-retry"),
  setServer: (url) => ipcRenderer.invoke("bf:set-server", String(url)),
  quit: () => ipcRenderer.send("bf:quit"),
});
