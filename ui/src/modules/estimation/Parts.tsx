/**
 * The parts the steps of the wizard share: the words under a field (the first wrong thing of the form or the refusal of the core,
 * with the place of the body it is about), three numbers X / Y / Z. Everything is told in Russian; a window is floating, not modal.
 */
import css from "../../shell/PropsParts.module.css";
import { issueAt } from "./form";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { wizardStore } from "./wizardState";

/** The words under the field `at` (the path of the body, or the fields inside it). */
export function IssueAt({ at, id }: { at: string | string[]; id: string }) {
  const issue = useSel(wizardStore, (s) => s.issue);
  const hit = issueAt(issue, ...(Array.isArray(at) ? at : [at]));
  if (!hit) return null;
  return (
    <p className={css.error} role="alert" data-testid={`est-issue-${id}`} data-code={hit.code}>
      {hit.message} {hit.hint}
    </p>
  );
}

export function Triple({ labels, values, onChange, testId, invalid }: { labels: readonly string[]; values: readonly string[]; onChange: (next: [string, string, string]) => void; testId: string; invalid?: boolean }) {
  return (
    <div className={mod.row3}>
      {labels.map((l, i) => (
        <label key={l}>
          <span>{l}</span>
          <input className={css.input} value={values[i]} inputMode="numeric" spellCheck={false} aria-label={l} aria-invalid={invalid} data-testid={`${testId}-${i}`} onChange={(e) => onChange(values.map((v, k) => (k === i ? e.target.value : v)) as [string, string, string])} />
        </label>
      ))}
    </div>
  );
}

const nf = (x: number) => x.toLocaleString("ru-RU");
export const g = (x: number | null) => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));
export { nf };
