import { useEffect, useState, useSyncExternalStore } from "react";
import { Icon } from "./icons";

/**
 * A dialog with several parameters (operations on lines and wireframes): the application's own,
 * like `dialogs.tsx`. `askForm` resolves with the values, or null when cancelled.
 */
export type FormValue = string | number | boolean;

export interface FormField {
  key: string;
  label: string;
  type: "number" | "text" | "select" | "checkbox" | "note";
  value?: FormValue;
  options?: { value: string; label: string }[];
  hint?: string;
  min?: number;
  step?: number;
  /** The field is shown only while this holds for the current values. */
  show?: (values: Record<string, FormValue>) => boolean;
  /** An empty number is allowed (comes back as ""). */
  optional?: boolean;
}

export interface FormOptions {
  title: string;
  message?: string;
  fields: FormField[];
  ok?: string;
  /** Returns an error text for values that cannot be accepted. */
  check?: (values: Record<string, FormValue>) => string | null;
}

interface Request {
  id: number;
  options: FormOptions;
  resolve: (v: Record<string, FormValue> | null) => void;
}

let queue: Request[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function askForm(options: FormOptions): Promise<Record<string, FormValue> | null> {
  return new Promise((resolve) => {
    queue = [...queue, { id: nextId++, options, resolve }];
    emit();
  });
}

export function FormHost() {
  const current = useSyncExternalStore(subscribe, () => queue[0] ?? null);
  if (!current) return null;
  return <Form key={current.id} request={current} />;
}

function Form({ request: r }: { request: Request }) {
  const [values, setValues] = useState<Record<string, FormValue>>(() =>
    Object.fromEntries(r.options.fields.filter((f) => f.type !== "note").map((f) => [f.key, f.value ?? (f.type === "checkbox" ? false : f.type === "select" ? f.options?.[0]?.value ?? "" : "")])));
  const [error, setError] = useState<string | null>(null);
  const close = (result: Record<string, FormValue> | null) => {
    queue = queue.filter((x) => x.id !== r.id);
    emit();
    r.resolve(result);
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(null);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = r.options.fields.filter((f) => !f.show || f.show(values));
  const accept = () => {
    const out: Record<string, FormValue> = {};
    for (const f of shown) {
      if (f.type === "note") continue;
      let v = values[f.key];
      if (f.type === "number") {
        const text = String(v).trim().replace(",", ".");
        if (text === "") {
          if (!f.optional) return setError(`Заполните поле «${f.label}»`);
          v = "";
        } else {
          const n = Number(text);
          if (!Number.isFinite(n)) return setError(`«${f.label}»: нужно число`);
          if (f.min !== undefined && n < f.min) return setError(`«${f.label}»: не меньше ${f.min}`);
          v = n;
        }
      }
      out[f.key] = v;
    }
    const problem = r.options.check?.(out) ?? null;
    if (problem) return setError(problem);
    close(out);
  };
  const set = (key: string, v: FormValue) => {
    setError(null);
    setValues((prev) => ({ ...prev, [key]: v }));
  };

  return (
    <div className="modal-backdrop app-dialog-backdrop" onMouseDown={() => close(null)}>
      <div className="modal app-dialog form-dialog" role="dialog" aria-modal="true" aria-labelledby={`form-${r.id}`} onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="gear" size={15} />
          <strong id={`form-${r.id}`} className="grow">{r.options.title}</strong>
          <button className="icon" onClick={() => close(null)} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={14} /></button>
        </header>
        <form className="app-dialog-body" onSubmit={(e) => { e.preventDefault(); accept(); }}>
          {r.options.message && <p className="app-dialog-message">{r.options.message}</p>}
          <div className="form-grid">
            {shown.map((f, i) => f.type === "note" ? (
              <p key={f.key} className="form-note muted small">{f.label}</p>
            ) : f.type === "checkbox" ? (
              <label key={f.key} className="form-check" title={f.hint}>
                <input type="checkbox" checked={Boolean(values[f.key])} onChange={(e) => set(f.key, e.target.checked)} /> {f.label}
              </label>
            ) : (
              <label key={f.key} className="form-row" title={f.hint}>
                <span>{f.label}</span>
                {f.type === "select" ? (
                  <select value={String(values[f.key])} onChange={(e) => set(f.key, e.target.value)} autoFocus={i === 0}>
                    {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : (
                  <input value={String(values[f.key] ?? "")} inputMode={f.type === "number" ? "decimal" : undefined} autoFocus={i === 0}
                    onChange={(e) => set(f.key, e.target.value)} />
                )}
                {f.hint && <small className="muted">{f.hint}</small>}
              </label>
            ))}
          </div>
          {error && <p className="form-error">{error}</p>}
          <footer className="app-dialog-foot">
            <button type="button" onClick={() => close(null)}>Отмена</button>
            <button type="submit" className="primary">{r.options.ok ?? "Выполнить"}</button>
          </footer>
        </form>
      </div>
    </div>
  );
}
