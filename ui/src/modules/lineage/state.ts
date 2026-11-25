/**
 * The states of the computed objects of the open project (Д-35; ADR 0025 §10): what the badges of the tree and the panels of
 * «Свойства» show. The states are asked in batches — a row of the tree (its badge) or a section that opens says which nodes it
 * shows (`watch`); the asks of one moment go in one `POST /lineage/status`. What is watched is read again after what can change
 * a state: `lineage.changed`, `objects.changed`, `drillholes.changed`, `composites.changed`, `geostat.changed` of the core and
 * a finished job (the states are pulled, not marked: the core computes them when asked). A read overtaken by a newer one is
 * dropped; another project — an empty cache. No React.
 */
import { errorText } from "../../shell/props";
import type { Selected } from "../../shell/selection";
import { Store } from "../../shell/store";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { nodeKey, postStatus, type LNode, type LStatus } from "./api";

export interface LineageState {
  /** by `nodeKey` */
  statuses: Record<string, LStatus>;
  /** grows with every read: the panels of the origin and of the dependents read again when it moves */
  version: number;
  error: string | null;
}

const EMPTY: LineageState = { statuses: {}, version: 0, error: null };

export const lineageStore = new Store<LineageState>(EMPTY);

export const statusOf = (n: LNode): LStatus | undefined => lineageStore.get().statuses[nodeKey(n)];

/** The events of the core after which a state can be different. */
export const REFRESH_EVENTS: readonly string[] = ["lineage.changed", "objects.changed", "drillholes.changed", "composites.changed", "geostat.changed"];
/** The wait that gathers the asks of one moment into one request, ms. */
export const BATCH_MS = 10;
/** The wait that gathers the events of one moment into one read, ms. */
export const REFRESH_MS = 150;

const watched = new Map<string, { node: LNode; n: number }>();
let pending = new Map<string, LNode>();
let batchTimer: ReturnType<typeof setTimeout> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let seq = 0;
let epoch: string | null = null;

function currentUid(): string | null {
  return projectStore.get().current?.uid ?? null;
}

/** What is watched now (for the tests and the summary of `lineage.status`). */
export const watchedNodes = (): LNode[] => [...watched.values()].map((w) => w.node);

async function read(nodes: LNode[]): Promise<void> {
  if (!nodes.length) return;
  const uid = currentUid();
  if (!uid) return;
  const mine = ++seq;
  try {
    const got = await postStatus(nodes);
    if (currentUid() !== uid) return;
    const next = { ...lineageStore.get().statuses };
    // a read overtaken by a newer one still tells about its nodes (the newer one may not have had them)
    for (const s of got) next[nodeKey(s)] = s;
    lineageStore.set((s) => ({ statuses: next, version: s.version + 1, error: mine === seq ? null : s.error }));
  } catch (e) {
    if (currentUid() === uid) lineageStore.set({ error: errorText(e) });
  }
}

function flush(): void {
  batchTimer = null;
  const nodes = [...pending.values()];
  pending = new Map();
  void read(nodes);
}

/** Asks the states of the nodes (the asks of one moment are one request). */
export function request(nodes: readonly LNode[]): void {
  for (const n of nodes) pending.set(nodeKey(n), n);
  if (pending.size && !batchTimer) batchTimer = setTimeout(flush, BATCH_MS);
}

/**
 * Starts to show the nodes: their states are asked now (not those the cache has) and read again on every event that can change
 * them. Returns the function that stops it (a row that goes, a section that closes).
 */
export function watch(nodes: readonly LNode[]): () => void {
  const fresh: LNode[] = [];
  const mine = new Map<string, LNode>();
  for (const n of nodes) mine.set(nodeKey(n), n);
  for (const [k, n] of mine) {
    const w = watched.get(k);
    if (w) w.n += 1;
    else watched.set(k, { node: n, n: 1 });
    if (!lineageStore.get().statuses[k]) fresh.push(n);
  }
  request(fresh);
  return () => {
    for (const k of mine.keys()) {
      const w = watched.get(k);
      if (!w) continue;
      w.n -= 1;
      if (w.n <= 0) watched.delete(k);
    }
  };
}

/** Reads what is watched again, now. */
export async function refreshAll(): Promise<void> {
  await read(watchedNodes());
}

/** Reads what is watched again soon (the events of one moment — one read). */
export function scheduleRefresh(): void {
  if (refreshTimer || !watched.size) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void refreshAll();
  }, REFRESH_MS);
}

/** A state the core just told (the answer of «Закрепить»): into the cache at once, the rest — soon. */
export function put(status: LStatus): void {
  lineageStore.set((s) => ({ statuses: { ...s.statuses, [nodeKey(status)]: status }, version: s.version + 1 }));
  scheduleRefresh();
}

export function clearLineage(): void {
  if (batchTimer) clearTimeout(batchTimer);
  if (refreshTimer) clearTimeout(refreshTimer);
  batchTimer = refreshTimer = null;
  pending = new Map();
  seq += 1;
  lineageStore.set({ ...EMPTY, version: lineageStore.get().version + 1 });
}

/** The counts of the states of the nodes in the cache: «Устарели: 3 из 12». */
export function summary(nodes: readonly LNode[] = watchedNodes()): { total: number; stale: number; lost: number; pinned: number; stale_names: string[] } {
  const all = lineageStore.get().statuses;
  const got = nodes.map((n) => all[nodeKey(n)]).filter((s): s is LStatus => s !== undefined);
  const stale = got.filter((s) => s.state === "stale");
  return {
    total: got.length,
    stale: stale.length,
    lost: got.filter((s) => s.state === "source_lost").length,
    pinned: got.filter((s) => s.state === "pinned").length,
    stale_names: stale.map((s) => s.name),
  };
}

/**
 * The node of the object chosen in the tree (`object.select`): a wireframe and a block model have a state; layers of lines and
 * rasters are not computed.
 */
export function nodeOfSelected(sel: Selected | null): LNode | null {
  if (!sel) return null;
  if (sel.kind === "wireframe") return { kind: "wireframe", id: sel.id };
  if (sel.kind === "blocks") return { kind: "object:block_model", id: sel.id };
  return null;
}

/** An event of the core: what can change a state is read again soon. */
export function handleCoreEvent(event: string): void {
  if (REFRESH_EVENTS.includes(event)) scheduleRefresh();
}

/** Follows the project and the events of the core; returns the function that stops it. */
export function followEvents(): () => void {
  epoch = currentUid();
  const offProject = projectStore.subscribe(() => {
    const cur = currentUid();
    if (cur === epoch) return;
    epoch = cur;
    watched.clear();
    clearLineage();
  });
  const offEvents = onCoreEvent(handleCoreEvent);
  const offJobs = onJobFinished(() => scheduleRefresh());
  return () => {
    offProject();
    offEvents();
    offJobs();
  };
}
