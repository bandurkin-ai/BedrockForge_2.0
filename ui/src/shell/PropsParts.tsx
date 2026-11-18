/**
 * Small parts of the «Свойства» panels of objects (ADR 0011 §5): the list of facts, a colour that is saved
 * when the picker closes (not on every move inside it), a slider that is saved when it is released, and the
 * way a panel runs a registry command and shows why it failed.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { useApp } from "./AppContext";
import { num, type Fact } from "./props";
import css from "./PropsParts.module.css";

export function Facts({ facts }: { facts: readonly Fact[] }) {
  if (!facts.length) return null;
  return (
    <dl className={css.facts}>
      {facts.map((f) => (
        <div key={f.label} style={{ display: "contents" }}>
          <dt>{f.label}</dt>
          <dd>{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** React's `onChange` of a colour or a range input fires on every move; the native `change` — when the value
 * is committed (the picker is closed, the slider is released, a key is pressed). */
function useCommitted(ref: RefObject<HTMLInputElement>, onCommit: (value: string) => void): void {
  const latest = useRef(onCommit);
  latest.current = onCommit;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const done = () => latest.current(el.value);
    el.addEventListener("change", done);
    return () => el.removeEventListener("change", done);
  }, [ref]);
}

/** What a field does with a committed value; `false` — it was not saved: the field shows the stored one again. */
type Commit<T> = (value: T) => void | Promise<boolean>;

/** A check box that answers at once and goes back when the change was not saved. */
export function Check({ checked, title, disabled, onCommit, children }: { checked: boolean; title?: string; disabled?: boolean; onCommit: Commit<boolean>; children: ReactNode }) {
  const [draft, setDraft] = useState(checked);
  useEffect(() => setDraft(checked), [checked]);
  return (
    <label className={css.check} title={title}>
      <input
        type="checkbox"
        checked={draft}
        disabled={disabled}
        onChange={(e) => {
          const next = e.target.checked;
          setDraft(next);
          void Promise.resolve(onCommit(next)).then((ok) => ok === false && setDraft(checked));
        }}
      />
      {children}
    </label>
  );
}

/** A colour field: `onCommit` runs once — when the picker is closed or the value is typed. */
export function ColorField({ value, label, disabled, onCommit }: { value: string; label: string; disabled?: boolean; onCommit: Commit<string> }) {
  const ref = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  useCommitted(ref, (v) => {
    if (v !== value) void Promise.resolve(onCommit(v)).then((ok) => ok === false && setDraft(value));
  });
  return <input ref={ref} type="color" className={css.color} value={draft} aria-label={label} title={label} disabled={disabled} onChange={(e) => setDraft(e.target.value)} />;
}

/** A slider with its value: it follows the pointer and is saved when released (or changed by a key). */
export function Slider({ value, min, max, step, label, disabled, onCommit }: { value: number; min: number; max: number; step: number; label: string; disabled?: boolean; onCommit: Commit<number> }) {
  const ref = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  useCommitted(ref, (v) => {
    const next = Number(v);
    if (Number.isFinite(next) && Math.abs(next - value) > 1e-9) void Promise.resolve(onCommit(next)).then((ok) => ok === false && setDraft(value));
  });
  return (
    <>
      <input ref={ref} type="range" className={css.slider} min={min} max={max} step={step} value={draft} aria-label={label} disabled={disabled} onChange={(e) => setDraft(Number(e.target.value))} />
      <span className={css.value}>{num(draft, 2)}</span>
    </>
  );
}

/** Runs registry commands for a panel: `true` — done; a failure is kept as the text to show under the panel.
 * The commands of a panel run one after another: each change carries the version the previous one left. */
export function useRun(): { run: (id: string, args?: Record<string, unknown>) => Promise<boolean>; error: string | null; setError: (text: string | null) => void } {
  const app = useApp();
  const [error, setError] = useState<string | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const run = useCallback(
    (id: string, args: Record<string, unknown> = {}) => {
      const next = queue.current.then(async () => {
        const r = await app.registry.execute(id, args);
        setError(r.ok ? null : r.result);
        return r.ok;
      });
      queue.current = next;
      return next;
    },
    [app],
  );
  return { run, error, setError };
}
