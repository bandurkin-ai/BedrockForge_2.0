/**
 * Small parts of the window «Блочная модель · …»: a text field that is written when it loses the focus or Enter is pressed
 * (not on every key — each write is a new request of the display), the words under a field of the form, and the
 * memory of the wrong values the person typed (they are not written, the field keeps them and tells why). React.
 */
import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import css from "../../../shell/PropsParts.module.css";
import type { FormField, FormIssue } from "./style";

export function TextField({
  value,
  onCommit,
  label,
  testId,
  invalid,
  width,
  placeholder,
  disabled,
  title,
}: {
  value: string;
  onCommit: (text: string) => void;
  label: string;
  testId: string;
  invalid?: boolean;
  width?: number | string;
  placeholder?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const done = () => {
    if (draft !== value) onCommit(draft);
  };
  const key = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      done();
    }
  };
  return (
    <input
      className={css.input}
      style={width === undefined ? undefined : { width }}
      value={draft}
      placeholder={placeholder}
      disabled={disabled}
      title={title}
      aria-label={label}
      aria-invalid={invalid || undefined}
      data-testid={testId}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={done}
      onKeyDown={key}
    />
  );
}

/** The words under a field of the form, in Russian with the hint (the same as the core's kind of refusal). */
export function Issue({ text, testId }: { text: string | null | undefined; testId: string }) {
  if (!text) return null;
  return (
    <p className={css.error} role="alert" data-testid={testId}>
      {text}
    </p>
  );
}

export type Issues = Partial<Record<FormField, string>>;

/**
 * The wrong values typed in the form by field: `apply` takes the answer of an edit (null — written, the field is clear;
 * else the place and the words). A stored style that is wrong is told by the panel itself (`colourIssueAt`).
 */
export function useIssues(): { issues: Issues; apply: (field: FormField, r: FormIssue | null) => void; clear: () => void } {
  const [issues, set] = useState<Issues>({});
  const apply = useCallback((field: FormField, r: FormIssue | null) => {
    set((cur) => {
      const next = { ...cur };
      delete next[field];
      if (r) next[r.field] = r.text;
      return next;
    });
  }, []);
  const clear = useCallback(() => set({}), []);
  return { issues, apply, clear };
}
