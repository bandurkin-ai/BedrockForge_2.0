/** Typed access to the preload bridge (window.bf). Pure module: no React, no three.js. */

export interface CoreInfo {
  baseUrl: string;
  token: string;
  version: string;
}

export interface BfBridge {
  core(): CoreInfo | null;
  appVersion(): string;
  uiReady(): void;
  spike(): string | null;
  /** User settings file %APPDATA%\BedrockForge\settings.json (null — missing or damaged). */
  loadSettings(): unknown;
  saveSettings(data: unknown): void;
  copyText(text: string): void;
  saveText(defaultName: string, text: string): Promise<boolean>;
  /** Save dialog and write (PNG/SVG of a chart, CSV of its data with a BOM, .bfstyle.json): the path or null. */
  saveFile(name: string, kind: "png" | "svg" | "csv" | "bfstyle", data: string | Uint8Array): Promise<string | null>;
  /** Open dialog for a text file (.bfstyle.json, CSV of codes): its name and text, or null when cancelled. */
  openText(kind: "bfstyle" | "csv"): Promise<{ name: string; text: string | null; error?: string } | null>;
  coreRetry(): void;
  /** «Сохранить как» for .bfggis: the chosen path or null. */
  /** Save dialog: .bfggis by default, or a table format (dat, csv, xlsx). */
  pickSave(defaultName: string, kind?: "dat" | "csv" | "xlsx"): Promise<string | null>;
  /** «Открыть» a .bfggis: the chosen path or null. */
  pickOpen(): Promise<string | null>;
  pickFolder(): Promise<string | null>;
  /** Files for import: "tables" (DAT, CSV, Excel) or "prj"; [] when cancelled. */
  pickFiles(kind: "tables" | "prj" | "objects", multi: boolean): Promise<string[]>;
  showInFolder(file: string): void;
  /** Disk path of a file dropped from Explorer. */
  pathForFile(file: File): string;
  coreLog(): string;
  onCoreState(cb: (state: { status: "online" | "offline" | "starting"; info: CoreInfo | null; message?: string }) => void): () => void;
}

declare global {
  interface Window {
    bf?: BfBridge;
  }
}

export function bridge(): BfBridge | null {
  return typeof window !== "undefined" && window.bf ? window.bf : null;
}
