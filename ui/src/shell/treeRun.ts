/** Runs the actions of a tree row (`treeActions.ts`): menu items with the reason of an unavailable command, own
 * confirmation before a command, a message when it failed, and «Свойства» of the right dock. No React. */
import type { App } from "./app";
import type { MenuItem } from "./ContextMenu";
import { askConfirm, askText, showMessage } from "./dialogs";
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

const errorText = (e: unknown): string => {
  const full = (e as { full?: unknown } | null)?.full; // CoreError: the message with its hint
  return typeof full === "string" ? full : e instanceof Error ? e.message : String(e);
};

/** «Переименовать…»: asks the new name in an own dialog, saves it, shows why it was not saved. */
export async function renameDialog(what: string, name: string, save: (name: string) => Promise<unknown>): Promise<void> {
  const next = await askText({ title: `Переименовать ${what}`, label: "Название", initial: name, okLabel: "Переименовать" });
  if (!next || next === name) return;
  try {
    await save(next);
  } catch (e) {
    await showMessage({ title: "Название не изменено", message: errorText(e) });
  }
}

/** «Удалить…»: asks in an own dialog, deletes, shows why it was not deleted. */
export async function deleteDialog(title: string, message: string, remove: () => Promise<unknown>): Promise<void> {
  if (!(await askConfirm({ title, message, okLabel: "Удалить", danger: true }))) return;
  try {
    await remove();
  } catch (e) {
    await showMessage({ title: "Объект не удалён", message: errorText(e) });
  }
}
