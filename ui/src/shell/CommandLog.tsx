import { useApp } from "./AppContext";
import { formatArgs } from "./registry";
import { useRegistryVersion } from "./useStore";
import shell from "./Shell.module.css";
import css from "./CommandLog.module.css";

/** Command log (ТЗ 7.0.8): last 200 entries, newest first; failed rows highlighted. */
export function CommandLog() {
  const app = useApp();
  useRegistryVersion(app.registry);
  const entries = app.registry.entries();
  if (!entries.length) return <div className={shell.empty}>Здесь появятся выполненные команды: ваши и ассистента Claude.</div>;
  const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <table className={css.table} aria-label="Журнал команд">
      <thead>
        <tr>
          <th>Время</th>
          <th>Кто</th>
          <th>Команда</th>
          <th>Параметры</th>
          <th>Результат</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => (
          <tr key={e.id} className={e.ok ? undefined : css.failed}>
            <td>{time.format(e.time)}</td>
            <td>{e.source === "claude" ? "Claude" : "Вы"}</td>
            <td>{e.title}</td>
            <td>{formatArgs(e.args)}</td>
            <td>{e.result}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
