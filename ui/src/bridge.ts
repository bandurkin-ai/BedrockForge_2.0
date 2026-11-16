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
}

declare global {
  interface Window {
    bf?: BfBridge;
  }
}

export function bridge(): BfBridge | null {
  return typeof window !== "undefined" && window.bf ? window.bf : null;
}
