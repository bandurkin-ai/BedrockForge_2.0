/**
 * The state of the tab «QAQC» outside React (ADR 0020 §6, §11): the data of control samples (null — the first),
 * the element (null — the first of the data), the filter, the subtab, the type of duplicates, the counts of the
 * last calculation (the counter «записей N из M»), the batch shown and the sample whose card is open. It lives for
 * the session; another project starts it again. `rev` grows on an edit of the data or of the setup: the subtabs
 * compute again. One request per slot at a time: a newer one drops the answer of an older one (`latest`).
 * No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { listData, type CalcBody, type CalcCounts, type QaqcData } from "./api";
import type { DupKind, QaqcSection } from "./commands";
import { EMPTY_FILTER, filterJson, filterKey, type QaqcFilter } from "./filters";

/** A batch shown on the charts and in «Партии» (a click on a sample). */
export interface Highlight {
  lab: string | null;
  batch: string;
}

/** The sample whose card is open: its row in the records and what the chart knows of it. */
export interface SamplePick {
  row: number;
  section: QaqcSection;
  lab: string | null;
  batch: string | null;
  /** facts of the point (date, value, z, status…) in the order shown */
  facts: [string, string][];
}

export interface QaqcState {
  /** null — not read yet */
  datas: QaqcData[] | null;
  dataError: string | null;
  /** null — the first data */
  dataId: number | null;
  /** null — the first element of the data */
  element: string | null;
  filter: QaqcFilter;
  section: QaqcSection;
  dupKind: DupKind;
  /** grows on an edit of the data or of the setup (`qaqc.changed`): the subtabs compute again */
  rev: number;
  /** grows on an edit of the reports (`objects.changed {kind: "qaqc_report"}`) */
  reportsRev: number;
  /** the counts of the last calculation of `key` (`calcKey`) */
  counts: { key: string; counts: CalcCounts } | null;
  highlight: Highlight | null;
  sample: SamplePick | null;
}

export const initialQaqc = (): QaqcState => ({
  datas: null,
  dataError: null,
  dataId: null,
  element: null,
  filter: EMPTY_FILTER,
  section: "standards",
  dupKind: "DUP_F",
  rev: 0,
  reportsRev: 0,
  counts: null,
  highlight: null,
  sample: null,
});

export const qaqcStore = new Store<QaqcState>(initialQaqc());

/** The data the tab works on: the chosen one, or the first. */
export function activeData(s: QaqcState = qaqcStore.get()): QaqcData | null {
  const list = s.datas ?? [];
  return list.find((d) => d.id === s.dataId) ?? list[0] ?? null;
}

/** The element of the tab: the chosen one when the data have it (any case), or the first element. */
export function activeElement(s: QaqcState = qaqcStore.get()): string | null {
  const els = activeData(s)?.elements ?? [];
  const want = s.element?.toLowerCase();
  return (want ? els.find((e) => e.name.toLowerCase() === want)?.name : undefined) ?? els[0]?.name ?? null;
}

/** The key of a calculation: the data and their version, the element, the filter, `rev`; null — nothing to compute. */
export function calcKey(s: QaqcState = qaqcStore.get()): string | null {
  const data = activeData(s);
  const element = activeElement(s);
  if (!data || !element) return null;
  return JSON.stringify([data.id, data.version, element, filterKey(s.filter), s.rev]);
}

/** The body of `POST /qaqc/calc/*` for the tab; null — no data or no element. */
export function calcBody(s: QaqcState = qaqcStore.get(), extra: Partial<CalcBody> = {}): CalcBody | null {
  const data = activeData(s);
  const element = activeElement(s);
  if (!data || !element) return null;
  return { data_id: data.id, element, filter: filterJson(s.filter), ...extra };
}

const tickets = new Map<string, number>();

/** The answer of a request that was overtaken by a newer one in its slot. */
export const DROPPED: unique symbol = Symbol("dropped");

/** Runs a request of `slot`; when a newer one of the slot started meanwhile, its answer (or failure) is dropped. */
export async function latest<T>(slot: string, run: () => Promise<T>): Promise<T | typeof DROPPED> {
  const ticket = (tickets.get(slot) ?? 0) + 1;
  tickets.set(slot, ticket);
  try {
    const value = await run();
    return tickets.get(slot) === ticket ? value : DROPPED;
  } catch (e) {
    if (tickets.get(slot) !== ticket) return DROPPED;
    throw e;
  }
}

/** Every request on its way is dropped (another project). */
export function dropRequests(): void {
  for (const [slot, t] of tickets) tickets.set(slot, t + 1);
}

/** Reads the list of the data of the project; the choice stays when the data are still there. */
export async function loadDatas(): Promise<QaqcData[] | null> {
  try {
    const datas = await latest("datas", listData);
    if (datas === DROPPED) return qaqcStore.get().datas;
    const s = qaqcStore.get();
    qaqcStore.set({ datas, dataError: null, dataId: s.dataId !== null && datas.some((d) => d.id === s.dataId) ? s.dataId : null });
    return datas;
  } catch (e) {
    qaqcStore.set({ dataError: errorText(e) });
    return null;
  }
}

/** Other data: the card and the batch shown go (they are of the records of the data before). */
export function setData(id: number | null): void {
  if (id === qaqcStore.get().dataId) return;
  qaqcStore.set({ dataId: id, highlight: null, sample: null });
}

export function setElement(element: string | null): void {
  if (element === qaqcStore.get().element) return;
  qaqcStore.set({ element, sample: null });
}

export function setFilter(filter: QaqcFilter): void {
  if (filterKey(filter) === filterKey(qaqcStore.get().filter)) return;
  qaqcStore.set({ filter });
}

export function setSection(section: QaqcSection): void {
  if (section !== qaqcStore.get().section) qaqcStore.set({ section });
}

export function setDupKind(dupKind: DupKind): void {
  if (dupKind !== qaqcStore.get().dupKind) qaqcStore.set({ dupKind });
}

/** A subtab tells the counts of its answer to `key`: the counter shows them while the key is current. */
export function reportCounts(key: string, counts: CalcCounts): void {
  const c = qaqcStore.get().counts;
  if (c && c.key === key && JSON.stringify(c.counts) === JSON.stringify(counts)) return;
  qaqcStore.set({ counts: { key, counts } });
}

/** The counts of the current calculation, or null before its first answer. */
export function currentCounts(s: QaqcState = qaqcStore.get()): CalcCounts | null {
  const key = calcKey(s);
  return key !== null && s.counts?.key === key ? s.counts.counts : null;
}

export function pickSample(sample: SamplePick | null): void {
  qaqcStore.set(sample ? { sample, highlight: sample.batch ? { lab: sample.lab, batch: sample.batch } : null } : { sample: null });
}

export function setHighlight(highlight: Highlight | null): void {
  qaqcStore.set({ highlight });
}

/** The records or the setup changed: the subtabs compute again. */
export function qaqcChanged(): void {
  qaqcStore.set((s) => ({ rev: s.rev + 1, sample: null }));
}

export function reportsChanged(): void {
  qaqcStore.set((s) => ({ reportsRev: s.reportsRev + 1 }));
}

/** Another project: the tab starts again on its subtab (the type of duplicates is of the person — it stays). */
export function resetQaqc(): void {
  dropRequests();
  const { section, dupKind, rev, reportsRev } = qaqcStore.get();
  qaqcStore.set({ ...initialQaqc(), section, dupKind, rev: rev + 1, reportsRev: reportsRev + 1 });
}
