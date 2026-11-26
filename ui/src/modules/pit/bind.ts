/**
 * The handlers of the commands of the pit optimisation and the windows «Сценарий карьера» and «Экономика карьера» of the shell (ТЗ 8.11.5; ADR 0028
 * решения 20, 28–30; docs/progress/e21-api.md §5, §6, §16). A command without arguments opens its window; with them it turns them into the request
 * (every wrong thing is told in Russian with a hint and the place in the form; nothing wrong reaches the core) and executes. The windows do everything
 * through the registry (the log, the notice, the question of the assistant). `pit.run` is unavailable until `validate` of the scenario is `ready`
 * (the hint is the first `missing`); the run is a job (stages, «Отменить»: nothing is written). The commands of 21.17–21.19 (`pit.rocks`, `.slopes`,
 * `.calendar`, `.report`, `.surfaces`, `.solid`, `.compare`, `.show`) are bound by their lanes (`bindPit` below calls them: one line each); the window
 * layer of the shell calls `renderPitWindow`. One app at a time (the tests create several): `bindPit` answers the function that unbinds the listeners.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { blockById, blocksStore, loadBlockDetails } from "../blocks/state";
import { cancelJob, onCoreEvent } from "../jobs/state";
import { askImpact } from "../lineage/impactAsk";
import { projectStore } from "../projects/state";
import { DEFAULT_PARAMS, readCheckSummary, type PitParams, type ScenarioDetail } from "./answers";
import { readCheck } from "./checkModel";
import { createScenario, deleteScenario, loadJob, loadRunResult, loadScenario, refusalOf, startCheck, startRun, updateScenario, waitJobEnd, type Refusal } from "./api";
import { economicsFormOfArgs, economicsOfParams, economicsPatch, EMPTY_SCENARIO, hasEconomics, hasRelief, hasScenarioKeys, issueOfRefusal, mergeParams, nameIssue, scenarioFormOfArgs, scenarioFormOfParams, scenarioPatch, type Args, type FormIssue, type ScenarioForm } from "./forms";
import { checkText, runText } from "./format";
import { calendarFormOfParams } from "./formsCalendar";
import { rocksFormOfParams } from "./formsRocks";
import { slopesFormOfParams } from "./formsSlopes";
import { bindCalendar, bindRocks, bindSlopes } from "./bindSetup";
import { CalendarWindow } from "./CalendarWindow";
import { CheckWindow } from "./CheckWindow";
import { EconomicsWindow } from "./EconomicsWindow";
import { RocksWindow } from "./RocksWindow";
import { ScenarioWindow } from "./ScenarioWindow";
import { SlopesWindow } from "./SlopesWindow";
import { beginCheck, beginRun, CALENDAR_TITLE, CHECK_TITLE, currentRun, currentScenarioId, ECONOMICS_TITLE, ensureDefaults, ensureModelDefaults, isRunning, loadScenarios, patchCheck, patchRun, pitEvent, pitStore, PIT_CALENDAR_WINDOW, PIT_CHECK_WINDOW, PIT_ECONOMICS_WINDOW, PIT_ROCKS_WINDOW, PIT_SCENARIO_WINDOW, PIT_SLOPES_WINDOW, resetPit, ROCKS_TITLE, SLOPES_TITLE, runReason, scenarioById, SCENARIO_TITLE, selectedScenario, selectScenario, setEconIssue, setScenarioIssue, type PitState } from "./state";

/** A refusal as an error of the core that keeps the place in the params (`param`): the window marks the field with it. */
export const asError = (r: Refusal) => {
  const e = refusal(r.code, r.message, r.hint);
  if (r.param) e.body = { param: r.param };
  return e;
};
const NO_SCENARIO = (id: number) => refusal("pit_scenario_not_found", `Сценарий карьера №${id} не найден.`, "Номера сценариев — в разделе «Карьер» → «Сценарии» дерева.");

/** The scenario of a command: `scenario_id`, else the one selected in the tree, else the one the windows work on. */
export function scenarioIdOf(app: App, a: Args): number {
  const known = (id: number) => {
    if (pitStore.get().scenariosLoaded && !scenarioById(id)) throw NO_SCENARIO(id);
    return id;
  };
  if (typeof a.scenario_id === "number") return known(a.scenario_id);
  const id = currentScenarioId(app);
  if (id !== null) return known(id);
  throw refusal("bad_param", "Не задан сценарий карьера.", "Передайте scenario_id, выберите сценарий в разделе «Карьер» дерева или создайте его командой «Новый сценарий».");
}

/** The scenario as the core has it now (the version of the edit comes from here). */
export async function freshScenario(id: number): Promise<ScenarioDetail> {
  try {
    return await loadScenario(id);
  } catch (e) {
    throw asError(refusalOf(e));
  }
}

const nameOf = (id: number, detail?: ScenarioDetail): string => detail?.name ?? scenarioById(id)?.name ?? `№${id}`;

/** What the scenario still needs to run: told after every change, so that a person and the assistant know the next step. */
export function readyText(): string {
  const v = pitStore.get().validate;
  if (!v) return "";
  if (v.ready) return "Сценарий готов к запуску.";
  const first = (v.missing[0]?.message ?? "параметров").replace(/\.$/, "");
  return `Для запуска не хватает: ${first}${v.missing.length > 1 ? ` (всего ${v.missing.length})` : ""}.`;
}

/**
 * Puts a part of the params into the scenario (`PUT`: the whole canonical params, the version of the read — the optimistic lock): for the commands of
 * the windows of this lane and of 21.17 (the rocks, the slopes, the calendar). The scenario is read again afterwards (its readiness too).
 */
export async function applyParams(id: number, patch: Partial<PitParams>, extra: { name?: string } = {}): Promise<{ text: string; version: number }> {
  const detail = await freshScenario(id);
  let r;
  try {
    r = await updateScenario(id, { ...(extra.name !== undefined ? { name: extra.name } : {}), params: mergeParams(detail.params, patch), expected_version: detail.version });
  } catch (e) {
    throw asError(refusalOf(e));
  }
  await selectScenario(id);
  void loadScenarios();
  const stale = r.params_changed && detail.has_result ? " Параметры изменены: результат устарел, нужен новый запуск." : "";
  return { text: `Сценарий «${r.name || nameOf(id, detail)}» обновлён (версия ${r.version}).${stale} ${readyText()}`.trim(), version: r.version };
}

// ---- the windows ----

export const windowOfScenario = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_SCENARIO_WINDOW)?.id ?? null;
export const windowOfEconomics = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_ECONOMICS_WINDOW)?.id ?? null;
export const windowOfRocks = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_ROCKS_WINDOW)?.id ?? null;
export const windowOfSlopes = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_SLOPES_WINDOW)?.id ?? null;
export const windowOfCalendar = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_CALENDAR_WINDOW)?.id ?? null;
export const windowOfCheck = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_CHECK_WINDOW)?.id ?? null;

/** Opens (or brings forward) the window of a kind; its title names the scenario. */
export function showWindow(kind: string, title: string, size: "form" | "normal"): string {
  const open = windowStore.get().windows.find((w) => w.kind === kind)?.id;
  if (open) {
    updateWindow(open, { title });
    focusWindow(open);
    return open;
  }
  return openWindow(kind, title, size, {}, ...viewport());
}

/** Opens the window «Проверка шаблона карьера» (its result is `pitStore.check`: the command fills it). */
export const openCheckWindow = (): string => showWindow(PIT_CHECK_WINDOW, CHECK_TITLE, "normal");

/** The window of the rocks / the slopes / the calendar of a scenario: the form is read from the scenario, its readiness and the defaults of its model are read too. */
async function openSetup(kind: string, base: string, id: number, form: (p: PitParams) => Partial<PitState>, size: "form" | "normal"): Promise<string> {
  void ensureDefaults();
  const detail = await freshScenario(id);
  pitStore.set(form(detail.params));
  if (detail.params.model_id !== null) {
    void loadBlockDetails(detail.params.model_id);
    void ensureModelDefaults(detail.params.model_id);
  }
  await selectScenario(id);
  return showWindow(kind, `${base} · ${detail.name}`, size);
}

export const openRocksWindow = (id: number): Promise<string> => openSetup(PIT_ROCKS_WINDOW, ROCKS_TITLE, id, (p) => ({ rocksForm: rocksFormOfParams(p), rocksIssue: null }), "normal");
export const openSlopesWindow = (id: number): Promise<string> => openSetup(PIT_SLOPES_WINDOW, SLOPES_TITLE, id, (p) => ({ slopesForm: slopesFormOfParams(p), slopesIssue: null }), "form");
export const openCalendarWindow = (id: number): Promise<string> => openSetup(PIT_CALENDAR_WINDOW, CALENDAR_TITLE, id, (p) => ({ calendarForm: calendarFormOfParams(p), calendarIssue: null }), "form");

/** The model of a new scenario: the one selected in the tree, else the one the form has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = pitStore.get().scenarioForm.modelId;
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

/** A model chosen in the window «Сценарий»: its fields and its DTMs are read, a relief of another model is not kept. */
export function chooseModel(id: number | null): void {
  pitStore.set((s) => ({ scenarioForm: { ...s.scenarioForm, modelId: id }, scenarioIssue: null }));
  if (id === null) return;
  void loadBlockDetails(id);
  void ensureModelDefaults(id);
}

/** Opens the window «Сценарий карьера»: a new scenario (`editId` null) or the relief and the restrictor of an existing one. */
export async function openScenarioWindow(form: ScenarioForm): Promise<string> {
  void ensureDefaults();
  pitStore.set({ scenarioForm: form, scenarioIssue: null });
  if (form.modelId !== null) chooseModel(form.modelId);
  const title = form.editId === null ? SCENARIO_TITLE : `Рельеф и ограничитель · ${scenarioById(form.editId)?.name ?? `№${form.editId}`}`;
  const open = windowOfScenario();
  if (open) {
    updateWindow(open, { title });
    focusWindow(open);
    return open;
  }
  return openWindow(PIT_SCENARIO_WINDOW, title, "form", {}, ...viewport());
}

/** Opens the window «Экономика карьера» for a scenario (its params are in the form). */
export async function openEconomicsWindow(id: number): Promise<string> {
  void ensureDefaults();
  const detail = await freshScenario(id);
  pitStore.set({ econForm: economicsOfParams(detail.params), econIssue: null });
  if (detail.params.model_id !== null) void ensureModelDefaults(detail.params.model_id);
  const title = `${ECONOMICS_TITLE} · ${detail.name}`;
  const open = windowOfEconomics();
  if (open) {
    updateWindow(open, { title });
    focusWindow(open);
    return open;
  }
  return openWindow(PIT_ECONOMICS_WINDOW, title, "normal", {}, ...viewport());
}

// ---- the commands ----

/** `pit.new {name?, model_id?, terrain_id?, filter_id?, restrict?, solver?}`: without arguments — the window; with them — the scenario is created. */
export async function runNew(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!hasScenarioKeys(a)) {
    await openScenarioWindow({ ...EMPTY_SCENARIO, modelId: defaultModel(app) });
    return `Открыто окно «${SCENARIO_TITLE}»`;
  }
  const got = scenarioFormOfArgs(a, EMPTY_SCENARIO);
  const fail = (i: FormIssue): never => {
    setScenarioIssue(i);
    throw refusal("bad_param", i.message, i.hint);
  };
  if (!got.ok) return fail(got.issue);
  const bad = nameIssue(got.form.name, pitStore.get().defaults?.limits);
  if (bad) return fail(bad);
  const patch = scenarioPatch(got.form);
  if (!patch.ok) return fail(patch.issue);
  if (got.form.modelId !== null && !blockById(got.form.modelId) && blocksStore.get().loaded) {
    return fail({ field: "model", param: "model_id", message: `Блочная модель №${got.form.modelId} не найдена.`, hint: "Номера моделей — в разделе «Блочные модели» дерева." });
  }
  if (source === "user" && windowOfScenario()) pitStore.set({ scenarioForm: got.form, scenarioIssue: null });
  const base = (await ensureDefaults())?.params ?? DEFAULT_PARAMS;
  let made;
  try {
    made = await createScenario({ ...(got.form.name ? { name: got.form.name } : {}), params: mergeParams(base, patch.patch) });
  } catch (e) {
    const r = refusalOf(e);
    setScenarioIssue(issueOfRefusal(r));
    throw asError(r);
  }
  pitStore.set({ scenarioIssue: null });
  await selectScenario(made.id);
  void loadScenarios();
  return `Сценарий «${made.name}» №${made.id} создан. ${readyText()} Цены и затраты — в команде «Экономика», типы пород — в «Породы».`.trim();
}

/** `pit.economics {scenario_id?, elements?, density?, …}`: without the economics in the call — the window; with it — the scenario is changed. */
export async function runEconomics(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (!hasEconomics(a)) {
    await openEconomicsWindow(id);
    return `Открыто окно «${ECONOMICS_TITLE}»`;
  }
  const detail = await freshScenario(id);
  const form = economicsFormOfArgs(a, detail.params);
  const patch = economicsPatch(form, pitStore.get().defaults?.limits);
  if (!patch.ok) {
    setEconIssue(patch.issue);
    throw refusal("bad_param", patch.issue.message, patch.issue.hint);
  }
  if (source === "user" || windowOfEconomics()) pitStore.set({ econForm: form, econIssue: null });
  try {
    const r = await applyParams(id, patch.patch);
    setEconIssue(null);
    return r.text;
  } catch (e) {
    const r = refusalOf(e);
    setEconIssue(issueOfRefusal(r));
    throw e;
  }
}

/** `pit.relief {scenario_id?, terrain_id?, filter_id?, restrict?}`: without them — the window of the scenario; with them — the relief and the restrictor are changed. */
export async function runRelief(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  const detail = await freshScenario(id);
  if (!hasRelief(a)) {
    await openScenarioWindow(scenarioFormOfParams(detail.params, detail.name, id));
    return `Открыто окно «${SCENARIO_TITLE}» для сценария «${detail.name}»`;
  }
  const got = scenarioFormOfArgs(a, scenarioFormOfParams(detail.params, detail.name, id));
  const fail = (i: FormIssue): never => {
    setScenarioIssue(i);
    throw refusal("bad_param", i.message, i.hint);
  };
  if (!got.ok) return fail(got.issue);
  const patch = scenarioPatch(got.form);
  if (!patch.ok) return fail(patch.issue);
  try {
    const { terrain_id, filter_id, restrict } = patch.patch;
    return (await applyParams(id, { terrain_id, filter_id, restrict })).text;
  } catch (e) {
    setScenarioIssue(issueOfRefusal(refusalOf(e)));
    throw e;
  }
}

/** `pit.check {scenario_id?}`: the job `pit.check` followed to its end; the window «Проверка шаблона» shows the result (the table and the chart φ_эфф(az)), the answer is its text. */
export async function runCheck(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  const detail = await freshScenario(id);
  const seq = beginCheck(id, detail.name);
  openCheckWindow();
  try {
    const started = await startCheck(detail.params);
    const end = await waitJobEnd(started);
    if (end.status === "cancelled") {
      patchCheck(seq, { phase: "idle" });
      return "Проверка шаблона отменена";
    }
    const job = await loadJob(started);
    if (end.status === "failed" || job.error) {
      const e = job.error ?? end.error;
      throw refusal(e?.code ?? "job_failed", e?.message ?? "Шаблон не проверен.", e?.hint ?? "Повторите проверку.");
    }
    const summary = readCheckSummary(job.result);
    const full = readCheck(job.result);
    if (!summary || !full) throw refusal("bad_answer", "Ядро вернуло проверку шаблона в непонятной форме.", "Обновите программу.");
    patchCheck(seq, { phase: "done", result: full });
    return checkText(summary);
  } catch (e) {
    const r = refusalOf(e);
    patchCheck(seq, { phase: "failed", error: r });
    throw asError(r); // a refusal made here is a CoreError too: it keeps its words
  }
}

/** `pit.run {scenario_id?, solver?}`: the job `pit.run` followed to its end (stages, «Отменить»); the answer — the text of the result. */
export async function runRun(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (isRunning()) throw refusal("pit_running", "Сценарий уже считается.", "Дождитесь конца или отмените расчёт во вкладке «Задания».");
  if (app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения: расчёт не запущен.", "Откройте проект для записи: расчёт пишет поля PIT и ORE и отчёт.");
  const name = nameOf(id);
  const solver = a.solver === "auto" || a.solver === "hpf" || a.solver === "ortools" ? a.solver : undefined;
  const seq = beginRun(id, name);
  const uid = projectStore.get().current?.uid ?? null;
  const fail = (r: Refusal): never => {
    patchRun(seq, { phase: "failed", error: r });
    throw asError(r);
  };
  let started;
  try {
    started = await startRun(id, solver);
  } catch (e) {
    return fail(refusalOf(e));
  }
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitJobEnd(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (currentRun().seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") {
    patchRun(seq, { phase: "cancelled" });
    void loadScenarios();
    return `Расчёт сценария «${name}» отменён: ничего не записано`;
  }
  if (end.status === "failed") {
    const e = end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message ?? "Расчёт не завершён.", hint: e?.hint ?? "Повторите запуск.", param: null });
  }
  let result;
  try {
    result = await loadRunResult(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!result) return fail({ code: "bad_answer", message: "Ядро вернуло расчёт без итога.", hint: "Повторите запуск; если повторится — обновите программу.", param: null });
  patchRun(seq, { phase: "done", result });
  void loadScenarios();
  void selectScenario(id);
  return runText(result);
}

/** «Отменить» of the window: the job of the run is cancelled between the stages; nothing is written (the record is one transaction). */
export async function cancelRun(): Promise<void> {
  const job = currentRun().job;
  if (job && isRunning()) await cancelJob(job);
}

/** `pit.rename {scenario_id?, name}`. */
export async function runRename(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  const name = typeof a.name === "string" ? a.name.trim() : "";
  if (!name) throw refusal("bad_param", "Не задано имя сценария.", "Передайте name — новое имя.");
  const bad = nameIssue(name, pitStore.get().defaults?.limits);
  if (bad) throw refusal("bad_param", bad.message, bad.hint);
  const detail = await freshScenario(id);
  try {
    const r = await updateScenario(id, { name, expected_version: detail.version });
    await selectScenario(id);
    void loadScenarios();
    return `Сценарий «${detail.name}» переименован в «${r.name || name}»`;
  } catch (e) {
    throw asError(refusalOf(e));
  }
}

/** `pit.delete {scenario_id?}`: the question of the graph (what is built from the scenario), then the deletion; the fields PIT, ORE and the DTMs stay. */
export async function runDelete(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  const name = nameOf(id);
  if (source === "user" && !(await askConfirm({ title: "Удалить сценарий карьера", message: `Удалить сценарий «${name}»? Отчёт pit-by-pit удалится из проекта; поля PIT и ORE в модели и ЦТМ оболочек останутся.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  if (!(await askImpact({ kind: "object:pit_scenario", id }, `удалить сценарий карьера «${name}»`))) return "Отменено";
  try {
    await deleteScenario(id);
  } catch (e) {
    throw asError(refusalOf(e));
  }
  if (currentScenarioId(app) === id) await selectScenario(null);
  void loadScenarios();
  return `Сценарий «${name}» удалён`;
}

// ---- the binding ----

export function bindPit(app: App): () => void {
  const r = app.registry;
  const writable = needWritable(app);
  r.bind("pit.new", noted(app, (a, s) => runNew(app, a, s)), writable);
  r.bind("pit.economics", noted(app, (a, s) => runEconomics(app, a, s)), writable);
  r.bind("pit.relief", noted(app, (a) => runRelief(app, a)), writable);
  r.bind("pit.check", noted(app, (a) => runCheck(app, a)), writable);
  r.bind("pit.run", noted(app, (a) => runRun(app, a)), (ctx) => needProject(ctx) ?? runReason(app));
  r.bind("pit.rename", noted(app, (a) => runRename(app, a)), writable);
  r.bind("pit.delete", noted(app, (a, s) => runDelete(app, a, s)), writable);
  bindRocks(app);
  bindSlopes(app);
  bindCalendar(app);
  // 21.18: bindReport(app), bindShow(app) · 21.19: bindSurfaces(app), bindSolid(app), bindCompare(app)

  // the scenarios belong to the project: another project — clean windows and the lists read again
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadScenarios();
  let seen: number | null = selectedScenario(app);
  const offs = [
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      seen = null;
      resetPit();
      if (cur) void loadScenarios();
    }),
    // a scenario chosen in the tree is the one the commands work on: its params and its readiness are read
    app.store.subscribe(() => {
      const sel = selectedScenario(app);
      if (sel === seen) return;
      seen = sel;
      if (sel !== null) void selectScenario(sel);
    }),
    // a model that is gone leaves the form of the new scenario
    blocksStore.subscribe(() => {
      const f = pitStore.get().scenarioForm;
      if (f.editId === null && f.modelId !== null && blocksStore.get().loaded && !blockById(f.modelId)) pitStore.set({ scenarioForm: { ...f, modelId: null, terrainId: null } });
    }),
    onCoreEvent((event, data) => {
      if (event !== "pit.changed" || !projectStore.get().current) return;
      const e = pitEvent(data);
      void loadScenarios();
      const cur = pitStore.get().currentId;
      if (cur !== null && (e.id === null || e.id === cur)) void selectScenario(e.deleted ? null : cur);
    }),
  ];
  return () => offs.forEach((off) => off());
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of this module. The windows of 21.18–21.19 add their cases here. */
export function renderPitWindow(w: FloatWin): ReactNode | null {
  if (w.kind === PIT_SCENARIO_WINDOW) return createElement(ScenarioWindow, { winId: w.id });
  if (w.kind === PIT_ECONOMICS_WINDOW) return createElement(EconomicsWindow, { winId: w.id });
  if (w.kind === PIT_ROCKS_WINDOW) return createElement(RocksWindow, { winId: w.id });
  if (w.kind === PIT_SLOPES_WINDOW) return createElement(SlopesWindow, { winId: w.id });
  if (w.kind === PIT_CALENDAR_WINDOW) return createElement(CalendarWindow, { winId: w.id });
  if (w.kind === PIT_CHECK_WINDOW) return createElement(CheckWindow, { winId: w.id });
  return null;
}
