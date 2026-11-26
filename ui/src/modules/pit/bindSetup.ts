/**
 * The handlers of the commands `pit.rocks`, `pit.slopes` and `pit.calendar` (ТЗ 8.11.2, 8.11.4, 8.11.5; ADR 0028 решения 4, 9–12, 19, 29, 30; docs/progress/e21-api.md §2, §3, §16)
 * and the windows «Породы карьера», «Углы бортов карьера» and «Календарь карьера» of the shell. A command without arguments opens its window; with them it turns them into the
 * part of the params (every wrong thing is told in Russian with a hint and the place in the form; nothing wrong reaches the core) and puts it into the scenario (`applyParams`:
 * `PUT` with the version of the read). Arguments that are not in the call leave the scenario as it is. A change of the calendar does not make the result old (the core: the calendar
 * is not in `params_hash`). The windows run the commands through the registry (the log, the notice, the question of the assistant). Called by `bindPit` (`bind.ts`).
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { needWritable, noted, refusal } from "../../shell/objectRun";
import { DEFAULT_LIMITS, type PitParams } from "./answers";
import { applyParams, freshScenario, openCalendarWindow, openRocksWindow, openSlopesWindow, scenarioIdOf, windowOfCalendar, windowOfRocks, windowOfSlopes } from "./bind";
import { refusalOf, type Refusal } from "./api";
import { issue, type Args, type FormIssue } from "./forms";
import { calendarFormOfArgs, calendarPatch, hasCalendar } from "./formsCalendar";
import { hasRocks, rocksFormOfArgs, rocksPatch } from "./formsRocks";
import { hasSlopes, slopesFormOfArgs, slopesPatch } from "./formsSlopes";
import { CALENDAR_TITLE, pitStore, ROCKS_TITLE, SLOPES_TITLE, setCalendarIssue, setRocksIssue, setSlopesIssue } from "./state";

const limitsNow = () => pitStore.get().defaults?.limits ?? DEFAULT_LIMITS;

/** The fields of the elements of a scenario: the keys of the recovery of a rock type. */
export const elementFields = (p: PitParams): string[] => p.elements.map((e) => e.field);

/** Where in the form «Породы» a refusal of the core belongs (`param` of the body: `rocks.by…`, `rocks.types.<i>…`). */
export function rocksIssueOf(r: Refusal): FormIssue {
  const p = r.param ?? "";
  const field = p.startsWith("rocks.by") ? "rock_by" : p.startsWith("rocks") ? "rock_types" : r.code === "block_field_not_found" || r.code === "filter_field_missing" ? "rock_by" : "general";
  return issue(field, r.message, r.hint, r.param);
}

/** Where in the form «Углы бортов» a refusal of the core belongs (`slopes.table.<i>.angle`, `slopes.domains…`, `slopes.tolerance`, `k`). */
export function slopesIssueOf(r: Refusal): FormIssue {
  const p = r.param ?? "";
  const field = p === "k" ? "k" : p.startsWith("slopes.tolerance") ? "tolerance" : p.startsWith("slopes.domain") ? "domains" : p.startsWith("slopes") ? "slopes" : r.code === "block_field_not_found" ? "domains" : "general";
  return issue(field, r.message, r.hint, r.param);
}

/** Where in the form «Календарь» a refusal of the core belongs (`calendar.q_ore`, `calendar.q_mass`, `calendar.rate`). */
export function calendarIssueOf(r: Refusal): FormIssue {
  const p = r.param ?? "";
  const field = p.startsWith("calendar.q_ore") ? "q_ore" : p.startsWith("calendar.q_mass") ? "q_mass" : p.startsWith("calendar.rate") ? "rate" : "general";
  return issue(field, r.message, r.hint, r.param);
}

/** `pit.rocks {scenario_id?, rock_field?, rock_rules?, rock_types?}`: without them — the window; with them — the rocks of the scenario are changed. */
export async function runRocks(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (!hasRocks(a)) {
    await openRocksWindow(id);
    return `Открыто окно «${ROCKS_TITLE}»`;
  }
  const detail = await freshScenario(id);
  const fail = (i: FormIssue): never => {
    setRocksIssue(i);
    throw refusal("bad_param", i.message, i.hint);
  };
  const got = rocksFormOfArgs(a, detail.params);
  if (!got.ok) return fail(got.issue);
  const patch = rocksPatch(got.form, elementFields(detail.params), limitsNow());
  if (!patch.ok) return fail(patch.issue);
  if (source === "user" || windowOfRocks()) pitStore.set({ rocksForm: got.form, rocksIssue: null });
  try {
    const r = await applyParams(id, patch.patch);
    setRocksIssue(null);
    return r.text;
  } catch (e) {
    setRocksIssue(rocksIssueOf(refusalOf(e)));
    throw e;
  }
}

/** `pit.slopes {scenario_id?, table?, domain_field?, domains?, k?, tolerance?}`: without them — the window; with them — the slopes of the scenario are changed. */
export async function runSlopes(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (!hasSlopes(a)) {
    await openSlopesWindow(id);
    return `Открыто окно «${SLOPES_TITLE}»`;
  }
  const detail = await freshScenario(id);
  const fail = (i: FormIssue): never => {
    setSlopesIssue(i);
    throw refusal("bad_param", i.message, i.hint);
  };
  const got = slopesFormOfArgs(a, detail.params);
  if (!got.ok) return fail(got.issue);
  const patch = slopesPatch(got.form, limitsNow());
  if (!patch.ok) return fail(patch.issue);
  if (source === "user" || windowOfSlopes()) pitStore.set({ slopesForm: got.form, slopesIssue: null });
  try {
    const r = await applyParams(id, patch.patch);
    setSlopesIssue(null);
    return `${r.text} Шаблон дуг зависит от углов: проверьте его командой «Проверить шаблон».`;
  } catch (e) {
    setSlopesIssue(slopesIssueOf(refusalOf(e)));
    throw e;
  }
}

/** `pit.calendar {scenario_id?, q_ore?, q_mass?, rate_pct?}`: without them — the window; with them — the calendar of the scenario is changed (the result stays valid). */
export async function runCalendar(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = scenarioIdOf(app, a);
  if (!hasCalendar(a)) {
    await openCalendarWindow(id);
    return `Открыто окно «${CALENDAR_TITLE}»`;
  }
  const detail = await freshScenario(id);
  const form = calendarFormOfArgs(a, detail.params);
  const patch = calendarPatch(form);
  if (!patch.ok) {
    setCalendarIssue(patch.issue);
    throw refusal("bad_param", patch.issue.message, patch.issue.hint);
  }
  if (source === "user" || windowOfCalendar()) pitStore.set({ calendarForm: form, calendarIssue: null });
  try {
    const r = await applyParams(id, patch.patch);
    setCalendarIssue(null);
    return r.text;
  } catch (e) {
    setCalendarIssue(calendarIssueOf(refusalOf(e)));
    throw e;
  }
}

// ---- the binding (one line each in `bindPit`) ----

export function bindRocks(app: App): void {
  app.registry.bind("pit.rocks", noted(app, (a, s) => runRocks(app, a, s)), needWritable(app));
}

export function bindSlopes(app: App): void {
  app.registry.bind("pit.slopes", noted(app, (a, s) => runSlopes(app, a, s)), needWritable(app));
}

export function bindCalendar(app: App): void {
  app.registry.bind("pit.calendar", noted(app, (a, s) => runCalendar(app, a, s)), needWritable(app));
}
