/**
 * What the windows of the groups of a model share (ТЗ 8.9.2, 8.9.4; ADR 0026 решения 15–18; docs/progress/e19-api.md §4, §5, §12): «Оболочки
 * содержаний», «Жила», «Расширение каркаса». A group is written with `POST /models/{id}/groups` (new — last, the oldest) or
 * `PUT /models/{id}/groups/{gid}` (an edit); the version is the one the core stores now (read just before). The window is a form: its «Сохранить»
 * runs the command of the kind (`implicit.shells` …) with the typed values, so the registry, the log and the assistant see the same write. A
 * command with no values opens the window (ADR 0021 п. 9), with them it writes. The refusal of the core (`param` — «group.params.thresholds.1»,
 * «groups.2.codes.0») stands under its field of the window; the names of the fields are the local paths («params.thresholds.1», «codes», «name»).
 * No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { refusal, viewport } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { closeWindow, focusWindow, openWindow, updateWindow, windowStore } from "../../shell/windows";
import { isIssue, type Bounds, type Issue } from "../estimation/formBase";
import { addGroup, previewPoints, refusalOf, replaceGroup, type DefaultsAnswer, type GroupMeta, type ModelInfo, type PreviewAnswer, type Range, type Refusal } from "./api";
import { EMPTY_FORM, formOfMeta, rangesOf, type ModelForm } from "./form";
import { implicitStore, loadDetails, loadRows, modelById, pickModel } from "./state";
import { ensureDefaults } from "./wizardState";

export type GroupKind = "shell" | "vein" | "expand";

// ---- the ranges of the parameters of a group (the answer of `GET /defaults` is the source; these mirror `spec_parts.RANGES`) ----

const R = (lo: number | null, hi: number | null, unit = ""): Range => ({ lo, hi, lo_open: false, integer: false, unit });

export const GROUP_RANGES: Record<string, Range> = {
  p: R(0.3, 0.7),
  t_min: R(0, 100, "м"),
  offset: R(0, 10_000, "м"),
  expand: R(-1000, 1000, "м"),
};

/** Thresholds of a shell (`limits.thresholds` of the core). */
export const THRESHOLDS_MAX = 20;

export const groupRanges = (d: DefaultsAnswer | null): Record<string, Range> => ({ ...GROUP_RANGES, ...rangesOf(d) });

export const boundsOf = (ranges: Record<string, Range>, key: string): Bounds => {
  const r = ranges[key] ?? GROUP_RANGES[key];
  return { lo: r.lo, hi: r.hi, integer: r.integer, open: r.lo_open, ...(r.unit ? { unit: r.unit } : {}) };
};

// ---- the spec of a kind ----

/** What a form needs to be checked: the ranges of the core, the model it belongs to and the codes the other groups already hold. */
export interface GroupCtx {
  ranges: Record<string, Range>;
  model: ModelForm;
  taken: ReadonlySet<string>;
}

export type Made = { ok: true; group: Record<string, unknown> } | { ok: false; issue: Issue };

export interface KindSpec<F> {
  kind: GroupKind;
  /** the kind of the floating window */
  window: string;
  command: string;
  /** «Оболочки содержаний» */
  title: string;
  /** `type` of the group in the core */
  type: string;
  /** «Оболочка», «Жила», «Расширение»: the base of the name by default */
  nameBase: string;
  empty: (model: ModelForm) => F;
  fromGroup: (g: GroupMeta, model: ModelForm) => F;
  /** the arguments of the command over a base form; the words under the first wrong one */
  fromArgs: (a: Record<string, unknown>, base: F, model: ModelForm) => F | Issue;
  /** the group of the body (`{name, type, codes, params}`) or the first wrong thing of the form */
  make: (f: F, ctx: GroupCtx) => Made;
  /** does the command carry enough to write (else it opens the window)? */
  complete: (a: Record<string, unknown>) => boolean;
  /** the arguments of the command from a made group */
  argsOf: (group: Record<string, unknown>) => Record<string, unknown>;
  nameOf: (f: F) => string;
  withName: (f: F, name: string) => F;
}

export interface GroupWin<F> {
  modelId: number | null;
  /** null — a new group */
  groupId: number | null;
  form: F;
  issue: Issue | null;
  saving: boolean;
  dirty: boolean;
  /** the line the last write told */
  saved: string | null;
}

export const NO_MODEL = (id: number) => refusal("model_not_found", `Неявная модель №${id} не найдена.`, "Номера моделей — в разделе «Неявные модели» дерева.");

/** The path of a refusal inside the group: «group.params.p» and «groups.3.params.p» are «params.p». */
export const localParam = (p: string | null): string | null => (p === null ? null : p.replace(/^group\./, "").replace(/^groups\.\d+\./, ""));

/** The refusal of the core as a thing under a field of the window. */
export function groupIssue(r: Refusal): Issue {
  const param = localParam(r.param) ?? (r.code === "name_taken" ? "name" : r.code === "wireframe_open" ? "params.wireframe_id" : null);
  return { param, code: r.code, message: r.message, hint: r.hint, reread: r.code === "version_conflict" };
}

const NOUN = "Группа";

/** The model of a command: the given one, the one chosen in the tree, the only one of the project; else the words of why not. */
export function needModel(app: App, given: unknown): number {
  const id = pickModel(given, app.store.get().selected);
  if (id !== null) return id;
  const rows = implicitStore.get().rows;
  if (rows.length > 1) throw refusal("no_model", "Выберите модель: в проекте их несколько.", "Выберите модель в разделе «Неявные модели» дерева или передайте model_id.");
  throw refusal("no_model", "В проекте нет неявной модели.", "Создайте её командой «Новая модель» (мастер) и вернитесь к этой команде.");
}

/** The model with the parameters as stored (the version of a write is this one). */
export async function detailsOf(id: number): Promise<ModelInfo> {
  if (!modelById(id) && implicitStore.get().loaded) throw NO_MODEL(id);
  let info: ModelInfo | null;
  try {
    info = await loadDetails(id);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  if (!info) throw refusal("project_changed", "Проект изменился.", "Повторите команду.");
  return info;
}

/** The first free name «Оболочка N» among the groups of the model. */
export function freeGroupName(names: readonly string[], base: string): string {
  const taken = new Set(names);
  for (let i = names.length + 1; ; i++) if (!taken.has(`${base} ${i}`)) return `${base} ${i}`;
}

const stripped = (a: Record<string, unknown>): string[] => Object.keys(a).filter((k) => k !== "model_id" && k !== "group_id");

/** The runtime of a kind: its store, its window, the command (open or write). */
export function createGroupKind<F>(spec: KindSpec<F>) {
  const fresh = (): GroupWin<F> => ({ modelId: null, groupId: null, form: spec.empty(EMPTY_FORM), issue: null, saving: false, dirty: false, saved: null });
  const store = new Store<GroupWin<F>>(fresh());
  const windowId = (): string | null => windowStore.get().windows.find((w) => w.kind === spec.window)?.id ?? null;
  const titleOf = (name: string): string => `${spec.title}: «${name}»`;

  /** The groups of this kind the model holds. */
  const groupsOf = (info: ModelInfo | undefined): GroupMeta[] => formOfMeta("", info?.meta ?? {}).groups.flatMap((g) => (g.type === spec.type && g.raw ? [g.raw] : []));

  /** The codes the other groups of the model hold (a code is in one group). */
  const takenBy = (model: ModelForm, gid: number | null): Set<string> => new Set(model.groups.filter((g) => g.id !== gid).flatMap((g) => g.codes));

  const ctxOf = (model: ModelForm, gid: number | null, d: DefaultsAnswer | null): GroupCtx => ({ ranges: groupRanges(d), model, taken: takenBy(model, gid) });

  function set(patch: Partial<GroupWin<F>>): void {
    store.set(patch);
  }

  /** A change of the form (the person types): the old refusal goes. */
  function setForm(patch: Partial<F>): void {
    store.set((s) => ({ form: { ...s.form, ...patch }, issue: null, dirty: true, saved: null }));
  }

  function fail(id: number, issue: Issue): never {
    const s = store.get();
    if (windowId() && s.modelId === id) store.set({ issue });
    throw refusal(issue.code, issue.message, issue.hint);
  }

  /** The form of an existing group, or the empty one. */
  function formFor(info: ModelInfo, gid: number | null): F {
    const model = formOfMeta(info.name, info.meta);
    const raw = gid === null ? null : groupsOf(info).find((g) => g.id === gid);
    return raw ? spec.fromGroup(raw, model) : spec.empty(model);
  }

  /** `implicit.<kind> {model_id, group_id?, …}` with values: writes the group. */
  async function save(id: number, a: Record<string, unknown>): Promise<string> {
    const info = await detailsOf(id);
    const defaults = await ensureDefaults();
    const model = formOfMeta(info.name, info.meta);
    const gid = typeof a.group_id === "number" ? a.group_id : null;
    const existing = gid === null ? null : (groupsOf(info).find((g) => g.id === gid) ?? null);
    if (gid !== null && !existing) throw refusal("group_not_found", `В модели «${info.name}» нет такой группы (№${gid}) вида «${spec.title}».`, "Номера групп — в окне группы; без group_id команда добавляет новую.");
    const s = store.get();
    const own = windowId() !== null && s.modelId === id && s.groupId === gid; // the window of this very group: its form is the base
    const base = own ? s.form : existing ? spec.fromGroup(existing, model) : spec.empty(model);
    const typed = spec.fromArgs(a, base, model);
    if (isIssue(typed)) fail(id, typed);
    const form = spec.nameOf(typed).trim() ? typed : spec.withName(typed, freeGroupName(model.groups.map((g) => g.name), spec.nameBase));
    const made = spec.make(form, ctxOf(model, gid, defaults));
    if (!made.ok) fail(id, made.issue);
    const body = { ...(existing ? { id: existing.id, relation: existing.relation, faults: existing.faults } : {}), ...made.group };
    if (own) store.set({ saving: true });
    try {
      const r = gid === null ? await addGroup(id, info.version, body) : await replaceGroup(id, gid, info.version, body);
      const text = `${NOUN} «${r.group.name || String(made.group.name)}» ${gid === null ? "добавлена в модель" : "изменена в модели"} «${info.name}» (версия ${r.version}). Чтобы пересчитать поверхности и тела, выполните «Обновить».`;
      if (windowId() && store.get().modelId === id && (own || gid === null)) store.set({ groupId: r.group.id, form, dirty: false, issue: null, saved: text });
      await loadRows();
      void loadDetails(id).catch(() => undefined);
      return text;
    } catch (e) {
      const r = refusalOf(e);
      fail(id, groupIssue(r));
    } finally {
      store.set({ saving: false });
    }
  }

  /** `implicit.<kind>` without values: the window of the model (and of its group), the arguments in the form. */
  async function open(id: number, a: Record<string, unknown>): Promise<string> {
    const info = await detailsOf(id);
    const model = formOfMeta(info.name, info.meta);
    const gid = typeof a.group_id === "number" ? a.group_id : null;
    if (gid !== null && !groupsOf(info).some((g) => g.id === gid)) throw refusal("group_not_found", `В модели «${info.name}» нет такой группы (№${gid}) вида «${spec.title}».`, "Номера групп — в окне группы.");
    const s = store.get();
    const keep = windowId() !== null && s.modelId === id && s.groupId === gid; // the same group: what is typed stays
    const typed = spec.fromArgs(a, keep ? s.form : formFor(info, gid), model);
    if (isIssue(typed)) throw refusal(typed.code, typed.message, typed.hint);
    store.set({ modelId: id, groupId: gid, form: typed, issue: null, saving: false, dirty: stripped(a).length > 0 || (keep && s.dirty), saved: null });
    void ensureDefaults();
    const win = windowId();
    if (win) {
      focusWindow(win);
      updateWindow(win, { title: titleOf(info.name) });
    } else openWindow(spec.window, titleOf(info.name), "normal", {}, ...viewport());
    return `Открыто окно «${spec.title}» модели «${info.name}»`;
  }

  async function run(app: App, a: Record<string, unknown>, _source: CommandSource): Promise<string> {
    const id = needModel(app, a.model_id);
    return spec.complete(a) ? save(id, a) : open(id, a);
  }

  /** The buttons of the window: the typed form is checked, then the command writes it (and «Обновить» builds). */
  async function submit(app: App, andUpdate: boolean): Promise<void> {
    const s = store.get();
    if (s.modelId === null) return;
    const info = implicitStore.get().details[s.modelId];
    if (!info) return;
    const model = formOfMeta(info.name, info.meta);
    const d = (await ensureDefaults()) ?? null;
    const named = spec.nameOf(s.form).trim() ? s.form : spec.withName(s.form, freeGroupName(model.groups.map((g) => g.name), spec.nameBase));
    const made = spec.make(named, ctxOf(model, s.groupId, d));
    if (!made.ok) {
      store.set({ issue: made.issue });
      return;
    }
    const args = { model_id: s.modelId, ...(s.groupId !== null ? { group_id: s.groupId } : {}), ...spec.argsOf(made.group) };
    const r = await app.registry.execute(spec.command, args, "user");
    if (!r.ok) {
      app.store.set({ statusNote: r.result });
      return;
    }
    if (andUpdate) await app.registry.execute("implicit.update", { model_id: s.modelId }, "user");
  }

  /** «Данные группы»: the points the core would take for the form as typed (no solution); null — the form is wrong (the words are under the field). */
  async function preview(limit = 1000): Promise<PreviewAnswer | null> {
    const s = store.get();
    const info = s.modelId === null ? undefined : implicitStore.get().details[s.modelId];
    if (s.modelId === null || !info) return null;
    const d = (await ensureDefaults()) ?? null;
    const named = spec.nameOf(s.form).trim() ? s.form : spec.withName(s.form, freeGroupName(formOfMeta(info.name, info.meta).groups.map((g) => g.name), spec.nameBase));
    const made = spec.make(named, ctxOf(formOfMeta(info.name, info.meta), s.groupId, d));
    if (!made.ok) {
      store.set({ issue: made.issue });
      return null;
    }
    return previewPoints(s.modelId, { group: { ...made.group, ...(s.groupId !== null ? { id: s.groupId } : {}) }, limit });
  }

  /** The group the person chose in the window (null — a new one). */
  function choose(gid: number | null): void {
    const s = store.get();
    const info = s.modelId === null ? undefined : implicitStore.get().details[s.modelId];
    if (!info) return;
    store.set({ groupId: gid, form: formFor(info, gid), issue: null, dirty: false, saved: null });
  }

  /** The model is gone (or another project): the window goes. */
  function forget(modelId?: number): void {
    const s = store.get();
    if (modelId !== undefined && s.modelId !== modelId) return;
    const win = windowId();
    if (win) closeWindow(win);
    store.set(fresh());
  }

  return { spec, store, windowId, groupsOf, takenBy, ctxOf, set, setForm, save, open, run, submit, preview, choose, forget };
}

export type GroupRuntime<F> = ReturnType<typeof createGroupKind<F>>;
