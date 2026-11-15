import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Icon } from "./icons";

/**
 * The application's own dialogs instead of the browser's confirm / prompt / alert:
 * styled like the rest of the interface, in the theme, with the keyboard (Enter, Escape).
 * Call `askConfirm`, `askText` or `showMessage` from anywhere; `<DialogHost />` (once, in App) draws them.
 */

interface Base {
  title: string;
  message?: string;
}
export interface ConfirmOptions extends Base {
  /** text of the main button (default «ОК»; for a dangerous action e.g. «Удалить») */
  ok?: string;
  cancel?: string;
  /** the main button is red: deleting, replacing */
  danger?: boolean;
}
export interface TextOptions extends Base {
  value?: string;
  placeholder?: string;
  ok?: string;
  /** empty text is allowed (e.g. «пусто — в корень группы») */
  allowEmpty?: boolean;
}

type Request =
  | { id: number; kind: "confirm"; options: ConfirmOptions; resolve: (v: boolean) => void }
  | { id: number; kind: "text"; options: TextOptions; resolve: (v: string | null) => void }
  | { id: number; kind: "message"; options: Base & { error?: boolean }; resolve: () => void };

let queue: Request[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

function enqueue(r: Request) {
  queue = [...queue, r];
  emit();
}
function done(id: number) {
  queue = queue.filter((r) => r.id !== id);
  emit();
}

/** «Are you sure?» — resolves true for the main button, false for «Отмена», Escape or a click outside. */
export function askConfirm(options: ConfirmOptions | string): Promise<boolean> {
  const o = typeof options === "string" ? { title: "Подтвердите действие", message: options } : options;
  return new Promise((resolve) => enqueue({ id: nextId++, kind: "confirm", options: o, resolve }));
}

/** A line of text (a name, a folder); null when cancelled. The text comes back trimmed. */
export function askText(options: TextOptions): Promise<string | null> {
  return new Promise((resolve) => enqueue({ id: nextId++, kind: "text", options, resolve }));
}

/** A message with one button. */
export function showMessage(options: (Base & { error?: boolean }) | string): Promise<void> {
  const o = typeof options === "string" ? { title: "Сообщение", message: options } : options;
  return new Promise((resolve) => enqueue({ id: nextId++, kind: "message", options: o, resolve }));
}

/** An error of an action shown in the application's dialog. */
export const showError = (e: unknown) => void showMessage({ title: "Не выполнено", message: (e as Error)?.message ?? String(e), error: true });

export function DialogHost() {
  const current = useSyncExternalStore(subscribe, () => queue[0] ?? null);
  if (!current) return null;
  return <Dialog key={current.id} request={current} />;
}

function Dialog({ request: r }: { request: Request }) {
  const [text, setText] = useState(r.kind === "text" ? r.options.value ?? "" : "");
  const input = useRef<HTMLInputElement>(null);
  const okRef = useRef<HTMLButtonElement>(null);

  const cancel = () => {
    done(r.id);
    if (r.kind === "confirm") r.resolve(false);
    else if (r.kind === "text") r.resolve(null);
    else r.resolve();
  };
  const accept = () => {
    if (r.kind === "text") {
      const v = text.trim();
      if (!v && !r.options.allowEmpty) return;
      done(r.id);
      r.resolve(v);
      return;
    }
    done(r.id);
    if (r.kind === "confirm") r.resolve(true);
    else r.resolve();
  };

  useEffect(() => {
    if (r.kind === "text") {
      input.current?.focus();
      input.current?.select();
    } else {
      okRef.current?.focus();
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        cancel();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const danger = r.kind === "confirm" && r.options.danger;
  const error = r.kind === "message" && r.options.error;
  const okText = r.kind === "message" ? "Закрыть" : r.options.ok ?? (danger ? "Удалить" : "ОК");
  return (
    <div className="modal-backdrop app-dialog-backdrop" onMouseDown={cancel}>
      <div
        className={`modal app-dialog${danger ? " danger" : ""}${error ? " error" : ""}`} role={r.kind === "message" ? "alertdialog" : "dialog"}
        aria-modal="true" aria-labelledby={`dlg-${r.id}`} onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <Icon name={danger ? "trash" : error ? "close" : "spark"} size={15} />
          <strong id={`dlg-${r.id}`} className="grow">{r.options.title}</strong>
          <button className="icon" onClick={cancel} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={14} /></button>
        </header>
        <form className="app-dialog-body" onSubmit={(e) => { e.preventDefault(); accept(); }}>
          {r.options.message && <p className="app-dialog-message">{r.options.message}</p>}
          {r.kind === "text" && (
            <input ref={input} value={text} placeholder={r.options.placeholder} aria-label={r.options.title} onChange={(e) => setText(e.target.value)} />
          )}
          <footer className="app-dialog-foot">
            {r.kind !== "message" && <button type="button" onClick={cancel}>{(r.kind === "confirm" && r.options.cancel) || "Отмена"}</button>}
            <button ref={okRef} type="submit" className={danger ? "danger-btn" : "primary"} disabled={r.kind === "text" && !text.trim() && !r.options.allowEmpty}>{okText}</button>
          </footer>
        </form>
      </div>
    </div>
  );
}
