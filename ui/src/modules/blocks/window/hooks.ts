/**
 * What the panels of the window «Блочная модель · …» read: the model (the details with the labels and the statistics of
 * the fields when they are read, else the row of the list), the style of the view, what the display of the model shows
 * now. Selectors give stored objects, not new ones (`useSyncExternalStore` compares them). React.
 */
import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { CommandRegistry } from "../../../shell/registry";
import type { Store } from "../../../shell/store";
import { viewStore } from "../../scene/bind";
import type { BlockModelInfo } from "../api";
import { BLOCK_STYLES_KEY, blocksShowStore, pickedBlockStore, readStyle, type BlockStyle, type PickedBlock, type ShowInfo } from "../display";
import { blocksStore, loadBlockDetails } from "../state";

/** A value of a store; the same snapshot serves the first render on the server (the tests draw the panels to static markup). */
export function useStore<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** Re-renders when a command is registered or bound (a button that waits for a command of a later step). */
export function useRegistryVersion(registry: Pick<CommandRegistry, "subscribe" | "version">): number {
  return useSyncExternalStore(registry.subscribe, registry.version, registry.version);
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** The entry of `block_styles` of the model, read tolerantly (the default when there is none). */
export function useStyle(id: number): BlockStyle {
  const raw = useStore(viewStore, (k) => {
    const map = k[BLOCK_STYLES_KEY];
    return isObj(map) ? map[String(id)] : undefined;
  });
  return useMemo(() => readStyle(raw), [raw]);
}

/** The model: the details when they are of its version (the labels, the statistics), else the list's row; undefined — gone. */
export function useModel(id: number): { model: BlockModelInfo | undefined; details: BlockModelInfo | undefined; fresh: boolean; error: string | undefined } {
  const brief = useStore(blocksStore, (s) => s.list.find((b) => b.id === id));
  const details = useStore(blocksStore, (s) => s.details[id]);
  const error = useStore(blocksStore, (s) => s.detailErrors[id]);
  const version = brief?.version;
  const fresh = details !== undefined && details.version === version;
  useEffect(() => {
    if (version !== undefined && !fresh) void loadBlockDetails(id);
  }, [id, version, fresh]);
  return { model: fresh ? details : brief, details: fresh ? details : undefined, fresh, error };
}

export const useInfo = (id: number): ShowInfo | undefined => useStore(blocksShowStore, (s) => s.models[id]);

export const usePicked = (id: number): PickedBlock | null => useStore(pickedBlockStore, (s) => (s.picked?.model === id ? s.picked : null));
