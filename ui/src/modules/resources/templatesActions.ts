/**
 * The actions of the window «Шаблоны форм отчёта» (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19, 29; docs/progress/e20-api.md): choose a template,
 * make a new one or a copy, save (`PUT` with `expected_version`), delete (the question in our own window). Every refusal of the core is told
 * in Russian with its hint in the place of the editor; a template saved in another window meanwhile (409 `version_conflict`) is asked about,
 * never overwritten. A template is the show of a report: nothing is recalculated. No React.
 */
import { askConfirm } from "../../shell/dialogs";
import { deleteTemplate, loadTemplate, putTemplate, refusalOf, type Refusal } from "./api";
import { loadTemplates, resourcesStore } from "./state";
import { bodyOfDraft, copyDraft, draftOf, isChanged, newTemplateId, placeOfParam, type Issue } from "./templates";
import { currentDraft, setIssue, startNew, syncProject, templatesStore } from "./templatesState";

const issueOf = (r: Refusal): Issue => ({ ...placeOfParam(r.param), message: r.message, hint: r.hint });

const ids = (): string[] => resourcesStore.get().templates.map((t) => t.id);
const names = (): string[] => resourcesStore.get().templates.map((t) => t.name);

/** The window opens: the list is read, the first template is chosen (JORC) when none is. */
export async function startEditor(): Promise<void> {
  syncProject();
  await loadTemplates();
  const s = templatesStore.get();
  if (s.draft || s.loading) return;
  const first = resourcesStore.get().templates[0]?.id;
  if (first) await chooseTemplate(first, true);
}

/** Drops the changes of the draft only when the user agrees (our own window). */
async function mayLeave(): Promise<boolean> {
  const s = templatesStore.get();
  if (!s.draft || !isChanged(s.draft, s.base)) return true;
  return askConfirm({ title: "Шаблон изменён", message: "В окне есть несохранённые правки шаблона. Отбросить их?", okLabel: "Отбросить", danger: true });
}

/** A template of the list chosen: read whole from the core (the version is the one `expected_version` of the save will carry). */
export async function chooseTemplate(id: string, force = false): Promise<boolean> {
  if (!force && !(await mayLeave())) return false;
  templatesStore.set({ loading: true, issue: null, note: null });
  try {
    const t = await loadTemplate(id);
    templatesStore.set({ selected: t.id, base: t, draft: draftOf(t), loading: false });
    return true;
  } catch (e) {
    const r = refusalOf(e);
    templatesStore.set({ loading: false, issue: { place: "general", message: r.message, hint: r.hint, index: null } });
    if (r.code === "template_not_found") void loadTemplates(); // gone in another window: the list is read again
    return false;
  }
}

export async function newTemplate(): Promise<void> {
  if (await mayLeave()) startNew();
}

/** «Создать копию»: the template shown (a template of the box too) as a new one of the user. */
export async function copyTemplate(): Promise<boolean> {
  const s = templatesStore.get();
  if (!s.base || !(await mayLeave())) return false;
  templatesStore.set({ selected: null, draft: copyDraft(s.base, names()), issue: null, note: null });
  return true;
}

export interface Saved {
  id: string;
  version: number;
}

/** «Сохранить»: a new template gets its code from the name; one that exists goes with the version read. */
export async function saveTemplate(): Promise<Saved | null> {
  const d = currentDraft();
  if (!d || d.builtin || templatesStore.get().busy) return null;
  const got = bodyOfDraft(d);
  if (!got.ok) {
    setIssue(got.issue);
    return null;
  }
  templatesStore.set({ busy: true, issue: null, note: null });
  let id = d.id ?? newTemplateId(String(got.body.name), ids());
  const fresh = d.id === null;
  try {
    let saved: Saved | null = null;
    for (let attempt = 0; saved === null; attempt++) {
      try {
        saved = await putTemplate(id, got.body, fresh ? null : d.version);
      } catch (e) {
        const r = refusalOf(e);
        // a code taken meanwhile by another window: the list is read and the next free code is tried once
        if (fresh && r.code === "version_conflict" && attempt === 0) {
          await loadTemplates();
          id = newTemplateId(String(got.body.name), ids());
          continue;
        }
        throw e;
      }
    }
    const t = await loadTemplate(saved.id);
    templatesStore.set({ selected: t.id, base: t, draft: draftOf(t), busy: false, note: `Шаблон «${t.name}» сохранён (версия ${saved.version}).` });
    void loadTemplates();
    return saved;
  } catch (e) {
    const r = refusalOf(e);
    templatesStore.set({ busy: false, issue: issueOf(r) });
    if (r.code === "version_conflict" && !fresh) {
      // the template was saved in another window: reread it (the edits go) or keep the window as it is
      const again = await askConfirm({ title: "Шаблон изменён в другом окне", message: `Шаблон «${d.name}» сохранили в другом окне, пока вы его правили. Перечитать его? Ваши правки в этом окне пропадут.`, okLabel: "Перечитать", danger: true });
      if (again) await chooseTemplate(d.id as string, true);
    } else if (r.code === "template_not_found") void loadTemplates();
    return null;
  }
}

/** «Удалить»: a template of the user, after the question; the box (JORC, ГКЗ) is not deleted. */
export async function removeTemplate(): Promise<boolean> {
  const s = templatesStore.get();
  const b = s.base;
  if (!b || b.builtin || s.busy) return false;
  const ok = await askConfirm({
    title: "Удалить шаблон",
    message: `Удалить шаблон «${b.name}»? Сохранённые отчёты не пересчитываются; в отчётах, где выбрана эта форма, выберите другую.`,
    okLabel: "Удалить",
    danger: true,
  });
  if (!ok) return false;
  templatesStore.set({ busy: true, issue: null, note: null });
  try {
    await deleteTemplate(b.id, b.version);
  } catch (e) {
    const r = refusalOf(e);
    templatesStore.set({ busy: false, issue: issueOf(r) });
    if (r.code === "version_conflict" || r.code === "template_not_found") void loadTemplates();
    return false;
  }
  templatesStore.set({ busy: false, selected: null, base: null, draft: null });
  await loadTemplates();
  const first = resourcesStore.get().templates[0]?.id;
  if (first) await chooseTemplate(first, true);
  templatesStore.set({ note: `Шаблон «${b.name}» удалён.` });
  return true;
}
