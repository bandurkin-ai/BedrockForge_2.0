/**
 * What the window «Сверка данных и модели» knows (ТЗ 7.35 Д-33; docs/progress/e19-api.md §8.3): the model, the table and the field of the codes
 * (taken from the data of the model as long as the person does not choose others), the answer (the matrices and the list of the intervals that
 * do not agree, by pages), the interval picked. A read that a newer one overtook is dropped. «Перейти» — the hole of the interval is selected
 * and the window «Данные по скважинам» (7.17) is opened on it. No React.
 */
import type { App } from "../../shell/app";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { loadCompare, type CompareAnswer, type Mismatch } from "./compareApi";
import { formOfMeta } from "./form";
import { loadDetails } from "./state";
import type { Metric } from "./compareModel";

export const COMPARE_WINDOW = "implicit-compare";
export const COMPARE_TITLE = "Сверка данных и модели";

export interface CompareWin {
  modelId: number | null;
  setId: number | null;
  /** the key or the name of the table */
  table: string | null;
  field: string | null;
  page: number;
  size: number;
  metric: Metric;
  phase: "idle" | "loading" | "ok" | "error";
  answer: CompareAnswer | null;
  error: string | null;
  /** index of the picked row of the list of the page */
  picked: number | null;
  note: string | null;
}

const fresh = (): CompareWin => ({ modelId: null, setId: null, table: null, field: null, page: 1, size: 100, metric: "length", phase: "idle", answer: null, error: null, picked: null, note: null });

export const compareStore = new Store<CompareWin>(fresh());

let seq = 0;

export const ready = (s: CompareWin): boolean => s.modelId !== null && s.setId !== null && !!s.table && !!s.field;

/** The window for a model; the data of the model (or `given`) fill the choice. Does not read — «Сверить» does. */
export async function openCompare(modelId: number, given: { setId?: number | null; table?: string | null; field?: string | null } = {}): Promise<void> {
  const same = compareStore.get().modelId === modelId;
  if (!same) compareStore.set({ ...fresh(), modelId });
  const s = compareStore.get();
  let { setId, table, field } = { setId: given.setId ?? (same ? s.setId : null), table: given.table ?? (same ? s.table : null), field: given.field ?? (same ? s.field : null) };
  if (setId === null || !table || !field) {
    const info = await loadDetails(modelId).catch(() => null);
    if (info) {
      const f = formOfMeta(info.name, info.meta);
      setId = setId ?? f.setId;
      table = table ?? f.table;
      field = field ?? f.field;
    }
  }
  compareStore.set({ setId, table: table ?? null, field: field ?? null });
}

export function choose(patch: Partial<Pick<CompareWin, "setId" | "table" | "field" | "size" | "metric">>): void {
  compareStore.set({ ...patch, ...("setId" in patch ? { table: null, field: null } : "table" in patch ? { field: null } : {}) });
}

/** «Сверить»: the matrices and the first page of the list. */
export async function runCompare(page = 1): Promise<CompareAnswer | null> {
  const s = compareStore.get();
  if (!ready(s)) {
    compareStore.set({ error: "Выберите набор скважин, таблицу кодов и поле кода." });
    return null;
  }
  const mine = ++seq;
  const uid = projectStore.get().current?.uid ?? null;
  compareStore.set({ phase: "loading", error: null, picked: null, note: null });
  try {
    const answer = await loadCompare(s.modelId as number, { set_id: s.setId as number, table: s.table as string, field: s.field as string, page, size: s.size });
    if (mine !== seq || (projectStore.get().current?.uid ?? null) !== uid) return null;
    compareStore.set({ phase: "ok", answer, page: answer.mismatches.page, error: null });
    return answer;
  } catch (e) {
    if (mine !== seq) return null;
    compareStore.set({ phase: "error", answer: null, error: errorText(e) });
    return null;
  }
}

export const goPage = (page: number): Promise<CompareAnswer | null> => runCompare(page);

/** «Перейти в 7.17»: the hole is selected (in 3D and in the tables) and the window «Данные по скважинам» opens on it; the depth is told. */
export async function gotoHole(app: App, m: Mismatch): Promise<string> {
  const s = compareStore.get();
  const where = `${m.hole}, интервал ${m.from ?? "—"}–${m.to ?? "—"} м (код данных ${m.data_code}, модели — ${m.model_code})`;
  const sel = await app.registry.execute("hole.select", { hole_id: m.hole, ...(s.setId !== null ? { set_id: s.setId } : {}) }, "user");
  if (!sel.ok) app.store.set({ holeId: m.hole }); // the hole is not in the 3D window: the data window still opens on it
  const r = await app.registry.execute("holes.data_window", {}, "user");
  const text = r.ok ? `Скважина ${where}${m.depth !== null ? `; глубина середины ${m.depth.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} м` : ""}.` : `Скважину ${m.hole} не открыть: ${r.result}`;
  compareStore.set({ note: text });
  return text;
}

export function resetCompare(): void {
  seq++;
  compareStore.set(fresh());
}
