/**
 * The rows «Моделирование» of the open project (ТЗ 8.8.1; ADR 0024 §9) for the section of the tree and the windows: the list
 * (`GET /simulations`), the details of the rows the windows opened (`GET /simulations/{id}`). Read when the project opens and on
 * `geostat.changed {kind: "simulation"}` of the core; a read overtaken by a newer one is dropped. The wizard keeps its own state
 * (`wizardState.ts`). No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { deleteSimulationApi, loadSimulation, loadSimulations, type SimulationInfo, type SimulationRow } from "./api";

export interface SimulationState {
  rows: SimulationRow[];
  loaded: boolean;
  error: string | null;
  /** `GET /simulations/{id}` of the rows a window opened */
  details: Record<number, SimulationInfo>;
}

const EMPTY: SimulationState = { rows: [], loaded: false, error: null, details: {} };

export const simulationStore = new Store<SimulationState>(EMPTY);

export const simById = (id: number): SimulationRow | undefined => simulationStore.get().rows.find((r) => r.id === id);

export const rowsByName = (list: readonly SimulationRow[]): SimulationRow[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));

export function clearSimulations(): void {
  simulationStore.set(EMPTY);
}

let seq = 0;

/** Reads the rows; the details of a row that is gone are dropped, of a changed one — read again. */
export async function loadRows(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) return clearSimulations();
  try {
    const rows = await loadSimulations();
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = simulationStore.get();
    const details: Record<number, SimulationInfo> = {};
    const stale: number[] = [];
    for (const r of rows) {
      const d = cur.details[r.id];
      if (!d) continue;
      if (d.version === r.version && d.stale === r.stale && (d.meta.last_run as { at?: string } | null)?.at === (r.last_run?.at ?? undefined)) details[r.id] = d;
      else stale.push(r.id);
    }
    simulationStore.set({ rows, loaded: true, error: null, details });
    for (const id of stale) void loadDetails(id).catch(() => undefined); // a failed read of the details is not an unhandled rejection: the window asks again when it needs them
  } catch (e) {
    if (mine !== seq) return;
    simulationStore.set({ loaded: true, error: errorText(e) });
  }
}

const detailSeq = new Map<number, number>();

/** The row with its parameters as stored (the wizard «Изменить», the windows of the summary and of the post-processing). */
export async function loadDetails(id: number): Promise<SimulationInfo | null> {
  const mine = (detailSeq.get(id) ?? 0) + 1;
  detailSeq.set(id, mine);
  const uid = projectStore.get().current?.uid ?? null;
  const info = await loadSimulation(id);
  if (detailSeq.get(id) !== mine || projectStore.get().current?.uid !== uid) return null;
  simulationStore.set((s) => ({ details: { ...s.details, [id]: info } }));
  return info;
}

/** What a `geostat.changed` event means for the list: true — read it again (an event without a body: whatever changed). */
export function isSimulationEvent(data: string): boolean {
  try {
    const kind = (JSON.parse(data) as { kind?: unknown }).kind;
    return kind === undefined || kind === "simulation";
  } catch {
    return true;
  }
}

/** `DELETE /simulations/{id}`: the row and its realizations go; the fields written to the block model stay. The list is read again after it, a failure too. */
export async function deleteSimulation(id: number): Promise<void> {
  try {
    await deleteSimulationApi(id);
    simulationStore.set((s) => {
      const details = { ...s.details };
      delete details[id];
      return { details };
    });
  } finally {
    await loadRows();
  }
}
