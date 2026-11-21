/**
 * The state of «Настройка QAQC → Данные» outside React (ADR 0020 §2, §11): the list of the data objects of the
 * project (rows of «Аудит и статистика» read the same list), the import job followed in the window, the result
 * with its counters, a refusal of the core at its field, the import form asked for by `qaqc.import`.
 */
import { errorText } from "../../../shell/props";
import { Store } from "../../../shell/store";
import { loadData, type ImportCounts, type QaqcData, type Started } from "../setupApi";
import { errorPlace } from "./mapping";

export interface ImportRun {
  job: Started;
  name: string;
  replaceId: number | null;
}

export interface ImportDone {
  dataId: number | null;
  name: string;
  rows: number;
  counts: ImportCounts;
}

/** What the window shows instead of the list: the import form (from a file or from a table of a set). */
export interface FormRequest {
  mode: "file" | "table";
  /** a file given by the command: read at once */
  path?: string | null;
  setId?: number | null;
  table?: string | null;
  replaceId?: number | null;
  /** a new request opens the form afresh */
  seq: number;
}

export interface DataState {
  list: QaqcData[];
  loaded: boolean;
  error: string | null;
  run: ImportRun | null;
  done: ImportDone | null;
  /** a refusal of the last import: the field it belongs to (null — the whole form) */
  refused: { at: string | null; message: string } | null;
  form: FormRequest | null;
}

const EMPTY: DataState = { list: [], loaded: false, error: null, run: null, done: null, refused: null, form: null };
export const dataStore = new Store<DataState>({ ...EMPTY });

let seq = 0;
let reads = 0;

export function resetData(): void {
  reads++;
  dataStore.set({ ...EMPTY });
}

/** Reads the list again; only the newest read is applied. */
export async function reloadData(): Promise<QaqcData[]> {
  const mine = ++reads;
  try {
    const list = await loadData();
    if (mine === reads) dataStore.set({ list, loaded: true, error: null });
    return list;
  } catch (e) {
    if (mine === reads) dataStore.set({ loaded: true, error: errorText(e) });
    return dataStore.get().list;
  }
}

export const findData = (id: number): QaqcData | undefined => dataStore.get().list.find((d) => d.id === id);

export function upsertData(row: QaqcData): void {
  dataStore.set((s) => ({ list: s.list.some((d) => d.id === row.id) ? s.list.map((d) => (d.id === row.id ? row : d)) : [...s.list, row] }));
}

export function dropData(id: number): void {
  dataStore.set((s) => ({ list: s.list.filter((d) => d.id !== id) }));
}

export const dataByName = (list: readonly QaqcData[]): QaqcData[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));

export function openForm(req: Omit<FormRequest, "seq">): void {
  dataStore.set({ form: { ...req, seq: ++seq }, refused: null });
}

export function closeForm(): void {
  dataStore.set({ form: null, refused: null });
}

export function startedRun(run: ImportRun): void {
  dataStore.set({ run, done: null, refused: null, form: null });
}

/** The job is over: its totals (null — failed or cancelled; the window shows the job's own state). */
export function finishRun(done: ImportDone | null): void {
  dataStore.set({ done });
}

/** «К списку»: the window shows the list again. */
export function dismissRun(): void {
  dataStore.set({ run: null, done: null });
}

/** A refusal of the core shown at its field of the form (the form stays open). */
export function refuseImport(e: unknown): void {
  dataStore.set({ refused: { at: errorPlace(e), message: errorText(e) } });
}

