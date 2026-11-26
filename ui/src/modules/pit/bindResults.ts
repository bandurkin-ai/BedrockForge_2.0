/**
 * The handlers of the commands `pit.surfaces`, `pit.solid` and `pit.compare` and the windows «Поверхности оболочек карьера», «Каркас карьера» and
 * «Сравнить сценарии карьера» of the shell (ТЗ 8.11.5; ADR 0028 решения 25–27, 29, 30; docs/progress/e21-api.md §9–§11, §13, §16). A command without
 * arguments from the interface opens its window; with them it executes (every wrong thing is told in Russian with a hint, nothing wrong reaches the core).
 * The surfaces and the solid are quick jobs followed to their end (the DTMs / the solid are wireframes of the project, the tree reads them from the
 * events); they need a scenario that has a result, a project open for writing. The comparison is synchronous and works in a project open for reading.
 * The windows run the commands through the registry (the log, the notice, the question of the assistant). `bindPitResults` is called by `bindPit`
 * (`bind.ts`); `renderPitResultsWindow` — by `renderPitWindow`. One app at a time (the tests create several): a new call replaces the listener of the project.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { projectStore } from "../projects/state";
import { DEFAULT_LIMITS } from "./answers";
import { refusalOf, waitJobEnd, type Refusal, type Started } from "./api";
import { asError, freshScenario, scenarioIdOf, showWindow } from "./bind";
import { CompareWindow } from "./CompareWindow";
import { loadSolidResult, loadSurfacesResult, startSolid, startSurfaces } from "./resultsApi";
import { compareText, parseShells, shellsOf, solidText, surfacesText } from "./resultsModel";
import { beginJob, COMPARE_TITLE, isBusy, loadCompare, patchJob, PIT_COMPARE_WINDOW, PIT_SOLID_WINDOW, PIT_SURFACES_WINDOW, resetResults, SOLID_TITLE, SURFACES_TITLE, type JobRun } from "./resultsState";
import { SolidWindow } from "./SolidWindow";
import { SurfacesWindow } from "./SurfacesWindow";
import { currentScenarioId, pitStore, scenarioById, selectScenario } from "./state";

type Args = Record<string, unknown>;
type Kind = "surfaces" | "solid";

export const windowOfSurfaces = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_SURFACES_WINDOW)?.id ?? null;
export const windowOfSolid = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_SOLID_WINDOW)?.id ?? null;
export const windowOfCompare = (): string | null => windowStore.get().windows.find((w) => w.kind === PIT_COMPARE_WINDOW)?.id ?? null;

const NO_RESULT = (name?: string) => refusal("pit_no_result", name ? `У сценария «${name}» нет результата.` : "У сценария нет результата.", "Запустите расчёт сценария: команда «Запустить».");

/**
 * Why `pit.surfaces` / `pit.solid` cannot run now, or null: a scenario without a result, one that is being calculated, a project where none has a result. No scenario chosen is
 * not a reason (the command names the scenario by `scenario_id`; without it the command says to choose one).
 */
export function noResultReason(app: App): string | null {
  const id = currentScenarioId(app);
  if (id === null) return compareReason();
  const row = scenarioById(id);
  if (row && row.state === "running") return "Сценарий считается: дождитесь конца расчёта";
  if (row && !row.has_result) return "У сценария нет результата: запустите расчёт";
  const s = pitStore.get();
  if (s.scenario?.id === id && !s.scenario.has_result) return "У сценария нет результата: запустите расчёт";
  return null;
}

/** Why `pit.compare` cannot run now, or null: the scenarios are known and none has a result. */
export function compareReason(): string | null {
  const s = pitStore.get();
  if (s.scenariosLoaded && !s.scenarios.some((r) => r.has_result)) return "Нет сценариев с результатом: запустите расчёт";
  return null;
}

// ---- the windows ----

/** Opens the window of the surfaces / of the solid for a scenario: its result (the shells) is read first, its name is in the title. */
async function openOn(kind: string, base: string, id: number): Promise<string> {
  const detail = await selectScenario(id);
  return showWindow(kind, `${base} · ${detail?.name ?? `№${id}`}`, "normal");
}

export const openSurfacesWindow = (id: number): Promise<string> => openOn(PIT_SURFACES_WINDOW, SURFACES_TITLE, id);
export const openSolidWindow = (id: number): Promise<string> => openOn(PIT_SOLID_WINDOW, SOLID_TITLE, id);

export function openCompareWindow(): string {
  const open = windowOfCompare();
  if (open) return showWindow(PIT_COMPARE_WINDOW, COMPARE_TITLE, "normal");
  return openWindow(PIT_COMPARE_WINDOW, COMPARE_TITLE, "normal", {}, ...viewport());
}

// ---- the quick jobs ----

/**
 * Follows a started job to its end: the stages are in the list of the jobs, the result is read from the job. A refusal at any step stands in the window (`error`)
 * and is thrown as an error of the core with its words; a cancel is not an error (null). The overtaken run (another one began, the project changed) writes nothing.
 */
async function follow<R>(kind: Kind, seq: number, start: () => Promise<Started>, load: (job: Started) => Promise<R | null>, what: string): Promise<R | null> {
  const patch = (p: Partial<JobRun<R>>) => patchJob(kind, seq, p as never);
  const fail = (r: Refusal): never => {
    patch({ phase: "failed", error: r });
    throw asError(r);
  };
  let started: Started;
  try {
    started = await start();
  } catch (e) {
    return fail(refusalOf(e));
  }
  patch({ job: started });
  let end;
  try {
    end = await waitJobEnd(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (end.status === "cancelled") {
    patch({ phase: "cancelled" });
    return null;
  }
  if (end.status === "failed") {
    const e = end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message || `Не удалось построить ${what}.`, hint: e?.hint || "Повторите команду; если повторится — проверьте результат сценария.", param: null });
  }
  let result: R | null;
  try {
    result = await load(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!result) return fail({ code: "bad_answer", message: `Ядро вернуло ${what} в непонятной форме.`, hint: "Обновите программу.", param: null });
  patch({ phase: "done", result });
  return result;
}

/** The checks both quick jobs share: no other run, a project open for writing, the scenario as the core has it now with a result. */
async function prepare(app: App, kind: Kind, id: number) {
  if (isBusy(kind)) throw refusal("pit_busy", kind === "surfaces" ? "Поверхности уже строятся." : "Каркас уже строится.", "Дождитесь конца: ход — во вкладке «Задания».");
  if (app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения: ничего не построено.", "Откройте проект для записи: поверхности и каркас — каркасы проекта.");
  const detail = await freshScenario(id);
  if (!detail.has_result) throw NO_RESULT(detail.name);
  return detail;
}

/** `pit.surfaces {scenario_id?, shells?}`: from the interface without `shells` — the window; with them (or from the assistant) — the DTMs of the shells (none — all of them). */
export async function runSurfaces(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (a.shells === undefined && source === "user") {
    await openSurfacesWindow(id);
    return `Открыто окно «${SURFACES_TITLE}»`;
  }
  const detail = await prepare(app, "surfaces", id);
  const all = shellsOf(detail);
  let shells: number[] | undefined;
  if (a.shells !== undefined) {
    const got = parseShells(a.shells, all.length || null);
    if (!got.ok) throw refusal("bad_param", got.message, got.hint);
    shells = got.shells;
  }
  if (source === "user") void openSurfacesWindow(id);
  const seq = beginJob("surfaces", id, detail.name);
  const result = await follow(
    "surfaces",
    seq,
    () => startSurfaces(id, shells),
    loadSurfacesResult,
    "поверхности оболочек",
  );
  return result ? surfacesText(result, detail.name) : "Построение поверхностей отменено: готовые оболочки остались";
}

/** `pit.solid {scenario_id?, shell, name?}`: from the interface without `shell` — the window; with it — the closed solid of the pit of the shell. */
export async function runSolid(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (a.shell === undefined && source === "user") {
    await openSolidWindow(id);
    return `Открыто окно «${SOLID_TITLE}»`;
  }
  const detail = await prepare(app, "solid", id);
  const all = shellsOf(detail);
  const shell = a.shell;
  if (typeof shell !== "number" || !Number.isInteger(shell) || shell < 1 || (all.length > 0 && shell > all.length)) {
    throw refusal("bad_param", shell === undefined ? "Не задан номер оболочки." : `Оболочки «${String(shell)}» нет${all.length ? `: в результате их ${all.length}` : ""}.`, `Передайте shell — номер оболочки${all.length ? ` от 1 до ${all.length}` : " от 1"} (таблица pit-by-pit).`);
  }
  const name = typeof a.name === "string" ? a.name.trim() : "";
  const limit = pitStore.get().defaults?.limits.name ?? DEFAULT_LIMITS.name;
  if (name.length > limit) throw refusal("bad_param", `Имя каркаса длиннее ${limit} знаков.`, "Сократите имя или оставьте его пустым: ядро назовёт каркас само.");
  if (source === "user") void openSolidWindow(id);
  const seq = beginJob("solid", id, detail.name);
  const result = await follow(
    "solid",
    seq,
    () => startSolid(id, shell, name || undefined),
    loadSolidResult,
    "каркас карьера",
  );
  return result ? solidText(result) : "Построение каркаса отменено: ничего не записано";
}

// ---- the comparison ----

const sideId = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) ? v : null);

/** `pit.compare {a?, b?}`: without them — the window; with both — two scenarios by the shells (the same RF), NPV, the params; a shell only one has is a gap, not 0. */
export async function runCompare(a: Args, source: CommandSource): Promise<string> {
  const given = [a.a, a.b].filter((x) => x !== undefined).length;
  if (given === 0) {
    openCompareWindow();
    return `Открыто окно «${COMPARE_TITLE}»`;
  }
  const ia = sideId(a.a);
  const ib = sideId(a.b);
  if (given === 1 || ia === null || ib === null) throw refusal("bad_param", "Нужны оба сценария: a и b — их номера.", "Передайте номера двух сценариев (раздел «Карьер» → «Сценарии» дерева) или вызовите команду без параметров: откроется окно выбора.");
  for (const id of [ia, ib]) {
    const row = scenarioById(id);
    if (pitStore.get().scenariosLoaded && !row) throw refusal("pit_scenario_not_found", `Сценарий карьера №${id} не найден.`, "Номера сценариев — в разделе «Карьер» → «Сценарии» дерева.");
    if (row && !row.has_result) throw NO_RESULT(row.name);
  }
  if (source === "user") openCompareWindow();
  try {
    return compareText(await loadCompare(ia, ib));
  } catch (e) {
    throw asError(refusalOf(e));
  }
}

// ---- the binding ----

let offProject: (() => void) | null = null;

export function bindPitResults(app: App): void {
  const r = app.registry;
  r.bind("pit.surfaces", noted(app, (a, s) => runSurfaces(app, a, s)), (ctx) => needWritable(app)(ctx) ?? noResultReason(app));
  r.bind("pit.solid", noted(app, (a, s) => runSolid(app, a, s)), (ctx) => needWritable(app)(ctx) ?? noResultReason(app));
  r.bind("pit.compare", noted(app, (a, s) => runCompare(a, s)), (ctx) => needProject(ctx) ?? compareReason());
  // the jobs and the answers belong to the project: another project — clean windows
  offProject?.();
  let uid = projectStore.get().current?.uid ?? null;
  offProject = projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetResults();
  });
}

/** The windows for the window layer of the shell (via `renderPitWindow`); null — not a window of this file. */
export function renderPitResultsWindow(w: FloatWin): ReactNode | null {
  if (w.kind === PIT_SURFACES_WINDOW) return createElement(SurfacesWindow, { winId: w.id });
  if (w.kind === PIT_SOLID_WINDOW) return createElement(SolidWindow, { winId: w.id });
  if (w.kind === PIT_COMPARE_WINDOW) return createElement(CompareWindow, { winId: w.id });
  return null;
}
