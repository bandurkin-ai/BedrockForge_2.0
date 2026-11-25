/**
 * The handler of `blocks.settings` and the window «Блочная модель · …» of the shell (ТЗ 8.7.6; ADR 0023 §11, §13). The
 * command opens the window of the model of the call (the one selected in the tree, else the only one; the window of that
 * model, if open, comes to the front). One window per model. The window follows the project: a renamed model renames
 * its window, a model that is gone closes it, another project closes them all (the style belongs to the project). What
 * the window changes is the style of the model in the view of the project (`edits.ts`); the commands `blocks.show`,
 * `.filter`, `.quick`, `.style` write the same keys.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import { needProject, noted, viewport } from "../../../shell/objectRun";
import { closeWindow, focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { projectStore } from "../../projects/state";
import { modelOf } from "../showCommands";
import { blockById, blocksStore } from "../state";
import { ModelWindow } from "./ModelWindow";
import { isTab, MODEL_WINDOW, modelTitle, type Tab } from "./state";

const modelOfWindow = (w: FloatWin): number | null => (typeof w.props?.model === "number" ? w.props.model : null);

/** The window of a model, if open. */
export const windowOfModel = (id: number): string | null => windowStore.get().windows.find((w) => w.kind === MODEL_WINDOW && modelOfWindow(w) === id)?.id ?? null;

/** Opens the window of a model (to the front when open); `tab` is the tab it opens on. */
export function openModelWindow(id: number, tab?: Tab): string {
  const open = windowOfModel(id);
  if (open) {
    if (tab) updateWindow(open, { props: { model: id, tab } });
    focusWindow(open);
    return open;
  }
  return openWindow(MODEL_WINDOW, modelTitle(blockById(id)?.name ?? null), "form", { model: id, ...(tab ? { tab } : {}) }, ...viewport());
}

export async function runSettings(app: App, a: Record<string, unknown>): Promise<string> {
  const model = await modelOf(app, a);
  openModelWindow(model.id);
  return `Открыто окно «${modelTitle(model.name)}»`;
}

let unbind: (() => void)[] = [];

export function bindModelWindow(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("blocks.settings", noted(app, (a) => runSettings(app, a)), needProject);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  unbind.push(
    // another project: its windows are not ours (the style is of the project)
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      for (const w of windowStore.get().windows) if (w.kind === MODEL_WINDOW) closeWindow(w.id);
    }),
    // a model renamed renames its window, a model that is gone closes it
    blocksStore.subscribe(() => {
      if (!blocksStore.get().loaded) return;
      for (const w of windowStore.get().windows) {
        if (w.kind !== MODEL_WINDOW) continue;
        const id = modelOfWindow(w);
        const model = id === null ? undefined : blockById(id);
        if (!model) closeWindow(w.id);
        else if (w.title !== modelTitle(model.name)) updateWindow(w.id, { title: modelTitle(model.name) });
      }
    }),
  );
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderModelWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== MODEL_WINDOW) return null;
  const id = modelOfWindow(w);
  if (id === null) return null;
  return createElement(ModelWindow, { winId: w.id, modelId: id, tab: isTab(w.props?.tab) ? w.props.tab : undefined });
}
