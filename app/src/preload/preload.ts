// Bridge between the sandboxed interface and the main process (contextIsolation).
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bf", {
  core: () => ipcRenderer.sendSync("bf:core-info"),
  appVersion: () => ipcRenderer.sendSync("bf:app-version"),
  spike: () => ipcRenderer.sendSync("bf:spike"),
  uiReady: () => ipcRenderer.send("bf:ui-ready"),
  loadSettings: () => ipcRenderer.sendSync("bf:settings-load"),
  saveSettings: (data: unknown) => ipcRenderer.send("bf:settings-save", data),
  copyText: (text: string) => ipcRenderer.send("bf:copy-text", String(text)),
  saveText: (defaultName: string, text: string) => ipcRenderer.invoke("bf:save-text", String(defaultName), String(text)),
  coreRetry: () => ipcRenderer.send("bf:core-retry"),
  coreLog: () => ipcRenderer.sendSync("bf:core-log"),
  onCoreState: (cb: (state: unknown) => void) => {
    ipcRenderer.on("bf:core-state", (_e, state) => cb(state));
  },
});
