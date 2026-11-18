/** Runs the actions of a tree row (`treeActions.ts`): menu items with the reason of an unavailable command, own
 * confirmation before a command, a message when it failed, and «Свойства» of the right dock. Rename and delete
 * are registry commands (`object.rename`, `object.delete`): they ask in their own windows. No React. */
import type { App } from "./app";
import type { MenuItem } from "./ContextMenu";
import { askConfirm, showMessage } from "./dialogs";
import type { TreeAction } from "./treeActions";

export type LocalAction = NonNullable<TreeAction["local"]>;

/** Runs a command of a row; a failure is shown (the tree has no other place for it). */
export async function runAction(app: App, a: TreeAction, local: (kind: LocalAction) => void): Promise<void> {
  if (a.local) return local(a.local);
  if (!a.command) return;
  if (a.confirm && !(await askConfirm(a.confirm))) return;
  const r = await app.registry.execute(a.command, a.args ?? {});
  if (!r.ok) await showMessage({ title: a.label.replace(/…$/, ""), message: r.result });
}

export function menuItems(app: App, actions: readonly TreeAction[], local: (kind: LocalAction) => void): MenuItem[] {
  return actions.map((a) =>
    a.separator
      ? { separator: true, label: "" }
      : { label: a.label, reason: a.command ? app.registry.reason(a.command) : null, run: () => void runAction(app, a, local) },
  );
}

/** Opens «Свойства» of the right dock unless it is showing already. */
export function showProperties(app: App): void {
  const w = app.store.get().workspace;
  if (!(w.right && w.rightTab === "properties")) void app.run("panel.set", { panel: "properties", open: true });
}

/** Select an object and show its properties: the click on a DTM, «Свойства» and «Настройки ЦТМ…» of the menus. */
export async function selectWithProperties(app: App, kind: string, id: number): Promise<void> {
  const r = await app.registry.execute("object.select", { kind, id });
  if (r.ok) showProperties(app);
}
