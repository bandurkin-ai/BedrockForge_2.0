/**
 * What the chart commands and the tree know without asking (ADR 0019 §9, §11): the number of components of each
 * set (the most of its tables — «Дендрограмма» needs 2 and more), read for the active set and again after an edit
 * of its data. No React.
 */
import { Store } from "../../shell/store";
import { invalidateFields, loadFields } from "./api";

export interface ChartsState {
  /** set id → the most components among its tables; absent — not read yet */
  components: Record<number, number>;
}

export const chartsStore = new Store<ChartsState>({ components: {} });

/** Reads the tables of the set (the cache of `loadFields` answers again until an edit). */
export async function readComponents(setId: number): Promise<void> {
  try {
    const f = await loadFields(setId);
    const most = f.tables.reduce((m, t) => Math.max(m, t.components), 0);
    chartsStore.set((s) => ({ components: { ...s.components, [setId]: most } }));
  } catch {
    /* the set is gone or the core is away: nothing known */
  }
}

/** An edit of the data of a set (all sets without one): its fields are read again. */
export function dataChanged(setId?: number): void {
  invalidateFields(setId);
  chartsStore.set((s) => {
    if (setId === undefined) return { components: {} };
    const { [setId]: _gone, ...rest } = s.components;
    return { components: rest };
  });
}

/** Why «Дендрограмма» cannot be built for the set: fewer than 2 components (unknown — no reason). */
export function componentsReason(setId: number | null): string | null {
  if (setId === null) return null;
  const n = chartsStore.get().components[setId];
  return n !== undefined && n < 2 ? "Нужно 2 и больше компонента" : null;
}
