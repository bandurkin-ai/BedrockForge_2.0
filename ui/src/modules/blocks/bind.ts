/**
 * Block models follow the project (ADR 0017 §3): the list is read when a project opens, on `objects.changed`
 * of a block model and when an import ends; the details «Свойства» show are read again when their version
 * moved; a selected model that is gone is not selected any more. Rename, delete and select are `object.*`
 * (`wireframes/objectCommands.ts`, `wireframes/bind.ts`). One app at a time (the tests create several).
 */
import type { App } from "../../shell/app";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { blockById, blocksStore, clearBlocks, isBlocksEvent, loadBlocks } from "./state";

let unbind: (() => void)[] = [];

export function bindBlocks(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadBlocks();
  unbind.push(
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      clearBlocks();
      if (cur) void loadBlocks();
    }),
    onCoreEvent((event, data) => {
      if (event === "objects.changed" && projectStore.get().current && isBlocksEvent(data)) void loadBlocks();
    }),
    onJobFinished((job) => {
      if (job.status === "done" && job.kind.startsWith("import.")) void loadBlocks();
    }),
    blocksStore.subscribe(() => {
      const s = app.store.get().selected;
      if (s?.kind === "blocks" && blocksStore.get().loaded && !blockById(s.id)) app.store.set({ selected: null });
    }),
  );
}
