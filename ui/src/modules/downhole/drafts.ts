/**
 * Drafts of the open display forms (ТЗ 7.16: «изменения сразу видны на скважинах»; ADR 0007 §3: until
 * «Сохранить» a draft lives only in the memory of its window — not in the tree, not in the view settings).
 *
 * The 3D layer of displays subscribes to `draftStore`: a draft with `chartId` is drawn instead of that saved
 * display, a draft without one is drawn in addition to the switched-on displays. Keys are window ids.
 */
import type { StripLogSettings } from "../../charts/striplog/model";
import { Store } from "../../shell/store";

export interface DownholeDraft {
  setId: number;
  /** the saved display the form edits; null — a new one */
  chartId: number | null;
  /** links to styles already resolved with the library */
  settings: StripLogSettings;
}

export const draftStore = new Store<{ drafts: Record<string, DownholeDraft> }>({ drafts: {} });

export function setDraft(key: string, draft: DownholeDraft | null): void {
  draftStore.set((s) => {
    const { [key]: _gone, ...rest } = s.drafts;
    return { drafts: draft ? { ...rest, [key]: draft } : rest };
  });
}

/** Drafts in the order of their windows (the 3D layer draws them on top of the saved displays). */
export function draftList(): (DownholeDraft & { key: string })[] {
  return Object.entries(draftStore.get().drafts).map(([key, d]) => ({ key, ...d }));
}
