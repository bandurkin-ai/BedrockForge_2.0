// Bridge between the sandboxed interface and the main process (contextIsolation).
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bf", {
  core: () => ipcRenderer.sendSync("bf:core-info"),
  appVersion: () => ipcRenderer.sendSync("bf:app-version"),
  spike: () => ipcRenderer.sendSync("bf:spike"),
  uiReady: () => ipcRenderer.send("bf:ui-ready"),
  onCoreRestarted: (cb: (info: unknown) => void) => {
    ipcRenderer.on("bf:core-restarted", (_e, info) => cb(info));
  },
});
