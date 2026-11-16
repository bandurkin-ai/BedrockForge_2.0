import { useEffect, useState } from "react";
import { bridge } from "../bridge";
import { useApp } from "./AppContext";
import { useStore } from "./useStore";
import css from "./CoreDown.module.css";

export const RETRY_MS = 3000;

/** «Вычислительное ядро недоступно» (ТЗ 7.0.2): reason, automatic retry every 3 s, log buttons. */
export function CoreDown() {
  const app = useApp();
  const core = useStore(app.store, (s) => s.core);
  const reason = useStore(app.store, (s) => s.coreMessage);
  const [left, setLeft] = useState(RETRY_MS / 1000);

  useEffect(() => {
    if (core !== "offline") return;
    setLeft(RETRY_MS / 1000);
    const tick = setInterval(() => setLeft((x) => (x <= 1 ? RETRY_MS / 1000 : x - 1)), 1000);
    const retry = setInterval(() => bridge()?.coreRetry(), RETRY_MS);
    return () => {
      clearInterval(tick);
      clearInterval(retry);
    };
  }, [core]);

  if (core !== "offline") return null;
  const log = () => bridge()?.coreLog() ?? "";
  return (
    <div className={css.overlay} role="alert">
      <div className={css.box}>
        <h2>Вычислительное ядро недоступно</h2>
        <div className={css.reason}>{reason ?? "Нет связи с ядром."}</div>
        <div>Повтор через {left} с…</div>
        <div className={css.buttons}>
          <button type="button" className={css.btn} onClick={() => bridge()?.coreRetry()}>
            Повторить сейчас
          </button>
          <button type="button" className={css.btn} onClick={() => bridge()?.copyText(log())}>
            Скопировать журнал
          </button>
          <button type="button" className={css.btn} onClick={() => void bridge()?.saveText("bf-core.log.txt", log())}>
            Сохранить .txt
          </button>
        </div>
      </div>
    </div>
  );
}
