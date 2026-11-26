/** The runtimes of the three kinds of groups (`groupBase.ts`) and what the module does with all of them at once. No React. */
import { closeWindow, windowStore, type FloatWin } from "../../shell/windows";
import { COPY_WINDOW, copyStore, resetCopy } from "./copyState";
import { createGroupKind, type GroupRuntime } from "./groupBase";
import { expandSpec } from "./ExpandState";
import { faults } from "./FaultState";
import { shellSpec } from "./ShellsState";
import { forgetStack, forgetStackMissing, STACK_WINDOW } from "./StackState";
import { implicitStore, modelById } from "./state";
import { veinSpec } from "./VeinState";
import { veinSystems } from "./VeinSystemState";

export const shells = createGroupKind(shellSpec);
export const veins = createGroupKind(veinSpec);
export const expands = createGroupKind(expandSpec);
export { faults, veinSystems };

/** Every kind (the types differ, the lifetime does not). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const KINDS: readonly GroupRuntime<any>[] = [shells, veins, expands, faults, veinSystems];

export const GROUP_WINDOWS: readonly string[] = [...KINDS.map((k) => k.spec.window), STACK_WINDOW];

export const isGroupWindow = (w: FloatWin): boolean => GROUP_WINDOWS.includes(w.kind) || w.kind === COPY_WINDOW;

/** A model is gone: its windows go (`modelId` omitted — another project: all of them). */
export function forgetGroups(modelId?: number): void {
  for (const k of KINDS) k.forget(modelId);
  forgetStack(modelId);
  const cp = copyStore.get();
  if (modelId === undefined || cp.modelId === modelId) {
    const win = windowStore.get().windows.find((w) => w.kind === COPY_WINDOW)?.id;
    if (win) closeWindow(win);
    resetCopy();
  }
}

/** The list of the models was read: the windows of a model that is not in it go. */
export function forgetMissing(): void {
  if (!implicitStore.get().loaded) return;
  for (const k of KINDS) {
    const id = k.store.get().modelId;
    if (id !== null && !modelById(id)) k.forget(id);
  }
  forgetStackMissing();
  const cid = copyStore.get().modelId;
  if (cid !== null && !modelById(cid)) forgetGroups(cid);
}
