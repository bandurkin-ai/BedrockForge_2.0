/**
 * The window «Стек отношений» (Э19б; ТЗ 7.35 Д-24, Д-31; ADR 0026 решения 19–21): the domains of a model in the order of their age (the youngest on top;
 * by dragging or by the buttons «выше» / «ниже»), the relation of each to the older ones («Согласно», «Эрозия», «Налегание», «Интрузия»), the faults
 * that cut it («рассечена разломом»). The draft lives here; «Применить» runs the commands `implicit.relations` and then `implicit.group_faults` of every
 * domain whose faults changed — the registry, the log and the assistant see the same writes. No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { CoreError } from "../../shell/api";
import { refusal, viewport } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { closeWindow, focusWindow, openWindow, updateWindow, windowStore } from "../../shell/windows";
import { detailsOf } from "./groupBase";
import { dropped, draftOf, faultsChanged, moved, relationsChanged, type StackDraft } from "./stackModel";
import type { Relation } from "./stackApi";
import { implicitStore, loadDetails } from "./state";

export const STACK_WINDOW = "implicit-stack";
export const STACK_TITLE = "Стек отношений";

export interface StackWin {
  modelId: number | null;
  /** the version of the model the draft is made of */
  version: number | null;
  draft: StackDraft | null;
  saving: boolean;
  /** the words of the last refusal */
  error: string | null;
  saved: string | null;
  dirty: boolean;
  /** the domain being dragged */
  dragging: number | null;
}

const fresh = (): StackWin => ({ modelId: null, version: null, draft: null, saving: false, error: null, saved: null, dirty: false, dragging: null });
export const stackStore = new Store<StackWin>(fresh());

export const stackWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === STACK_WINDOW)?.id ?? null;
const titleOf = (name: string) => `${STACK_TITLE}: «${name}»`;

/** `implicit.relations` without values: the window of the model (what is typed stays when the same model is open). */
export async function openStack(id: number, source: CommandSource = "user"): Promise<string> {
  const info = await detailsOf(id);
  const s = stackStore.get();
  // a draft that is not written is not thrown away by opening ANOTHER model: the person is asked (the same model keeps what is typed)
  if (source === "user" && stackWindow() !== null && s.dirty && s.draft !== null && s.modelId !== id) {
    const ok = await askConfirm({ title: STACK_TITLE, message: "В окне есть изменения стека, которые не записаны. Заменить их стеком другой модели?", okLabel: "Заменить", danger: true });
    if (!ok) return "Отменено";
  }
  const keep = stackWindow() !== null && s.modelId === id && s.dirty && s.draft !== null;
  stackStore.set(keep ? { error: null } : { modelId: id, version: info.version, draft: draftOf(info.meta), saving: false, error: null, saved: null, dirty: false, dragging: null });
  const win = stackWindow();
  if (win) {
    focusWindow(win);
    updateWindow(win, { title: titleOf(info.name) });
  } else openWindow(STACK_WINDOW, titleOf(info.name), "normal", {}, ...viewport());
  return `Открыто окно «${STACK_TITLE}» модели «${info.name}»`;
}

function change(patch: (d: StackDraft) => StackDraft): void {
  stackStore.set((s) => (s.draft ? { draft: patch(s.draft), dirty: true, saved: null, error: null } : s));
}

export const setRelation = (group: number, relation: Relation): void => change((d) => ({ ...d, relations: { ...d.relations, [group]: relation } }));
export const moveDomain = (group: number, delta: number): void => change((d) => ({ ...d, order: moved(d.order, group, delta) }));
export const dropDomain = (group: number, before: number | null): void => change((d) => ({ ...d, order: dropped(d.order, group, before) }));
export const setDragging = (group: number | null): void => stackStore.set({ dragging: group });

export function toggleFault(group: number, fault: number, on: boolean): void {
  change((d) => {
    const have = d.faults[group] ?? [];
    const next = on ? [...have.filter((f) => f !== fault), fault] : have.filter((f) => f !== fault);
    return { ...d, faults: { ...d.faults, [group]: next.sort((a, b) => a - b) } };
  });
}

/** The draft read again from the model («Сбросить», «Перечитать»). */
export async function resetStack(): Promise<void> {
  const id = stackStore.get().modelId;
  if (id === null) return;
  const info = await loadDetails(id);
  if (info) stackStore.set({ version: info.version, draft: draftOf(info.meta), dirty: false, error: null, saved: null });
}

/** The window is closed, the model is gone, another project: nothing is kept. */
export function forgetStack(modelId?: number): void {
  const s = stackStore.get();
  if (modelId !== undefined && s.modelId !== modelId) return;
  const win = stackWindow();
  if (win) closeWindow(win);
  stackStore.set(fresh());
}

/** «Применить»: the relations and the order first, then the faults of each domain that changed. A refusal stays under the buttons. */
export async function applyStack(app: App): Promise<void> {
  const s = stackStore.get();
  if (s.modelId === null || !s.draft || s.saving) return;
  const id = s.modelId;
  const draft = s.draft;
  stackStore.set({ saving: true, error: null, saved: null });
  const lines: string[] = [];
  try {
    const info = await detailsOf(id);
    // the optimistic lock: the draft is of the version it was read at; a model changed meanwhile is not overwritten
    if (s.version !== null && info.version !== s.version) {
      throw refusal("version_conflict", "Модель изменена, перечитайте.", "Нажмите «Сбросить»: окно возьмёт стек модели как он сейчас; свои правки повторите.");
    }
    if (relationsChanged(info.meta, draft)) {
      const r = await app.registry.execute("implicit.relations", { model_id: id, order: draft.order.join("; "), relations: draft.order.map((g) => `${g}=${draft.relations[g]}`).join("; ") }, "user");
      if (!r.ok) throw refusal("not_written", r.result, "Окно оставлено как есть: поправьте стек и нажмите «Применить» ещё раз.");
      lines.push(r.result);
    }
    const now = await detailsOf(id);
    for (const g of faultsChanged(now.meta, draft)) {
      const r = await app.registry.execute("implicit.group_faults", { model_id: id, group_id: g, fault_ids: (draft.faults[g] ?? []).join("; ") }, "user");
      if (!r.ok) throw refusal("not_written", r.result, "Окно оставлено как есть: поправьте разломы и нажмите «Применить» ещё раз.");
      lines.push(r.result);
    }
    const after = (await loadDetails(id)) ?? now;
    stackStore.set({ version: after.version, draft: draftOf(after.meta), dirty: false, saved: lines.length ? `Записано. Чтобы пересчитать поверхности и тела, выполните «Обновить» (версия ${after.version}).` : "Стек не менялся: записывать нечего." });
  } catch (e) {
    const text = e instanceof CoreError ? `${e.message} ${e.hint ?? ""}`.trim() : e instanceof Error ? e.message : String(e);
    stackStore.set({ error: text });
    app.store.set({ statusNote: text });
  } finally {
    stackStore.set({ saving: false });
  }
}

/** A model that left the list takes its window with it. */
export function forgetStackMissing(): void {
  const id = stackStore.get().modelId;
  if (id !== null && implicitStore.get().loaded && !implicitStore.get().rows.some((r) => r.id === id)) forgetStack(id);
}
