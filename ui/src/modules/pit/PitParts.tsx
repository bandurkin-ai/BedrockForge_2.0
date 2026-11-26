/**
 * The small parts the windows of the pit optimisation share (ТЗ 8.11.5; ADR 0028): the store hook, a group of a form, the words under a field (the first
 * wrong thing of the form or the refusal of the core, in its place), a field with a number. The windows of 21.17–21.19 use them too.
 */
import { useSyncExternalStore, type ReactNode } from "react";
import css from "../../shell/PropsParts.module.css";
import type { Store } from "../../shell/store";
import type { FormField, FormIssue } from "./forms";
import { pitStore, type PitState } from "./state";

export function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

export function Fieldset({ title, disabled = false, children }: { title: string; disabled?: boolean; children: ReactNode }) {
  return (
    <fieldset className={css.group} disabled={disabled}>
      <legend>{title}</legend>
      {children}
    </fieldset>
  );
}

/** The words under a field: `which` is the form (`scenario`, `economics`, `rocks`, `slopes`, `calendar`), `field` — the place; nothing while the form has nothing wrong there. */
const ISSUES = { scenario: (s: PitState) => s.scenarioIssue, economics: (s: PitState) => s.econIssue, rocks: (s: PitState) => s.rocksIssue, slopes: (s: PitState) => s.slopesIssue, calendar: (s: PitState) => s.calendarIssue };

export function Issue({ which, field }: { which: keyof typeof ISSUES; field: FormField }) {
  const issue = useSel(pitStore, ISSUES[which]);
  if (!issue || issue.field !== field) return null;
  return (
    <p className={css.error} role="alert" data-testid={`pit-issue-${field}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

/** Does the issue name this row of the elements (`elements.<i>.…`)? */
export const issueAtRow = (issue: FormIssue | null, i: number): boolean => !!issue && issue.field === "elements" && (issue.param ?? "").startsWith(`elements.${i}.`);

/** A labelled text field of a number (a decimal comma is fine). */
export function NumberField({ label, value, onChange, placeholder, testId, hint, disabled }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; testId: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={css.field} title={hint}>
      <span>{label}</span>
      <input className={css.input} value={value} inputMode="decimal" placeholder={placeholder} aria-label={label} data-testid={testId} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
