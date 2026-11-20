/**
 * Block models of the open project (ADR 0017 §3) for the section «Блочные модели» and «Свойства»: the list
 * (`GET /blocks`) and the details of the models «Свойства» showed (`GET /blocks/{id}`). Read when the project
 * opens, on `objects.changed {kind: "block_model"}` and after an import; a read overtaken by a newer one is
 * dropped. Rename and delete carry the version and read the list again — after a 409 too. No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { deleteBlockModelApi, getBlockModel, listBlockModels, patchBlockModel, type BlockModelInfo } from "./api";

export interface BlocksState {
  list: BlockModelInfo[];
  loaded: boolean;
  error: string | null;
  /** `GET /blocks/{id}` of the models «Свойства» showed */
  details: Record<number, BlockModelInfo>;
  detailErrors: Record<number, string>;
}

const EMPTY: BlocksState = { list: [], loaded: false, error: null, details: {}, detailErrors: {} };

export const blocksStore = new Store<BlocksState>(EMPTY);

export const blockById = (id: number): BlockModelInfo | undefined => blocksStore.get().list.find((b) => b.id === id);

export function clearBlocks(): void {
  blocksStore.set(EMPTY);
}

let seq = 0;

/** Reads the list; the details of models that are gone are dropped, of changed ones read again. */
export async function loadBlocks(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) return clearBlocks();
  try {
    const found = await listBlockModels();
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = blocksStore.get();
    const list = JSON.stringify(found) === JSON.stringify(cur.list) ? cur.list : found;
    const details: Record<number, BlockModelInfo> = {};
    const stale: number[] = [];
    for (const b of list) {
      const d = cur.details[b.id];
      if (!d) continue;
      if (d.version === b.version) details[b.id] = d;
      else stale.push(b.id);
    }
    blocksStore.set({ list, loaded: true, error: null, details });
    for (const id of stale) void loadBlockDetails(id);
  } catch (e) {
    if (mine !== seq) return;
    blocksStore.set({ loaded: true, error: errorText(e) });
  }
}

const detailSeq = new Map<number, number>();

/** «Свойства» of a model: its grid with the steps, the fields with their statistics, the source. */
export async function loadBlockDetails(id: number): Promise<void> {
  const mine = (detailSeq.get(id) ?? 0) + 1;
  detailSeq.set(id, mine);
  const uid = projectStore.get().current?.uid ?? null;
  try {
    const info = await getBlockModel(id);
    if (detailSeq.get(id) !== mine || projectStore.get().current?.uid !== uid) return;
    blocksStore.set((s) => {
      const detailErrors = { ...s.detailErrors };
      delete detailErrors[id];
      return { details: { ...s.details, [id]: info }, detailErrors };
    });
  } catch (e) {
    if (detailSeq.get(id) !== mine) return;
    blocksStore.set((s) => ({ detailErrors: { ...s.detailErrors, [id]: errorText(e) } }));
  }
}

/** What an `objects.changed` event means for the list: true — read it again. */
export function isBlocksEvent(data: string): boolean {
  try {
    const kind = (JSON.parse(data) as { kind?: unknown }).kind;
    return kind === undefined || kind === "block_model";
  } catch {
    return true; // an event without a body: whatever changed, read again
  }
}

function current(id: number): BlockModelInfo {
  const b = blockById(id);
  if (!b) throw new Error("Блочная модель не найдена: обновите дерево проекта");
  return b;
}

async function change<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } finally {
    await loadBlocks(); // after a 409 too: the next try goes with the new version
  }
}

/** `object.rename` of a block model; a name in use — 409 `name_taken` with the hint of the core. */
export function renameBlockModel(id: number, name: string): Promise<BlockModelInfo> {
  const b = current(id);
  return change(() => patchBlockModel(id, b.version, name));
}

export async function deleteBlockModel(id: number): Promise<void> {
  current(id);
  await change(() => deleteBlockModelApi(id));
  blocksStore.set((s) => {
    const details = { ...s.details };
    delete details[id];
    return { details };
  });
}
