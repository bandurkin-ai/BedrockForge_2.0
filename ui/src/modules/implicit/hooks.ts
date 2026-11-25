/** Hooks of the windows of the implicit modelling that are its own (`useSel` and `useLatest` are `../estimation/hooks`). */
import { useSyncExternalStore } from "react";

/** Re-renders when commands are bound (the reasons of the buttons change); a snapshot of the server too — the panels are drawn in the tests as text. */
export function useRegistry(registry: { subscribe: (fn: () => void) => () => void; version: () => number }): number {
  return useSyncExternalStore(registry.subscribe, registry.version, registry.version);
}
