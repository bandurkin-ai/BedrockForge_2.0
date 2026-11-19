/**
 * The state of the string editor seen by the rest of the window (ADR 0015 §5): the session open now (one at a
 * time), whether a save is under way, the notes of the snap panel. The layer of the session is hidden in the
 * scene while it is edited (`scene/linesSource.ts`): the editing layer shows the working copy. Pure.
 */
import type { EditSession } from "../../edit/session";
import { Store } from "../../shell/store";

export interface EditState {
  session: EditSession | null;
  /** grows with every new session object (a reload makes a new one) */
  generation: number;
  saving: boolean;
  /** what the snap does not take, for the snap panel: big layers, clouds, sets cut to collars */
  snapNotes: string[];
  /** the last pointer press was in the 3D window or on the editor panel: the keys of the editor work */
  focused: boolean;
}

export const editStore = new Store<EditState>({ session: null, generation: 0, saving: false, snapNotes: [], focused: false });

/** The layer under edit, or null. */
export const editedLayer = (): number | null => editStore.get().session?.layerId ?? null;

/** Is a layer shown in the scene: not hidden by its key and not the one being edited. */
export function layerShown(id: number, hidden: ReadonlySet<string>, edited: number | null): boolean {
  return id !== edited && !hidden.has(`ln:${id}`);
}
