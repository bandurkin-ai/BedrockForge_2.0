/**
 * The handlers of the commands of the implicit modelling (ТЗ 8.9.4; ADR 0026; docs/progress/e19-api.md §12) and the window of the
 * wizard «Новая модель»: without arguments a command opens its window, with them it asks the core (ADR 0021 п. 9).
 * - `implicit.new` — the wizard (the arguments prefill it); with a boundary (`box` or `wireframe_id`) — `POST /models`, an empty model;
 * - `implicit.domains` — the wizard of the model on the step of the domains; with `codes` — `POST /models/{id}/groups`, a domain last;
 * - `implicit.save` — the form of the wizard is written (the buttons of the wizard run this command: the registry, the log, the assistant);
 * - `implicit.update` — «Построить» / «Обновить» (`run.ts`).
 * The list of the models follows the project: read when it opens and on `implicit.changed`; another project — a clean wizard. The
 * groups, «В каркас» and «Удалить» (19.24, `groupCommands.ts`) are bound here too; the rest says «появится на этапе» until its window (19.25–19.26). One app at a time (the tests create several).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { addGroup, createModel, refusalOf } from "./api";
import { bindGroupCommands } from "./groupCommands";
import { forgetGroups, forgetMissing, isGroupWindow } from "./groupKinds";
import { bodyOf, boundaryGiven, EMPTY_FORM, formOfArgs, formOfMeta, freeName } from "./form";
import { runBuild } from "./run";
import { runShow } from "./showCommand";
import { followModels, forgetView } from "./viewState";
import { onScene } from "../scene/sceneHost";
import { clearImplicit, implicitStore, isImplicitEvent, loadDetails, loadRows, modelById, pickModel } from "./state";
import { ExpandForm } from "./ExpandForm";
import { ShellsForm } from "./ShellsForm";
import { ToWireframe } from "./ToWireframe";
import { VeinForm } from "./VeinForm";
import { Wizard } from "./Wizard";
import { editTitle, ensureDefaults, forgetDefaults, resetWizard, saveWizard, startEditModel, startNew, TITLE, WIZARD_WINDOW, wizardStore } from "./wizardState";
import { isIssue } from "../estimation/formBase";

/** The window of the wizard, if open. */
export const wizardWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === WIZARD_WINDOW)?.id ?? null;

const NO_MODEL = (id: number) => refusal("model_not_found", `Неявная модель №${id} не найдена.`, "Номера моделей — в разделе «Неявные модели» дерева.");

function showWizard(title: string): void {
  const open = wizardWindow();
  if (open) {
    focusWindow(open);
    updateWindow(open, { title });
  } else openWindow(WIZARD_WINDOW, title, "normal", {}, ...viewport());
}

/** Is a form with changes the person has not saved about to be replaced (by another model)? */
async function confirmDiscard(source: CommandSource): Promise<boolean> {
  const s = wizardStore.get();
  if (!s.dirty || !wizardWindow() || source !== "user") return true;
  return askConfirm({ title: TITLE, message: "В мастере есть несохранённые изменения. Заменить их другой моделью?", okLabel: "Заменить", danger: true });
}

/** `implicit.new` without a boundary: the wizard of a new model, the arguments (the name, the step) in the form. */
export async function openNew(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const s = wizardStore.get();
  const open = wizardWindow();
  // the open wizard of a new model keeps what is typed; another model's form (or none) is replaced
  const keep = open !== null && s.editing === null;
  if (!keep && !(await confirmDiscard(source))) return "Отменено";
  const f = formOfArgs(a, keep ? s.form : EMPTY_FORM);
  if (isIssue(f)) throw refusal(f.code, f.message, f.hint);
  if (keep) wizardStore.set({ form: f, dirty: s.dirty || Object.keys(a).length > 0 });
  else startNew(f);
  void ensureDefaults();
  showWizard(TITLE);
  return `Открыт мастер «${TITLE}»`;
}

/** `implicit.new {box | wireframe_id, …}`: a model with that boundary, nothing built. */
export async function createFromArgs(a: Record<string, unknown>): Promise<string> {
  const f = formOfArgs(a, EMPTY_FORM);
  if (isIssue(f)) throw refusal(f.code, f.message, f.hint);
  const defaults = await ensureDefaults();
  const built = bodyOf(f, defaults);
  if (!built.ok) throw refusal(built.issue.code, built.issue.message, built.issue.hint);
  try {
    const saved = await createModel(built.body);
    await loadRows();
    return `Создана модель «${saved.name}» (№${saved.id}): граница задана, групп нет. Добавьте домены (implicit.domains) и постройте (implicit.update).`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

/** The wizard of a stored model, on a step. */
export async function openEdit(id: number, step: number, source: CommandSource): Promise<string> {
  if (!modelById(id) && implicitStore.get().loaded) throw NO_MODEL(id);
  const s = wizardStore.get();
  if (!(s.editing?.id === id && wizardWindow())) {
    if (!(await confirmDiscard(source))) return "Отменено";
    await startEditModel(id, step).catch((e: unknown) => {
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    });
  } else wizardStore.set({ step });
  void ensureDefaults();
  showWizard(editTitle(wizardStore.get().form.name || modelById(id)?.name || `№${id}`));
  return `Открыт мастер модели «${wizardStore.get().form.name || `№${id}`}»`;
}

const codesOf = (text: unknown): string[] =>
  String(text ?? "")
    .split(/[;\n]+/)
    .map((c) => c.trim())
    .filter(Boolean);

/** `implicit.domains {model_id?, codes?, name?}` (§12). */
export async function runDomains(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = pickModel(a.model_id, app.store.get().selected);
  const codes = codesOf(a.codes);
  if (codes.length) {
    if (id === null) throw refusal("no_model", "Модель не выбрана.", "Передайте model_id или выберите модель в разделе «Неявные модели» дерева.");
    if (!modelById(id) && implicitStore.get().loaded) throw NO_MODEL(id);
    try {
      const info = await loadDetails(id);
      if (!info) throw refusal("project_changed", "Проект изменился.", "Повторите команду.");
      const groups = formOfMeta(info.name, info.meta).groups;
      const name = typeof a.name === "string" && a.name.trim() ? a.name.trim() : freeName(groups);
      const r = await addGroup(id, info.version, { name, type: "domain", codes });
      await loadRows();
      void loadDetails(id).catch(() => undefined);
      return `Домен «${r.group.name || name}» добавлен в модель «${info.name}» последним (самым старым): коды ${codes.join(", ")}. Версия ${r.version}; чтобы увидеть тело, выполните «Обновить».`;
    } catch (e) {
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    }
  }
  if (id !== null) return openEdit(id, 2, source);
  const rows = implicitStore.get().rows;
  if (rows.length > 1) throw refusal("no_model", "Выберите модель: в проекте их несколько.", "Выберите модель в разделе «Неявные модели» дерева или передайте model_id.");
  // none in the project: the domains are the second step of a new model
  const s = wizardStore.get();
  if (!(wizardWindow() && s.editing === null)) {
    if (!(await confirmDiscard(source))) return "Отменено";
    startNew(EMPTY_FORM, 2);
  } else wizardStore.set({ step: 2 });
  void ensureDefaults();
  showWizard(TITLE);
  return `Открыт мастер «${TITLE}» на шаге «Домены литологии»`;
}

/** `implicit.save {as_new?, build?}`: the form of the wizard is written (the same `saveWizard` as the buttons use). */
export async function runSave(a: Record<string, unknown>): Promise<string> {
  if (!wizardWindow()) throw refusal("no_wizard", "Нет открытой формы модели: сохранять нечего.", "Откройте мастер «Новая модель» (implicit.new) или модель из раздела «Неявные модели» дерева.");
  const saved = await saveWizard({ asNew: a.as_new === true });
  const head = `Сохранено: «${saved.name}», версия ${saved.version}`;
  if (a.build !== true) return head;
  return `${head}. ${await runBuild(saved.id)}`;
}

/** `implicit.update {model_id?}`: «Построить» / «Обновить». */
export async function runUpdate(app: App, a: Record<string, unknown>): Promise<string> {
  const id = pickModel(a.model_id, app.store.get().selected);
  if (id === null) throw refusal("no_model", "Модель не выбрана.", "Передайте model_id или выберите модель в разделе «Неявные модели» дерева.");
  return runBuild(id);
}

// ---- the registry ----

let unbind: (() => void)[] = [];

export function bindImplicit(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind(
    "implicit.new",
    noted(app, (a, s) => (boundaryGiven(a) ? createFromArgs(a) : openNew(a, s))),
    needWritable(app),
  );
  app.registry.bind("implicit.domains", noted(app, (a, s) => runDomains(app, a, s)), needWritable(app));
  app.registry.bind("implicit.save", noted(app, (a) => runSave(a)), needWritable(app));
  app.registry.bind("implicit.update", noted(app, (a) => runUpdate(app, a)), needWritable(app));
  app.registry.bind("implicit.show", noted(app, (a) => runShow(app, a)), needProject); // the view only: a read-only project may show too
  bindGroupCommands(app); // the groups (19.24): shells, vein, expand; «В каркас»; «Удалить»
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadRows();
  unbind.push(
    bindImplicitScene(),
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      clearImplicit();
      forgetView();
      forgetGroups();
      resetWizard();
      forgetDefaults();
      if (cur) void loadRows();
    }),
    onCoreEvent((event, data) => {
      if (event === "implicit.changed" && projectStore.get().current && isImplicitEvent(data)) void loadRows();
    }),
    // a model that is gone leaves the wizard (its form stays as a new one: nothing to overwrite), the selection and the 3D
    implicitStore.subscribe(() => {
      forgetMissing();
      followModels();
      const sel = app.store.get().selected;
      if (sel?.kind === "implicit" && implicitStore.get().loaded && !modelById(sel.id)) app.store.set({ selected: null });
      const e = wizardStore.get().editing;
      if (implicitStore.get().loaded && e && !modelById(e.id)) wizardStore.set({ editing: null, saved: null, dirty: true });
    }),
  );
}

/** The data points and the ellipsoid of the models in the main 3D: a lazy chunk with three.js, attached to every new scene. */
export function bindImplicitScene(): () => void {
  let detach: (() => void) | null = null;
  let generation = 0;
  const off = onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./scene").then((m) => {
      if (mine === generation) detach = m.attachImplicitScene(scene);
    });
  });
  return () => {
    generation++;
    detach?.();
    detach = null;
    off();
  };
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of the implicit modelling. */
export function renderImplicitWindow(w: FloatWin): ReactNode | null {
  if (w.kind === WIZARD_WINDOW) return createElement(Wizard, { winId: w.id });
  if (!isGroupWindow(w)) return null;
  switch (w.kind) {
    case "implicit-shells":
      return createElement(ShellsForm, { winId: w.id });
    case "implicit-vein":
      return createElement(VeinForm, { winId: w.id });
    case "implicit-expand":
      return createElement(ExpandForm, { winId: w.id });
    default:
      return createElement(ToWireframe, { winId: w.id });
  }
}

