/**
 * The state of the window «Блок · i, j, k» (ТЗ 7.35 Д-30): the block asked, the answer of the core, the row (tab) shown, the order and
 * the filter of the table of the probes, the probe chosen (it is lit in 3D), what the 3D draws. One window at a time: a new block
 * replaces the old one in it. Only the newest request counts: an older answer does not overwrite it (the core also answers an older
 * request of the channel with 409 `superseded`). No React.
 */
import { errorText } from "../../../shell/props";
import { Store } from "../../../shell/store";
import { isSuperseded, nextSeq, postInspect, refusalOf, type Candidate, type InspectAnswer, type InspectRequest, type InspectResult, type Refusal } from "./api";
import { nextSort, type Sort, type SortKey } from "./view";

export const INSPECT_WINDOW = "estimate-inspect";

/** What the main 3D draws of the block: the ellipsoid of the pass, the segments to the probes taken, the probes cut off. */
export interface Show3d {
  ellipsoid: boolean;
  segments: boolean;
  excluded: boolean;
}

export interface InspectState {
  request: InspectRequest | null;
  loading: boolean;
  answer: InspectAnswer | null;
  error: Refusal | null;
  /** the row of the plan shown (its `estimation_id`); null — the first */
  active: number | null;
  sort: Sort | null;
  /** the filter by the reason (a code); null — all */
  reason: string | null;
  /** the probe lit (`Candidate.i`) */
  picked: number | null;
  show: Show3d;
  /** the snapshot was copied / could not be: a note under the window */
  note: string | null;
}

const EMPTY: InspectState = { request: null, loading: false, answer: null, error: null, active: null, sort: null, reason: null, picked: null, show: { ellipsoid: true, segments: true, excluded: true }, note: null };

export const inspectStore = new Store<InspectState>(EMPTY);

export function resetInspect(): void {
  asked++;
  inspectStore.set({ ...EMPTY, show: inspectStore.get().show });
}

let asked = 0;

/** The row shown: the tab chosen, else the first one the block is in the domain of, else the first. */
export function activeResult(s: Pick<InspectState, "answer" | "active"> = inspectStore.get()): InspectResult | null {
  const rs = s.answer?.results ?? [];
  return rs.find((r) => r.estimation_id === s.active) ?? rs.find((r) => r.active) ?? rs[0] ?? null;
}

/** The probe lit, if it is among the candidates of the row shown. */
export function pickedCandidate(s: InspectState = inspectStore.get()): Candidate | null {
  const r = activeResult(s);
  return r && s.picked !== null ? (r.candidates.find((c) => c.i === s.picked) ?? null) : null;
}

/**
 * Asks the core for a block. The tab, the order and the filter stay when it is the same row of the plan after a re-read; a new
 * block resets the probe lit. Resolves with the answer, or null when the request was overtaken or failed (`error` says why).
 */
export async function inspect(req: InspectRequest, post: typeof postInspect = postInspect): Promise<InspectAnswer | null> {
  const mine = ++asked;
  const keep = inspectStore.get().request;
  const same = keep !== null && keep.model_id === req.model_id && keep.params_id === req.params_id;
  inspectStore.set({ request: req, loading: true, error: null, ...(same ? {} : { active: null, reason: null }), picked: null, note: null });
  try {
    const answer = await post(req, nextSeq());
    if (mine !== asked) return null;
    const cur = inspectStore.get();
    const keepTab = answer.results.some((r) => r.estimation_id === cur.active);
    inspectStore.set({ answer, loading: false, error: null, active: keepTab ? cur.active : null });
    return answer;
  } catch (e) {
    if (mine !== asked || isSuperseded(e)) return null;
    inspectStore.set({ loading: false, error: refusalOf(e), answer: null });
    return null;
  }
}

/** The same block asked again (the rows were run, the data changed). */
export function reinspect(): Promise<InspectAnswer | null> {
  const r = inspectStore.get().request;
  return r ? inspect(r) : Promise.resolve(null);
}

export function setActive(id: number): void {
  inspectStore.set({ active: id, reason: null, picked: null });
}

export const setReason = (reason: string | null): void => inspectStore.set({ reason });

export const sortBy = (key: SortKey): void => inspectStore.set((s) => ({ sort: nextSort(s.sort, key) }));

export const pick = (i: number | null): void => inspectStore.set((s) => ({ picked: s.picked === i ? null : i }));

export const setShow = (patch: Partial<Show3d>): void => inspectStore.set((s) => ({ show: { ...s.show, ...patch } }));

export const setNote = (note: string | null): void => inspectStore.set({ note });

/** The text of a failure of the window without a `Refusal` (a clipboard that is not there). */
export const failText = errorText;
