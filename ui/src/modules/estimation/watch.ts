/**
 * A subscription to a part of a store: `fn` runs only when what `select` gives changes (`Object.is`). The 3D layers of the estimation
 * (`scene.ts`, `inspect/scene.ts`) ask «is the window open» of the store of the windows, which changes at every move of every window:
 * without the selector the scene was built and redrawn for each of them. No React.
 */
export function watchSelected<S, T>(store: { get(): S; subscribe(fn: () => void): () => void }, select: (s: S) => T, fn: () => void): () => void {
  let last = select(store.get());
  return store.subscribe(() => {
    const next = select(store.get());
    if (Object.is(next, last)) return;
    last = next;
    fn();
  });
}
