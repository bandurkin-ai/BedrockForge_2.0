/**
 * Handlers of `filters.manage`, `filter.save`, `filter.delete`, `filter.test`, `filter.usage` (ТЗ 7.35 Д-02; ADR 0023
 * §8, §13) and the window «Фильтры» of the shell. `filters.manage` — the window (one per project; `filter_id` puts the
 * filter on edit); `filter.save` without a name and rules opens it; with them the body is checked first (a wrong text
 * or tree never reaches the core: the words and the place of 8.7.4), then `POST` / `PUT /filters` over the version read
 * — another version stored (409) is asked in our own window: «Сохранить как новый» or «Перечитать»; a filter used by
 * a consumer is changed after a question. `filter.delete` — the consumers are read first: a used filter is not
 * deleted (the list in our own window; the 422 `filter_in_use` of the core says the same), else a question; the
 * assistant is asked by the registry (`confirm`). `filter.test` — «Проверить на модели» (422 `not_ready` is said as it
 * is until the restrictor of 17.8). The list follows `objects.changed {kind: "filter"}`.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import { CoreError } from "../../../shell/api";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm, showMessage } from "../../../shell/dialogs";
import { needProject, needWritable, noted, refusal, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { onCoreEvent } from "../../jobs/state";
import { nextName } from "../../composites/api";
import { projectStore } from "../../projects/state";
import { blockById } from "../state";
import {
  createFilter,
  deleteFilter,
  filterUsage,
  loadFilter,
  loadFilters,
  testInline,
  testRefusal,
  testStored,
  testText,
  updateFilter,
  usageOfError,
  usageText,
  useText,
  type Filter,
  type FilterBody,
  type FilterUse,
} from "./api";
import { parseExpr } from "./expr";
import type { RuleIssue, Rule } from "./constructor";
import { FiltersWindow } from "./FiltersWindow";
import { bodyOfEdit, currentEdit, dropRow, editOf, FILTERS_WINDOW, filtersStore, findFilter, forgetEdit, newEdit, resetFilters, selectFilter, setEdit, setRows, upsertRow, type TestNote } from "./state";

export const filtersTitle = (project: string | null): string => (project ? `Фильтры · ${project}` : "Фильтры");

const given = (a: Record<string, unknown>, k: string) => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

export async function refreshFilters(): Promise<void> {
  setRows(await loadFilters());
}

const notFound = (id: unknown) => refusal("filter_not_found", `Фильтр №${String(id)} не найден.`, "Номера фильтров — в дереве, раздел «Фильтры».");

/** The filter of the arguments from the core (404 — not found in Russian); the row of the list is brought up to date. */
export async function filterOf(id: unknown): Promise<Filter> {
  if (typeof id !== "number" || !Number.isInteger(id)) throw notFound(id);
  try {
    const f = await loadFilter(id);
    upsertRow(f);
    return f;
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) throw notFound(id);
    throw e;
  }
}

/** The window «Фильтры», if open. */
export const windowOfFilters = (): string | null => windowStore.get().windows.find((w) => w.kind === FILTERS_WINDOW)?.id ?? null;

/** Opens the window (focused when open); `edit` puts a stored filter into its form, null — a new one, undefined — as it is. */
export function openFiltersWindow(app: App, edit?: Filter | null): string {
  const open = windowOfFilters();
  const modelId = open ? (currentEdit()?.modelId ?? null) : null;
  let win = open;
  if (open) focusWindow(open);
  else win = openWindow(FILTERS_WINDOW, filtersTitle(app.store.get().projectName), "normal", {}, ...viewport());
  if (edit !== undefined) filtersStore.set({ edit: edit ? editOf(edit, { modelId }) : newEdit({ modelId }), selected: edit?.id ?? null });
  else if (!open) filtersStore.set({ edit: newEdit() });
  return win as string;
}

/** Asked when a window with unsaved changes is about to take another filter (or a new one) from a person. */
export const REPLACE_QUESTION = { title: "Фильтры", message: "В окне есть несохранённые изменения фильтра. Открыть другой фильтр без сохранения?", okLabel: "Открыть без сохранения", danger: true };

/** false — the person kept the unsaved form of the open window (nothing is replaced); the assistant is never asked. */
async function mayReplace(source: CommandSource): Promise<boolean> {
  if (source !== "user") return true;
  const e = windowOfFilters() ? currentEdit() : null;
  return !e?.dirty || (await askConfirm(REPLACE_QUESTION));
}

/** A filter put on edit in the window (the consumers are read when the person asks). */
export async function editFilter(app: App, id: number): Promise<Filter> {
  const f = await filterOf(id);
  openFiltersWindow(app, f);
  return f;
}

/** The last filter saved by `filter.save` (the window binds itself to a new one). */
let saved: { id: number; name: string } | null = null;
export const lastSaved = (): { id: number; name: string } | null => saved;

/** After a write: the list read again, the window shows what is stored when it edits that filter (or a new one). */
async function afterWrite(f: Filter, keepEdit: boolean): Promise<void> {
  upsertRow(f);
  await refreshFilters().catch(() => undefined);
  const e = windowOfFilters() ? currentEdit() : null;
  if (e && (e.filterId === f.id || (e.filterId === null && keepEdit))) {
    setEdit(editOf(f, { mode: e.mode, modelId: e.modelId }));
    selectFilter(f.id);
  }
}

/** A refusal of the core that names a place of the tree marks that node in the window (the words stay the core's). */
function markRefusal(e: unknown): void {
  if (!(e instanceof CoreError) || typeof e.body?.param !== "string" || !e.body.param.startsWith("rules") || !windowOfFilters()) return;
  const expr = e.code === "expr_syntax" ? (e.body as unknown as RuleIssue["expr"]) : undefined;
  setEdit({ issue: { param: e.body.param, message: e.message, hint: e.hint, ...(expr ? { expr } : {}) } });
}

/** A filter saved elsewhere: the window not edited takes what is stored; an edited one keeps the version it read — its save is then a 409. */
export async function followClean(): Promise<void> {
  const e = currentEdit();
  if (!e || e.filterId === null || e.dirty) return;
  const row = findFilter(e.filterId);
  if (row && row.version !== e.version) setEdit(editOf(await loadFilter(e.filterId), { mode: e.mode, modelId: e.modelId }));
}

export const conflictQuestion = (name: string) => ({
  title: "Фильтр изменён в другом окне",
  message: `Фильтр «${name}» изменили в другом окне, пока вы его правили. Сохранить фильтр этого окна как новый? Иначе — перечитать сохранённый.`,
  okLabel: "Сохранить как новый",
});
export const rereadQuestion = (name: string) => ({ title: "Перечитать фильтр", message: `Перечитать фильтр «${name}» из проекта? Правки этого окна будут заменены.`, okLabel: "Перечитать", danger: true });
export const usedQuestion = (name: string, list: readonly FilterUse[]) => `Фильтр «${name}» используется: ${usageText(list)}. Изменённые правила получат и эти места. Изменить фильтр?`;

/** «Сохранить как новый» after a conflict (and «Копировать»): the body with the next free name. */
async function createAsNew(body: FilterBody, name: string): Promise<Filter> {
  let want = nextName(body.name ?? name);
  for (let i = 0; ; i++) {
    try {
      return await createFilter({ ...body, name: want });
    } catch (e) {
      if (i >= 50 || !(e instanceof CoreError) || e.code !== "name_taken") throw e;
      want = nextName(want);
    }
  }
}

/** The body of the arguments (what is given): `expr` or `rules` (JSON), name, note, folder; a wrong one — a refusal in Russian. */
export function bodyOfArgs(a: Record<string, unknown>): { ok: true; body: FilterBody } | { ok: false; error: CoreError } {
  const body: FilterBody = {};
  if (given(a, "name")) body.name = String(a.name).trim();
  if ("note" in a && a.note !== undefined) body.note = typeof a.note === "string" && a.note.trim() ? a.note.trim() : null;
  if (given(a, "folder")) body.folder = String(a.folder);
  if (given(a, "expr") && given(a, "rules")) return { ok: false, error: refusal("filter_rules", "Фильтр задаётся деревом правил или выражением, не обоими сразу.", "Передайте только rules или только expr.") };
  if (given(a, "expr")) {
    const text = String(a.expr);
    const p = parseExpr(text);
    if (!p.ok) return { ok: false, error: refusal("expr_syntax", p.issue.message, p.issue.hint) };
    body.expr = text;
  } else if (given(a, "rules")) {
    try {
      const parsed: unknown = typeof a.rules === "string" ? JSON.parse(a.rules) : a.rules;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not a tree");
      body.rules = parsed as Rule;
    } catch {
      return { ok: false, error: refusal("filter_rules", "Дерево правил — не JSON.", 'Пример: {"op": "and", "items": [{"cond": {"field": "AU", "op": ">", "value": 1}}]}.') };
    }
  }
  return { ok: true, body };
}

/** «AU > 1 AND DOMAIN = 2» or the Russian title of a tree with a wireframe — what the log and Claude read of a filter. */
export const shapeText = (f: Filter): string => f.text ?? f.title;

export async function runSave(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.filter_id;
  const hasRules = given(a, "expr") || given(a, "rules");
  if (id === undefined && !hasRules && !given(a, "name")) {
    if (!(await mayReplace(source))) return "Отменено";
    openFiltersWindow(app, null);
    return "Открыто окно «Фильтры»: новый фильтр";
  }
  const got = bodyOfArgs(a);
  if (!got.ok) throw got.error;
  const body = got.body;
  if (id !== undefined) {
    const f = await filterOf(id);
    if (!Object.keys(body).length) {
      if (!(await mayReplace(source))) return "Отменено";
      openFiltersWindow(app, f);
      return `Открыто окно «Фильтры»: «${f.name}»`;
    }
    if (source === "user" && hasRules) {
      const list = await filterUsage(f.id).catch(() => []);
      if (list.length && !(await askConfirm({ title: "Изменить фильтр", message: usedQuestion(f.name, list), okLabel: "Изменить", danger: true }))) return "Отменено";
    }
    const expected = typeof a.expected_version === "number" ? a.expected_version : f.version;
    let out: Filter;
    try {
      out = await updateFilter(f.id, body, expected);
    } catch (e) {
      markRefusal(e);
      if (source !== "user" || !(e instanceof CoreError) || e.code !== "version_conflict") throw e;
      // another window saved the filter meanwhile: «Сохранить как новый» / «Перечитать» (ТЗ раздел 17 п. 11)
      if (!(await askConfirm(conflictQuestion(f.name)))) {
        if (await askConfirm(rereadQuestion(f.name))) {
          const now = await loadFilter(f.id);
          setEdit(editOf(now, { mode: currentEdit()?.mode, modelId: currentEdit()?.modelId ?? null }));
          await afterWrite(now, false);
          return `Фильтр «${now.name}» перечитан из проекта`;
        }
        return "Отменено";
      }
      const made = await createAsNew({ ...body, ...(body.name ? {} : { name: f.name }) }, f.name);
      saved = { id: made.id, name: made.name };
      await afterWrite(made, true);
      return `Фильтр «${made.name}» сохранён как новый (№${made.id})`;
    }
    saved = { id: out.id, name: out.name };
    await afterWrite(out, false);
    return `Фильтр «${out.name}» изменён: ${shapeText(out)}`;
  }
  if (!given(a, "name")) throw refusal("name_empty", "У фильтра нет имени.", "Передайте name, например «Руда выше борта».");
  if (!hasRules) throw refusal("filter_rules", "У фильтра нет правил.", "Задайте expr (например AU > 1) или rules.");
  let out: Filter;
  try {
    out = await createFilter(body);
  } catch (e) {
    markRefusal(e);
    throw e;
  }
  saved = { id: out.id, name: out.name };
  await afterWrite(out, true);
  return `Фильтр «${out.name}» сохранён (№${out.id}): ${shapeText(out)}`;
}

/** The consumers of a refusal (or of the read) told in our own window. */
export async function tellUsed(name: string, list: readonly FilterUse[]): Promise<void> {
  await showMessage({
    title: "Фильтр используется",
    message: `Фильтр «${name}» не удалён — он выбран здесь:\n${list.map(useText).join("\n")}\n\nВыберите в них другой фильтр или удалите их, затем удалите фильтр.`,
  });
}

export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const f = await filterOf(a.filter_id);
  if (source === "user") {
    const list = await filterUsage(f.id);
    if (list.length) {
      await tellUsed(f.name, list);
      return `Фильтр «${f.name}» используется: ${usageText(list)} — не удалён`;
    }
    if (!(await askConfirm({ title: "Удалить фильтр", message: `Удалить фильтр «${f.name}»?`, okLabel: "Удалить", danger: true }))) return "Отменено";
  }
  try {
    await deleteFilter(f.id);
  } catch (e) {
    const list = usageOfError(e);
    if (!list.length) throw e;
    if (source === "user") await tellUsed(f.name, list);
    throw new Error(`Фильтр «${f.name}» используется: ${usageText(list, 20)} — не удалён. ${e instanceof CoreError ? e.hint : ""}`.trim());
  }
  forgetFilter(f.id);
  return `Фильтр «${f.name}» удалён`;
}

/** A filter gone: out of the list, the selection and the window (its form is emptied). */
export function forgetFilter(id: number): void {
  const wasEdited = currentEdit()?.filterId === id;
  dropRow(id);
  if (wasEdited) filtersStore.set({ edit: newEdit() });
}

/** The last answer of `filter.test`, classified (the window shows it; a refusal is told by its code, not by its words). */
let lastTest: TestNote | null = null;
export const lastTestNote = (): TestNote | null => lastTest;

/** «Проверить на модели»: the stored filter on the model; the result — text (the window shows the same). */
export async function runTest(a: Record<string, unknown>): Promise<string> {
  lastTest = null;
  const f = await filterOf(a.filter_id);
  const model = typeof a.model_id === "number" ? a.model_id : NaN;
  const name = blockById(model)?.name ?? `№${String(a.model_id)}`;
  try {
    const r = await testStored(f.id, model);
    if (!r) throw refusal("bad_answer", "Ядро ответило на проверку без чисел.", "Повторите проверку.");
    lastTest = { kind: "ok", text: testText(name, r) };
    return `Фильтр «${f.name}»: ${lastTest.text}`;
  } catch (e) {
    const t = testRefusal(e);
    lastTest = { kind: t.notReady ? "notice" : "error", text: t.text };
    throw e;
  }
}

export async function runUsage(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const f = await filterOf(a.filter_id);
  const list = await filterUsage(f.id);
  const text = list.length ? `Фильтр «${f.name}» используется (${list.length}): ${list.map(useText).join("; ")}` : `Фильтр «${f.name}» нигде не используется — его можно удалить`;
  if (source === "user") void showMessage({ title: `Где используется «${f.name}»`, message: list.length ? `${list.map(useText).join("\n")}\n\nПока ссылки есть, фильтр не удаляется.` : "Фильтр нигде не используется: он не выбран ни в виде блочной модели, ни в объектах проекта." });
  return text;
}

// ---- what the window does (through the registry, so every step is in the log) ----

/** «Сохранить» of the window: `filter.save` with its form; the window binds to the filter made. A wrong form — its text (null — saved). */
export async function saveWindow(app: App): Promise<string | null> {
  const e = currentEdit();
  if (!e) return "Окно фильтров не готово";
  const got = bodyOfEdit(e);
  if (!got.ok) {
    setEdit({ issue: got.issue });
    return got.error;
  }
  setEdit({ issue: null });
  saved = null;
  const args: Record<string, unknown> = { name: got.body.name, rules: JSON.stringify(got.body.rules), note: got.body.note ?? "" };
  if (e.filterId !== null) {
    args.filter_id = e.filterId;
    if (e.version !== null) args.expected_version = e.version;
  }
  const r = await app.registry.execute("filter.save", args, "user");
  return r.ok ? null : r.result;
}

/** «Копировать»: the form of the window as a new filter with the next free name. */
export async function copyWindow(app: App): Promise<string | null> {
  const e = currentEdit();
  if (!e) return "Окно фильтров не готово";
  const got = bodyOfEdit(e);
  if (!got.ok) return got.error;
  saved = null;
  const r = await app.registry.execute("filter.save", { name: nextName(got.body.name), rules: JSON.stringify(got.body.rules), note: got.body.note ?? "" }, "user");
  return r.ok ? null : r.result;
}

/** «Проверить на модели» of the window: the stored clean filter — `filter.test`; a new or changed one — its form as it is (`/filters/test`). */
export async function testWindow(app: App): Promise<TestNote> {
  const e = currentEdit();
  if (!e) return { kind: "error", text: "Окно фильтров не готово" };
  if (e.modelId === null) return { kind: "error", text: "Выберите блочную модель для проверки." };
  const model = blockById(e.modelId)?.name ?? `№${e.modelId}`;
  if (e.filterId !== null && !e.dirty) {
    lastTest = null;
    const r = await app.registry.execute("filter.test", { filter_id: e.filterId, model_id: e.modelId }, "user");
    return lastTest ?? { kind: r.ok ? "ok" : "error", text: r.result };
  }
  const got = bodyOfEdit(e);
  if (!got.ok) return { kind: "error", text: got.error };
  try {
    const r = await testInline(e.modelId, { rules: got.body.rules });
    return r ? { kind: "ok", text: testText(model, r) } : { kind: "error", text: "Ядро ответило на проверку без чисел. Повторите проверку." };
  } catch (err) {
    const t = testRefusal(err);
    return { kind: t.notReady ? "notice" : "error", text: t.text };
  }
}

export function bindFilters(app: App): void {
  const r = app.registry;
  r.bind(
    "filters.manage",
    noted(app, async (a, source) => {
      if (a.filter_id === undefined) {
        openFiltersWindow(app);
        return "Открыто окно «Фильтры»";
      }
      if (!(await mayReplace(source))) return "Отменено";
      const f = await editFilter(app, a.filter_id as number);
      return `Открыто окно «Фильтры»: «${f.name}»`;
    }),
    needProject,
  );
  r.bind("filter.save", noted(app, (a, source) => runSave(app, a, source)), needWritable(app));
  r.bind("filter.delete", noted(app, runDelete), needWritable(app));
  r.bind("filter.test", noted(app, runTest), needProject);
  r.bind("filter.usage", noted(app, runUsage), needProject);
  // the library of the project: read when it opens, kept by the events of the core
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetFilters();
    if (cur) void refreshFilters().catch(() => undefined);
  });
  onCoreEvent((event, data) => {
    if (event !== "objects.changed") return;
    try {
      const d = JSON.parse(data) as { kind?: unknown; id?: unknown; deleted?: unknown };
      if (d.kind !== "filter") return;
      if (d.deleted === true && typeof d.id === "number") forgetFilter(d.id);
    } catch {
      /* a malformed event: the list is read again */
    }
    void refreshFilters()
      .then(followClean)
      .catch(() => undefined);
  });
  // the form is not kept when the window is closed
  windowStore.subscribe(() => {
    if (windowOfFilters() === null && currentEdit()) forgetEdit();
  });
}

/** The window «Фильтры» for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderFiltersWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== FILTERS_WINDOW) return null;
  return createElement(FiltersWindow, { winId: w.id });
}
