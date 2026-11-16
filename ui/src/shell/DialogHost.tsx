import { useEffect, useRef, useState } from "react";
import { bridge } from "../bridge";
import { acceptText, closeDialog, dialogStore, type DialogRequest } from "./dialogs";
import { useStore } from "./useStore";
import css from "./Dialog.module.css";

/** Draws the head of the dialog queue (ТЗ 7.0.11): Enter — main button, Esc or a click outside — cancel. */
export function DialogHost() {
  const queue = useStore(dialogStore, (s) => s.queue);
  const head = queue[0];
  if (!head) return null;
  return <DialogView key={head.id} req={head} />;
}

function DialogView({ req }: { req: DialogRequest }) {
  const [text, setText] = useState(req.kind === "text" ? req.options.initial ?? "" : "");
  const okRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const finish = (ok: boolean) => {
    closeDialog(req.id);
    if (req.kind === "confirm") req.resolve(ok);
    else if (req.kind === "text") req.resolve(ok ? acceptText(text, req.options.allowEmpty) : null);
    else req.resolve();
  };
  const canOk = req.kind !== "text" || acceptText(text, req.options.allowEmpty) !== null;

  useEffect(() => {
    (inputRef.current ?? okRef.current)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        finish(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  const title = req.kind === "confirm" ? req.options.title ?? "Подтвердите действие" : req.kind === "text" ? req.options.title : req.options.title ?? "Сообщение";
  const okLabel = req.kind === "message" ? "Закрыть" : req.options.okLabel ?? "ОК";
  const danger = req.kind === "confirm" && req.options.danger;
  const log = req.kind === "message" ? req.options.log : undefined;

  return (
    <div className={css.backdrop} onMouseDown={(e) => e.target === e.currentTarget && finish(false)}>
      <div className={css.dialog} role="dialog" aria-modal="true" aria-label={title}>
        <div className={css.title}>{title}</div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canOk) finish(true);
          }}
        >
          <div className={css.body}>
            {req.kind === "text" ? (
              <label>
                {req.options.label}
                <input ref={inputRef} className={css.input} value={text} onChange={(e) => setText(e.target.value)} aria-label={req.options.label ?? title} />
              </label>
            ) : (
              req.options.message
            )}
            {log && <div className={css.log}>{log}</div>}
          </div>
          <div className={css.buttons}>
            {log && (
              <>
                <button type="button" className={`${css.btn} ${css.left}`} onClick={() => bridge()?.copyText(log)}>
                  Скопировать
                </button>
                <button type="button" className={css.btn} onClick={() => void bridge()?.saveText("журнал.txt", log)}>
                  Сохранить .txt
                </button>
              </>
            )}
            {req.kind !== "message" && (
              <button type="button" className={css.btn} onClick={() => finish(false)}>
                Отмена
              </button>
            )}
            <button ref={okRef} type="submit" className={`${css.btn} ${danger ? css.danger : css.primary}`} disabled={!canOk}>
              {okLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
