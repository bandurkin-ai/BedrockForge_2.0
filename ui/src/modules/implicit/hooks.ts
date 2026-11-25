/** Hooks of the windows of the implicit modelling: a part of a store; a read applied only while it is the newest one of its kind. */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Store } from "../../shell/store";

export function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

export function useLatest<T>(load: (() => Promise<T>) | null, deps: unknown[], fail?: (e: unknown) => void): T | null {
  const [value, setValue] = useState<T | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    setValue(null);
    if (!load) return;
    load().then(
      (v) => mine === seq.current && setValue(v),
      (e: unknown) => mine === seq.current && fail?.(e),
    );
  }, deps);
  return value;
}

/** Re-renders when commands are bound (the reasons of the buttons change); a snapshot of the server too — the panels are drawn in the tests as text. */
export function useRegistry(registry: { subscribe: (fn: () => void) => () => void; version: () => number }): number {
  return useSyncExternalStore(registry.subscribe, registry.version, registry.version);
}
