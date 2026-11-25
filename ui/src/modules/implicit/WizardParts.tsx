/**
 * The parts the steps of the wizard «Новая модель» share: the words under a field (the first wrong thing of the form or the refusal of
 * the core, with the place of the body it is about), a typed number with its range. Everything is told in Russian; a window is
 * floating, not modal.
 */
import css from "../../shell/PropsParts.module.css";
import { numberText } from "../composites/params";
import { issueAt } from "../estimation/formBase";
import type { Range } from "./api";
import { rangesOf, type ModelForm } from "./form";
import { useSel } from "../estimation/hooks";
import { setForm, wizardStore } from "./wizardState";

/** The words under the field `at` (the path of the body, or the fields inside it). */
export function IssueAt({ at, id }: { at: string | string[]; id: string }) {
  const issue = useSel(wizardStore, (s) => s.issue);
  const hit = issueAt(issue, ...(Array.isArray(at) ? at : [at]));
  if (!hit) return null;
  return (
    <p className={css.error} role="alert" data-testid={`imp-issue-${id}`} data-code={hit.code}>
      {hit.message} {hit.hint}
    </p>
  );
}

/** «0,1–100 м»: the range of a field as the core gives it. */
export function rangeText(r: Range | undefined): string {
  if (!r) return "";
  const unit = r.unit ? ` ${r.unit}` : "";
  if (r.lo !== null && r.hi !== null) return `${r.lo_open ? "> " : ""}${numberText(r.lo)}–${numberText(r.hi)}${unit}`;
  if (r.lo !== null) return `${r.lo_open ? "> " : "≥ "}${numberText(r.lo)}${unit}`;
  return r.hi !== null ? `≤ ${numberText(r.hi)}${unit}` : "";
}

/** The ranges of the form: the answer of the core when it came, else the mirror. */
export const useRanges = (): Record<string, Range> => rangesOf(useSel(wizardStore, (s) => s.defaults.data));

/** A typed number: the label, the field (text — «0,5»), the placeholder (the default), the range under it, the refusal. */
export function NumberRow({ label, field, rangeKey, at, id, placeholder, note }: { label: string; field: keyof ModelForm; rangeKey?: string; at: string; id: string; placeholder?: string; note?: string }) {
  const value = useSel(wizardStore, (s) => s.form[field] as string);
  const ranges = useRanges();
  const hint = rangeKey ? rangeText(ranges[rangeKey]) : "";
  return (
    <>
      <label className={css.formRow}>
        <span>{label}</span>
        <input className={css.input} value={value} placeholder={placeholder} inputMode="decimal" spellCheck={false} aria-label={label} title={hint ? `Диапазон: ${hint}` : undefined} data-testid={`imp-${id}`} onChange={(e) => setForm({ [field]: e.target.value } as Partial<ModelForm>)} />
      </label>
      {(hint || note) && (
        <span className={css.note} data-testid={`imp-${id}-note`}>
          {[hint ? `Диапазон: ${hint}.` : "", note ?? ""].filter(Boolean).join(" ")}
        </span>
      )}
      <IssueAt at={at} id={id} />
    </>
  );
}

export const nf = (x: number) => x.toLocaleString("ru-RU");
export const g = (x: number | null) => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));
