/** React hooks of the graph of dependencies: the state of a node and the watch of a section of the tree (Д-35). */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { errorText } from "../../shell/props";
import type { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { nodeKey, type LNode, type LStatus } from "./api";
import { lineageStore, watch } from "./state";

/** `useStore` with a server snapshot too: these components render statically in the tests (the selected value must be stable). */
export function useSelect<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** The uid of the open project: a node of another project is not the same node. */
export const useProjectUid = (): string | null => useSelect(projectStore, (s) => s.current?.uid ?? null);

/**
 * Shows the nodes: their states are asked in one batch (with those of the other rows of the same moment) and kept fresh while
 * the component stays. `nodes` of a section of the tree — one request for the whole section when it opens.
 */
export function useWatch(nodes: readonly LNode[]): void {
  const uid = useProjectUid();
  const key = nodes.map(nodeKey).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stable = useMemo(() => nodes, [key]);
  useEffect(() => (stable.length ? watch(stable) : undefined), [stable, uid]);
}

/**
 * What a panel asks of the core about a node (`load`): read when the node changes and again whenever the states were read
 * (an event of the core, a finished job); an answer overtaken by a newer ask is dropped; the old data stay while the new come.
 */
export function usePulled<T>(load: () => Promise<T>, key: string): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ key: string; data: T | null; error: string | null }>({ key, data: null, error: null });
  const version = useSelect(lineageStore, (s) => s.version);
  const seq = useRef(0);
  const latest = useRef(load);
  latest.current = load;
  useEffect(() => {
    const mine = ++seq.current;
    latest.current().then(
      (data) => mine === seq.current && setState({ key, data, error: null }),
      (e: unknown) => mine === seq.current && setState((s) => ({ key, data: s.key === key ? s.data : null, error: errorText(e) })),
    );
  }, [key, version]);
  return state.key === key ? { data: state.data, error: state.error } : { data: null, error: null };
}

/** The state of a node as the core told it last, or `undefined` while it is not known. */
export function useStatus(node: LNode | null): LStatus | undefined {
  useWatch(node ? [node] : []);
  return useSelect(lineageStore, (s) => (node ? s.statuses[nodeKey(node)] : undefined));
}
