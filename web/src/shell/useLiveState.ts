import { useCallback, useRef, useState, type MutableRefObject } from "react";

/**
 * State whose latest value is also readable synchronously through a ref.
 * Commands run back to back (Claude sends several at once) and must see each other's
 * changes without waiting for React to re-render.
 */
export function useLiveState<T>(initial: T | (() => T)): [T, (next: T | ((prev: T) => T)) => void, MutableRefObject<T>] {
  const [state, setState] = useState(initial);
  const ref = useRef(state);
  const set = useCallback((next: T | ((prev: T) => T)) => {
    ref.current = typeof next === "function" ? (next as (prev: T) => T)(ref.current) : next;
    setState(ref.current);
  }, []);
  return [state, set, ref];
}
