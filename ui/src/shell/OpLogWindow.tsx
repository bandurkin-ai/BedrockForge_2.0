import { bridge } from "../bridge";
import { useStore } from "./useStore";
import { logFileName, logText, opLogStore, openLogWindow } from "./opLog";
import css from "./OpLogWindow.module.css";

/** The log of one operation (ТЗ 7.9): «Копировать», «Сохранить .txt», «Обновить». */
export function OpLogWindow({ logId }: { logId?: number }) {
  useStore(opLogStore, (s) => s.version);
  const logs = opLogStore.get().logs;
  const log = logId === undefined ? undefined : logs.find((l) => l.id === logId);
  if (!log) return <LogList />;
  const text = logText(log);
  return (
    <div className={css.wrap}>
      <div className={css.bar}>
        <span className={log.failed ? css.failed : css.ok}>{log.failed ? "С ошибками" : "Без ошибок"}</span>
        <button type="button" onClick={() => bridge()?.copyText(text)}>
          Копировать
        </button>
        <button type="button" onClick={() => void bridge()?.saveText(logFileName(log), text)}>
          Сохранить .txt
        </button>
        <button type="button" onClick={() => opLogStore.set((s) => ({ version: s.version + 1 }))}>
          Обновить
        </button>
      </div>
      <pre className={css.text} data-testid="op-log-text">
        {text}
      </pre>
    </div>
  );
}

/** «Журналы»: the last 30 operation logs of the session (`logs.open`). */
function LogList() {
  const logs = useStore(opLogStore, (s) => s.logs);
  if (!logs.length) return <div className={css.wrap}>Журналов в этом сеансе ещё нет: они появляются при импорте и экспорте.</div>;
  return (
    <div className={css.wrap}>
      <ul className={css.list}>
        {logs.map((l) => (
          <li key={l.id}>
            <button type="button" className={l.failed ? css.failed : ""} onClick={() => openLogWindow(l.id)}>
              {l.started.toLocaleTimeString("ru-RU")} — {l.title}
              {l.failed ? " (ошибки)" : ""}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
