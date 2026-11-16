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
  coreRetry(): void;
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
