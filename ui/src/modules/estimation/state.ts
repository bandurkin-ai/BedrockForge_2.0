/**
 * The rows «Параметры оценки» and the plans of the open project (ТЗ 8.8.4; ADR 0024 §2, §11) for the section «Параметры оценки»
 * of the tree and the windows: the list (`GET /estimations`), the details of the rows the windows opened (`GET /estimations/{id}`),
 * the plans. Read when the project opens and on `geostat.changed {kind: "estimation" | "estimation_plan"}` of the core
 * (`objects.changed {kind: "block_model"}` after a run is the business of `modules/blocks`); a read overtaken by a newer one is
 * dropped. The wizard keeps its own state (`wizardState.ts`). No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { blocksStore } from "../blocks/state";
import { projectStore } from "../projects/state";
import { deleteEstimationApi, loadEstimation, loadEstimations, loadPlans, type EstimationInfo, type EstimationRow, type PlanRow } from "./api";

export interface EstimationState {
  rows: EstimationRow[];
  plans: PlanRow[];
  loaded: boolean;
  error: string | null;
  /** `GET /estimations/{id}` of the rows a window opened */
  details: Record<number, EstimationInfo>;
}

const EMPTY: EstimationState = { rows: [], plans: [], loaded: false, error: null, details: {} };

export const estimationStore = new Store<EstimationState>(EMPTY);

/** The block models the estimation writes to: a regular step, with blocks. */
export const estimableModels = () => blocksStore.get().list.filter((m) => !m.grid.variable && m.cells > 0);

export const rowById = (id: number): EstimationRow | undefined => estimationStore.get().rows.find((r) => r.id === id);

export function clearEstimations(): void {
  estimationStore.set(EMPTY);
}

let seq = 0;

/** Reads the rows and the plans; the details of a row that is gone are dropped, of a changed one — read again. */
export async function loadRows(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) return clearEstimations();
  try {
    const [rows, plans] = await Promise.all([loadEstimations(), loadPlans()]);
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = estimationStore.get();
    const details: Record<number, EstimationInfo> = {};
    const stale: number[] = [];
    for (const r of rows) {
      const d = cur.details[r.id];
      if (!d) continue;
      if (d.version === r.version && d.stale === r.stale) details[r.id] = d;
      else stale.push(r.id);
    }
    estimationStore.set({ rows, plans, loaded: true, error: null, details });
    for (const id of stale) void loadDetails(id).catch(() => undefined); // a failed read of the details is not an unhandled rejection: the wizard asks again when it needs them
  } catch (e) {
    if (mine !== seq) return;
    estimationStore.set({ loaded: true, error: errorText(e) });
  }
}

const detailSeq = new Map<number, number>();

/** The row with its parameters as stored (the wizard «Изменить», the window of the plan). */
export async function loadDetails(id: number): Promise<EstimationInfo | null> {
  const mine = (detailSeq.get(id) ?? 0) + 1;
  detailSeq.set(id, mine);
  const uid = projectStore.get().current?.uid ?? null;
  const info = await loadEstimation(id);
  if (detailSeq.get(id) !== mine || projectStore.get().current?.uid !== uid) return null;
  estimationStore.set((s) => ({ details: { ...s.details, [id]: info } }));
  return info;
}

/** What a `geostat.changed` event means for the list: true — read it again (a simulation is the business of Э18б). */
export function isEstimationEvent(data: string): boolean {
  try {
    const kind = (JSON.parse(data) as { kind?: unknown }).kind;
    return kind === undefined || kind === "estimation" || kind === "estimation_plan";
  } catch {
    return true; // an event without a body: whatever changed, read again
  }
}

/** `DELETE /estimations/{id}`: the row leaves the plans; the fields of the model stay. The list is read again after it, a failure too. */
export async function deleteEstimation(id: number): Promise<{ plans: number[] }> {
  try {
    const r = await deleteEstimationApi(id);
    estimationStore.set((s) => {
      const details = { ...s.details };
      delete details[id];
      return { details };
    });
    return { plans: r.plans };
  } finally {
    await loadRows();
  }
}
