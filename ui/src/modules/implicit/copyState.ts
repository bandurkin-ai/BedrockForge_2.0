/** What the window «В каркас» knows (ТЗ 8.9.4; ADR 0026 решение 24): the model, the output chosen to be copied, the name of the copy. No React. */
import { Store } from "../../shell/store";

export const COPY_WINDOW = "implicit-to-wireframe";
export const COPY_TITLE = "В каркас";

export interface CopyState {
  modelId: number | null;
  /** the wireframe-output chosen */
  wireframeId: number | null;
  name: string;
  busy: boolean;
  error: string | null;
}

const fresh = (): CopyState => ({ modelId: null, wireframeId: null, name: "", busy: false, error: null });

export const copyStore = new Store<CopyState>(fresh());

export const resetCopy = (): void => copyStore.set(fresh());
