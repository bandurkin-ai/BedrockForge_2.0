/**
 * The input of the panel «Claude» (Э23, 23.14; ADR 0029 решение 33): Enter sends, Shift+Enter is a new line, «Стоп» takes the place of «Отправить» while a
 * turn runs (`stopTurn`). A question longer than the contract allows (20 000 characters) is not cut silently: the field stops at the limit. The draft survives
 * a switch of the tab of the dock (a module variable, nothing is saved). `ComposerView` is pure (the tests call it); `Composer` keeps the text and talks to `state.ts`.
 */
import { useState, type KeyboardEvent } from "react";
import { ask, stopTurn, type AskResult } from "./state";
import { LIMITS } from "./types";
import css from "./Composer.module.css";

export type ComposerKeyAction = "send" | "none";

/** Enter alone sends; Shift/Ctrl/Alt+Enter and Enter of an input method (a composition is not finished) do not. */
export function composerKey(e: { key: string; shiftKey?: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean; isComposing?: boolean }): ComposerKeyAction {
  if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return "none";
  return "send";
}

const MIN_ROWS = 2;
const MAX_ROWS = 8;
export const rowsOf = (text: string): number => Math.min(MAX_ROWS, Math.max(MIN_ROWS, text.split("\n").length));

export interface ComposerViewProps {
  value: string;
  onChange: (text: string) => void;
  onSend: () => void;
  onStop: () => void;
  /** a turn runs: «Стоп» instead of «Отправить», the field stays editable */
  running: boolean;
  /** why a question cannot be sent now (not connected, no internet, login); null — it can */
  blocked: string | null;
  /** the refusal of the last attempt (`ask` → `{ok: false}`) */
  error?: { message: string; hint: string } | null;
}

export function ComposerView({ value, onChange, onSend, onStop, running, blocked, error = null }: ComposerViewProps) {
  const empty = value.trim().length === 0;
  const keyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (composerKey({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey, metaKey: e.metaKey, isComposing: e.nativeEvent.isComposing }) !== "send") return;
    e.preventDefault();
    if (!running && !blocked && !empty) onSend();
  };
  return (
    <form
      className={css.composer}
      data-testid="composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (!running && !blocked && !empty) onSend();
      }}
    >
      {error ? (
        <div className={css.error} role="alert" data-testid="composer-error">
          {error.message} {error.hint}
        </div>
      ) : null}
      <textarea
        className={css.input}
        data-testid="composer-input"
        aria-label="Вопрос Claude"
        placeholder={blocked ?? "Спросите о проекте или попросите выполнить команду · Enter — отправить, Shift+Enter — новая строка"}
        title={blocked ?? undefined}
        value={value}
        rows={rowsOf(value)}
        maxLength={LIMITS.text}
        disabled={blocked !== null}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={keyDown}
      />
      <div className={css.row}>
        {value.length > LIMITS.text * 0.9 ? (
          <span className={css.count}>
            {value.length} из {LIMITS.text}
          </span>
        ) : null}
        <span className={css.grow} />
        {running ? (
          <button type="button" className={css.stop} data-testid="composer-stop" onClick={onStop} title="Остановить ответ">
            Стоп
          </button>
        ) : (
          <button type="submit" className={css.send} data-testid="composer-send" disabled={blocked !== null || empty} title={blocked ?? "Отправить (Enter)"}>
            Отправить
          </button>
        )}
      </div>
    </form>
  );
}

let draft = "";

export interface ComposerProps {
  running: boolean;
  blocked: string | null;
  /** replaces `ask` (the tests) */
  onAsk?: (text: string) => Promise<AskResult>;
  onStop?: () => void;
}

export function Composer({ running, blocked, onAsk = ask, onStop = () => void stopTurn() }: ComposerProps) {
  const [text, setText] = useState(draft);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const change = (t: string) => {
    draft = t;
    setText(t);
    if (error) setError(null);
  };
  const send = () => {
    const q = text.trim();
    if (!q) return;
    void onAsk(q).then((r) => {
      if (r.ok) change("");
      else setError({ message: r.message, hint: r.hint });
    });
  };
  return <ComposerView value={text} onChange={change} onSend={send} onStop={onStop} running={running} blocked={blocked} error={error} />;
}
