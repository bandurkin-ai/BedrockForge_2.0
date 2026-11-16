import { useEffect, useRef, useState } from "react";
import {
  cancelAccountLogin, checkAssistant, connectAssistantKey, disconnectAssistant, finishAccountLogin, getAssistantUsage,
  setAssistantPreferences, startAccountLogin, type AssistantStatus, type AssistantUsage,
} from "../api";
import { desktop, mcpUrl } from "../desktop";
import { askConfirm } from "./dialogs";
import { Icon } from "./icons";

export const EFFORT_TITLES: Record<string, [string, string]> = {
  low: ["Быстро", "Короткие рассуждения: простые вопросы, меньше расход"],
  medium: ["Обычно", "Баланс скорости и качества (по умолчанию)"],
  high: ["Тщательно", "Больше рассуждений: анализ данных, многошаговые действия"],
  xhigh: ["Очень тщательно", "Для сложных многошаговых задач"],
  max: ["Максимум", "Когда точность важнее времени и расхода"],
};

const MODE_TITLES: Record<string, string> = {
  key: "API-ключ",
  account: "Аккаунт Anthropic",
  env: "Ключ из настроек сервера (ANTHROPIC_API_KEY)",
};

const money = (v: number) => `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`;
const tokens = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(2)} млн` : v >= 1e3 ? `${(v / 1e3).toFixed(1)} тыс.` : String(v));

/** The round account button in the title bar, as in Office: the state of the connection at a glance. */
export function ClaudeAccountButton({ status, onClick }: { status: AssistantStatus | null; onClick: () => void }) {
  const on = !!status?.configured;
  const who = status?.account?.organization ?? status?.account?.email ?? (status?.mode ? MODE_TITLES[status.mode] : null);
  return (
    <button
      className={`claude-account${on ? " on" : ""}`} onClick={onClick}
      title={on ? `Claude подключён: ${who ?? ""}. Модель ${status?.model}. Нажмите — профиль и настройки` : "Claude не подключён: нажмите, чтобы войти"}
      aria-label={on ? "Профиль Claude" : "Подключить Claude"}
    >
      <span className="claude-avatar" aria-hidden>{on ? (who?.trim()[0]?.toUpperCase() ?? "C") : <Icon name="spark" size={13} />}</span>
      <i className="claude-dot" aria-hidden />
    </button>
  );
}

/**
 * «Claude: подключение» — sign in with an Anthropic account or an API key, the profile of the
 * connection, the model and depth of reasoning, and how much was spent.
 */
export function ClaudeAccount({ status, onStatus }: { status: AssistantStatus | null; onStatus: (s: AssistantStatus) => void }) {
  const [tab, setTab] = useState<"subscription" | "account" | "key">("subscription");
  const [key, setKey] = useState("");
  const [login, setLogin] = useState<{ id: string; url: string } | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [usage, setUsage] = useState<AssistantUsage | null>(null);
  const [period, setPeriod] = useState<"today" | "month" | "all">("month");
  const loginRef = useRef(login);
  loginRef.current = login;

  useEffect(() => {
    getAssistantUsage().then(setUsage).catch(() => setUsage(null));
  }, [status?.configured]);
  // A login left open when the window closes is cancelled on the server.
  useEffect(() => () => {
    if (loginRef.current) void cancelAccountLogin().catch(() => undefined);
  }, []);

  const run = (p: Promise<AssistantStatus | void>, ok?: string) => {
    setBusy(true);
    setNote(null);
    p.then((s) => {
      if (s) onStatus(s);
      const text = (s && s.message) || ok;
      if (text) setNote({ text, error: !!s?.warning });
    })
      .catch((e: Error) => setNote({ text: e.message, error: true }))
      .finally(() => setBusy(false));
  };

  const startLogin = () => {
    // The page is opened at once (an address set later would be blocked as a pop-up).
    // The desktop client opens the address in the system browser once it is known.
    const page = desktop ? null : window.open("about:blank", "_blank");
    setBusy(true);
    setNote(null);
    startAccountLogin()
      .then((r) => {
        setLogin({ id: r.login_id, url: r.url });
        if (desktop) void desktop.openExternal(r.url);
        else if (page) page.location.href = r.url;
      })
      .catch((e: Error) => {
        page?.close();
        setNote({ text: e.message, error: true });
      })
      .finally(() => setBusy(false));
  };

  if (!status) return <p className="muted small form-note">Загрузка…</p>;
  const models = status.models ?? [];
  const current = models.find((m) => m.id === status.model);
  const u = usage?.[period];

  return (
    <div className="claude-account-form">
      {status.configured ? (
        <section className="ca-card">
          <div className="ca-profile">
            <span className="claude-avatar big" aria-hidden>{(status.account?.organization ?? "C")[0].toUpperCase()}</span>
            <div className="grow">
              <strong>Claude подключён</strong>
              <div className="muted small">{MODE_TITLES[status.mode ?? ""] ?? ""}{status.key ? ` · ${status.key}` : ""}</div>
              {status.account && Object.entries(status.account).filter(([k]) => k !== "profile").map(([k, v]) => (
                <div key={k} className="muted small">{k}: {v}</div>
              ))}
              {status.connected_at && <div className="muted small">с {new Date(status.connected_at * 1000).toLocaleString("ru-RU")}</div>}
            </div>
          </div>
          <div className="ca-actions">
            <button disabled={busy} onClick={() => run(checkAssistant().then((r) => setNote({ text: r.message })))}>Проверить связь</button>
            {status.mode !== "env" && (
              <button disabled={busy} onClick={async () => (await askConfirm({ title: "Выход из Claude", message: "Отключить Claude? Сохранённый ключ или вход будут удалены с сервера.", ok: "Выйти", danger: true })) && run(disconnectAssistant(), "Claude отключён")}>Выйти</button>
            )}
          </div>
          {status.mode === "env" && <p className="muted small">Ключ задан в окружении сервера. Чтобы войти иначе, войдите аккаунтом или введите ключ ниже — они заменят его.</p>}
        </section>
      ) : (
        <section className="ca-card">
          <strong>Подключите Claude</strong>
          <p className="muted small">Два пути: <b>подписка Claude</b> (Pro, Max, Team) — ваш Claude Desktop или Claude Code сам подключается к BedrockForge, без оплаты API; или <b>Claude API</b> — встроенная панель, оплата по тарифу API (вход аккаунтом Console или ключ).</p>
        </section>
      )}

      {(!status.configured || status.mode === "env") && (
        <section className="ca-card">
          <div className="dh-seg" role="tablist" aria-label="Способ входа">
            <button role="tab" aria-selected={tab === "subscription"} className={tab === "subscription" ? "active" : ""} onClick={() => setTab("subscription")} title="Без оплаты API: ваш Claude Desktop или Claude Code управляет BedrockForge">Подписка Claude</button>
            <button role="tab" aria-selected={tab === "account"} className={tab === "account" ? "active" : ""} onClick={() => setTab("account")}>Аккаунт API</button>
            <button role="tab" aria-selected={tab === "key"} className={tab === "key" ? "active" : ""} onClick={() => setTab("key")}>API-ключ</button>
          </div>
          {tab === "subscription" && <SubscriptionHelp />}
          {tab === "account" && (status.account_login === false ? (
            <p className="muted small">
              Вход аккаунтом недоступен: на сервере нет официальной утилиты Anthropic <code>ant</code>. Используйте API-ключ
              или установите <code>ant</code> в контейнер api (см. README, раздел «Claude»).
            </p>
          ) : !login ? (
            <>
              <p className="small">Вход в аккаунт Anthropic Console в браузере — ключ вводить не нужно, доступ обновляется сам. Оплата — по тарифу Claude API этого аккаунта.</p>
              <button className="primary" disabled={busy} onClick={startLogin}>{busy ? "Открываю…" : "Войти через Anthropic"}</button>
            </>
          ) : (
            <>
              <ol className="ca-steps small">
                <li>На открывшейся странице войдите и разрешите доступ. Не открылась — <a href={login.url} target="_blank" rel="noreferrer">открыть страницу входа</a>.</li>
                <li>Скопируйте код, который покажет страница.</li>
                <li>Вставьте его сюда:</li>
              </ol>
              <input value={code} placeholder="код со страницы входа" aria-label="Код со страницы входа" autoFocus onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && code.trim() && run(finishAccountLogin(login.id, code).then((s) => { setLogin(null); setCode(""); return s; }))} />
              <div className="ca-actions">
                <button className="primary" disabled={busy || !code.trim()} onClick={() => run(finishAccountLogin(login.id, code).then((s) => { setLogin(null); setCode(""); return s; }))}>{busy ? "Проверяю…" : "Подтвердить"}</button>
                <button disabled={busy} onClick={() => { void cancelAccountLogin(); setLogin(null); setCode(""); }}>Отмена</button>
              </div>
            </>
          ))}
          {tab === "key" && (
            <>
              <p className="small">Ключ создаётся на <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">console.anthropic.com → API Keys</a>. Он хранится только на сервере BedrockForge и в браузер не возвращается.</p>
              <input type="password" value={key} placeholder="sk-ant-…" aria-label="API-ключ Claude" autoComplete="off" onChange={(e) => setKey(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && key.trim() && run(connectAssistantKey(key).then((s) => { setKey(""); return s; }))} />
              <button className="primary" disabled={busy || !key.trim()} onClick={() => run(connectAssistantKey(key).then((s) => { setKey(""); return s; }))}>{busy ? "Проверяю ключ…" : "Подключить"}</button>
            </>
          )}
        </section>
      )}

      {note && <div className={`alert ${note.error ? "error" : "ok"} small`}>{note.text}</div>}

      <section className="ca-card">
        <strong>Модель</strong>
        <select value={status.model} aria-label="Модель Claude" disabled={busy} onChange={(e) => run(setAssistantPreferences({ model: e.target.value }))}>
          {models.map((m) => <option key={m.id} value={m.id}>{m.title} — ${m.price_in} / ${m.price_out} за 1 млн токенов</option>)}
        </select>
        {current && <span className="muted small">{current.note}</span>}
        <span className="small">Глубина рассуждений</span>
        <div className="dh-seg" role="radiogroup" aria-label="Глубина рассуждений">
          {(status.efforts ?? []).map((e) => (
            <button key={e} role="radio" aria-checked={status.effort === e} className={status.effort === e ? "active" : ""} disabled={busy} title={EFFORT_TITLES[e]?.[1]}
              onClick={() => run(setAssistantPreferences({ effort: e }))}>{EFFORT_TITLES[e]?.[0] ?? e}</button>
          ))}
        </div>
        <span className="muted small">{EFFORT_TITLES[status.effort ?? "medium"]?.[1]}</span>
      </section>

      <section className="ca-card">
        <div className="ca-head"><strong className="grow">Расход</strong>
          <div className="dh-seg" role="radiogroup" aria-label="Период расхода">
            {([["today", "Сегодня"], ["month", "30 дней"], ["all", "Всё время"]] as const).map(([p, t]) => (
              <button key={p} role="radio" aria-checked={period === p} className={period === p ? "active" : ""} onClick={() => setPeriod(p)}>{t}</button>
            ))}
          </div>
        </div>
        {u ? (
          <>
            <div className="ca-stats">
              <div><b>{money(u.cost_usd)}</b><span className="muted small">оценка</span></div>
              <div><b>{u.requests}</b><span className="muted small">запросов</span></div>
              <div><b>{tokens(u.input_tokens)}</b><span className="muted small">токенов на входе</span></div>
              <div><b>{tokens(u.output_tokens)}</b><span className="muted small">на выходе</span></div>
            </div>
            {u.by_model.length > 0 && (
              <table className="ca-table small">
                <thead><tr><th>Модель</th><th>Запросов</th><th>Токенов</th><th>$</th></tr></thead>
                <tbody>{u.by_model.map((r) => (
                  <tr key={r.model}><td>{models.find((m) => r.model.startsWith(m.id))?.title ?? r.model}</td><td>{r.requests}</td><td>{tokens(r.input_tokens + r.output_tokens)}</td><td>{money(r.cost_usd)}</td></tr>
                ))}</tbody>
              </table>
            )}
            <span className="muted small">Оценка по ценам API без налогов и скидок; точный счёт — в console.anthropic.com.</span>
          </>
        ) : <span className="muted small">Нет данных.</span>}
      </section>
    </div>
  );
}

/** How to use a Claude subscription: the user's own Claude app connects to BedrockForge over MCP. */
function SubscriptionHelp() {
  const mcp = mcpUrl();
  const code = `claude mcp add --transport http bedrockforge ${mcp}`;
  const desktop = JSON.stringify({ mcpServers: { bedrockforge: { command: "npx", args: ["-y", "mcp-remote", mcp] } } }, null, 2);
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (what: string, text: string) =>
    navigator.clipboard?.writeText(text).then(() => setCopied(what)).catch(() => setCopied(null));
  return (
    <div className="ca-sub">
      <p className="small">
        Подписку Claude (Pro, Max, Team) Anthropic разрешает использовать только в своих приложениях — встроенная панель BedrockForge
        работать по ней не может. Поэтому подключение идёт наоборот: <b>ваш Claude</b> (Claude Desktop или Claude Code, вход по подписке)
        подключается к BedrockForge по MCP, читает данные проекта и управляет открытым окном. Ключ API не нужен, расход — в лимитах подписки.
      </p>
      <strong className="small">Claude Code</strong>
      <div className="ca-code"><code>{code}</code><button className="small-btn" onClick={() => copy("code", code)}>{copied === "code" ? "Скопировано" : "Копировать"}</button></div>
      <strong className="small">Claude Desktop</strong>
      <ol className="ca-steps small">
        <li>Установите Node.js (nodejs.org) — нужен мост <code>mcp-remote</code>.</li>
        <li>Claude Desktop → Settings → Developer → Edit Config. Файл <code>claude_desktop_config.json</code>: Windows — <code>%APPDATA%\Claude</code>, macOS — <code>~/Library/Application Support/Claude</code>.</li>
        <li>Добавьте в него (или в существующий раздел <code>mcpServers</code>):</li>
      </ol>
      <div className="ca-code"><pre>{desktop}</pre><button className="small-btn" onClick={() => copy("desktop", desktop)}>{copied === "desktop" ? "Скопировано" : "Копировать"}</button></div>
      <ol className="ca-steps small" start={4}>
        <li>Перезапустите Claude Desktop: в списке инструментов появится «bedrockforge».</li>
        <li>Держите BedrockForge открытым в браузере или в программе для Windows (в строке состояния «MCP: готов») и пишите задачи в Claude: «покажи скважину H-12 на разрезе», «сводка аудита».</li>
      </ol>
      <p className="muted small">MCP-сервер работает без пароля на этом компьютере (порт 8000): не открывайте его в интернет.</p>
    </div>
  );
}
