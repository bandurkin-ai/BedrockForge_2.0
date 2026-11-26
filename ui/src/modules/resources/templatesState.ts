/**
 * What the window «Шаблоны форм отчёта» knows (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19, 29): the template chosen in the list, the template as the
 * core gave it (`base`), the draft the editor changes, the first wrong thing, the notice. The list of the templates is `resourcesStore.templates`
 * (read again on `resources.changed`). One window per project. No React.
 */
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { NEW_DRAFT, type Draft, type Issue, type TemplateBody } from "./templates";

export const TEMPLATES_WINDOW = "resources-templates";
export const TEMPLATES_TITLE = "Шаблоны форм отчёта";

export interface TemplatesState {
  /** the code of the chosen template; null — a new one (not saved yet) or nothing */
  selected: string | null;
  base: TemplateBody | null;
  draft: Draft | null;
  loading: boolean;
  busy: boolean;
  issue: Issue | null;
  note: string | null;
  /** the project the editor belongs to: another project — a clean editor */
  uid: string | null;
}

const EMPTY: TemplatesState = { selected: null, base: null, draft: null, loading: false, busy: false, issue: null, note: null, uid: null };

export const templatesStore = new Store<TemplatesState>(EMPTY);

export const currentDraft = (): Draft | null => templatesStore.get().draft;

/** A clean editor of the project that is open now. */
export function resetTemplates(): void {
  templatesStore.set({ ...EMPTY, uid: projectStore.get().current?.uid ?? null });
}

/** The editor of another project is dropped (the window asks it when it opens). */
export function syncProject(): void {
  if ((projectStore.get().current?.uid ?? null) !== templatesStore.get().uid) resetTemplates();
}

export function setDraft(patch: Partial<Draft>): void {
  templatesStore.set((s) => (s.draft ? { draft: { ...s.draft, ...patch }, issue: null, note: null } : {}));
}

/** The whole draft replaced (a label renamed, a row moved — the helpers of `templates.ts` give the new draft). */
export function putDraft(d: Draft): void {
  templatesStore.set({ draft: d, issue: null, note: null });
}

export const setIssue = (i: Issue | null): void => templatesStore.set({ issue: i });

export const startNew = (): void => templatesStore.set({ selected: null, base: null, draft: { ...NEW_DRAFT, labels: [""] }, issue: null, note: null });
