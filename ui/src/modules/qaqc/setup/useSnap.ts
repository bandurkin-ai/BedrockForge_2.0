import { useSyncExternalStore } from "react";
import type { Store } from "../../../shell/store";

/** `useStore` that also renders on the server (tests draw the window with `renderToStaticMarkup`). */
export function useSnap<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}
