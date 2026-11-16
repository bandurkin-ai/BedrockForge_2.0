import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bfSplash", {
  onState: (cb: (state: unknown) => void) => ipcRenderer.on("bf:splash-state", (_e, s) => cb(s)),
  close: () => ipcRenderer.send("bf:splash-close"),
  copyLog: () => ipcRenderer.send("bf:splash-copy-log"),
});
