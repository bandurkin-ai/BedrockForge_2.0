/**
 * The form of the window «Календарь карьера» (ТЗ 8.11.4, 8.11.5; ADR 0028 решение 19, 29; docs/progress/e21-api.md §2, §15 п. 11): the ore capacity Q_р and the mass capacity Q_гм (t per
 * year; Q_гм empty — no limit) and the discount rate r (the window and the command `pit.calendar` speak in percent, the API keeps a share: 8 % is 0,08). The calendar only shows
 * the NPV: it is not in `params_hash`, a change of it does not make the result old. Everything wrong is told here in Russian with a hint and the place in the form. No React.
 */
import { numberText, parseDecimal } from "../blocks/report/state";
import { DEFAULT_PARAMS, type PitParams } from "./answers";
import { has, issue, txt, type Args, type FormIssue, type Got } from "./forms";

export const MAX_Q = 1e12;
export const MAX_RATE_PCT = 50;

export interface CalendarForm {
  /** Q_р, t/year; "" — not set (the NPV is not counted) */
  qOre: string;
  /** Q_гм, t/year; "" — no limit */
  qMass: string;
  /** r, percent per year */
  ratePct: string;
}

/** A share → percent without the noise of the multiplication (0,07 → 7). */
export const pctOf = (share: number): number => Math.round(share * 100 * 1e9) / 1e9;
/** Percent → a share (7,5 → 0,075). */
export const shareOf = (pct: number): number => Math.round((pct / 100) * 1e12) / 1e12;

export const EMPTY_CALENDAR: CalendarForm = { qOre: "", qMass: "", ratePct: numberText(pctOf(DEFAULT_PARAMS.calendar.rate)) };

export const calendarFormOfParams = (p: PitParams): CalendarForm => ({
  qOre: p.calendar.q_ore === null ? "" : numberText(p.calendar.q_ore),
  qMass: p.calendar.q_mass === null ? "" : numberText(p.calendar.q_mass),
  ratePct: numberText(pctOf(p.calendar.rate)),
});

export type CalendarPatch = Pick<PitParams, "calendar">;

const capacity = (text: string, field: FormIssue["field"], what: string, param: string): { ok: true; value: number | null } | { ok: false; issue: FormIssue } => {
  if (!text.trim()) return { ok: true, value: null };
  const n = parseDecimal(text);
  if (n === null) return { ok: false, issue: issue(field, `${what} — не число.`, "Введите т/год, например 1 000 000; пусто — не задано.", param) };
  if (!(n > 0) || n > MAX_Q) return { ok: false, issue: issue(field, `${what} должна быть больше нуля.`, "Производительность — положительное число, т/год.", param) };
  return { ok: true, value: n };
};

/** The form «Календарь» → `params.calendar` (the rate as a share). */
export function calendarPatch(f: CalendarForm): Got<CalendarPatch> {
  const qOre = capacity(f.qOre, "q_ore", "Производительность по руде Q_р", "calendar.q_ore");
  if (!qOre.ok) return qOre;
  const qMass = capacity(f.qMass, "q_mass", "Производительность по горной массе Q_гм", "calendar.q_mass");
  if (!qMass.ok) return qMass;
  const pct = f.ratePct.trim() ? parseDecimal(f.ratePct) : pctOf(DEFAULT_PARAMS.calendar.rate);
  if (pct === null) return { ok: false, issue: issue("rate", "Ставка r — не число.", "Ставка дисконтирования, % в год, например 8.", "calendar.rate") };
  if (pct < 0 || pct > MAX_RATE_PCT) return { ok: false, issue: issue("rate", `Ставка r ${numberText(pct)} % вне диапазона 0…50 %.`, "Ставка дисконтирования — от 0 до 50 % в год; по умолчанию 8.", "calendar.rate") };
  return { ok: true, patch: { calendar: { q_ore: qOre.value, q_mass: qMass.value, rate: shareOf(pct) } } };
}

export const CALENDAR_KEYS = ["q_ore", "q_mass", "rate_pct"] as const;
export const hasCalendar = (a: Args): boolean => CALENDAR_KEYS.some((k) => has(a, k));

/** The arguments of `pit.calendar` from the form (`rate_pct` — percent, not a share). */
export function calendarArgs(f: CalendarForm, scenarioId: number | null): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = calendarPatch(f);
  if (!got.ok) return got;
  const c = got.patch.calendar;
  const a: Args = {};
  if (scenarioId !== null) a.scenario_id = scenarioId;
  a.q_ore = c.q_ore;
  a.q_mass = c.q_mass;
  a.rate_pct = pctOf(c.rate);
  return { ok: true, args: a };
}

/** The form that shows the arguments of a call (a key that is not in it stays as `base` has it). */
export function calendarFormOfArgs(a: Args, base: PitParams): CalendarForm {
  const f = calendarFormOfParams(base);
  if (has(a, "q_ore")) f.qOre = txt(a.q_ore);
  if (has(a, "q_mass")) f.qMass = txt(a.q_mass);
  if (has(a, "rate_pct")) f.ratePct = txt(a.rate_pct);
  return f;
}
