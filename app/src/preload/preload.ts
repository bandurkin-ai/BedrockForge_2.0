// Bridge between the sandboxed interface and the main process (contextIsolation).
import { contextBridge, ipcRenderer, webUtils } from "electron";

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
  pickSave: (defaultName: string) => ipcRenderer.invoke("bf:pick-save", String(defaultName)),
  pickOpen: () => ipcRenderer.invoke("bf:pick-open"),
  showInFolder: (file: string) => ipcRenderer.send("bf:show-in-folder", String(file)),
  pathForFile: (file: File) => webUtils.getPathForFile(file),
  coreLog: () => ipcRenderer.sendSync("bf:core-log"),
  onCoreState: (cb: (state: unknown) => void) => {
    const listener = (_e: unknown, state: unknown) => cb(state);
    ipcRenderer.on("bf:core-state", listener);
    return () => ipcRenderer.removeListener("bf:core-state", listener);
  },
});
