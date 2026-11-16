import { useSyncExternalStore } from "react";
import type { Store } from "./store";

export function useStore<T, R>(store: Store<T>, select: (s: T) => R): R {
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}

/** Re-renders when commands are bound or the command log grows. */
export function useRegistryVersion(registry: { subscribe: (fn: () => void) => () => void; version: () => number }): number {
  return useSyncExternalStore(registry.subscribe, registry.version);
}
