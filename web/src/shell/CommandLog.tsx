import type { CommandSource, LogEntry } from "./commands";

const SOURCE: Record<CommandSource, string> = { user: "Вы", claude: "Claude", remote: "MCP" };

function formatArgs(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`);
  return parts.join(", ");
}

/** Bottom dock: every executed command, with who ran it. Keeps Claude's actions visible. */
export function CommandLog({ entries }: { entries: LogEntry[] }) {
  if (entries.length === 0) {
    return <p className="muted small log-empty">Здесь появятся выполненные команды: ваши, ассистента Claude и внешних клиентов MCP.</p>;
  }
  return (
    <div className="table-wrap log-table">
      <table>
        <thead>
          <tr><th>Время</th><th>Кто</th><th>Команда</th><th>Параметры</th><th>Результат</th></tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id} className={e.ok ? "" : "failed"}>
              <td>{e.time.toLocaleTimeString("ru-RU")}</td>
              <td><span className={`source ${e.source}`}>{SOURCE[e.source]}</span></td>
              <td>{e.title}</td>
              <td className="mono">{formatArgs(e.args)}</td>
              <td className="wrap">{e.result}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
