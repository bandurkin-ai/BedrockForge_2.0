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
