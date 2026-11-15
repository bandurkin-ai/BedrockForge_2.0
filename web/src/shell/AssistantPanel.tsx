import { useEffect, useRef, useState } from "react";
import type { AssistantChatInfo, AssistantStatus, AssistantUsageTotals } from "../api";
import { ClaudeAccountButton } from "./ClaudeAccount";
import { askConfirm, askText } from "./dialogs";
import { Icon } from "./icons";
import { Markdown } from "./markdown";
import { QUICK_PROMPTS } from "./ribbonConfig";
import type { ChatItem } from "./useAssistant";

export interface Approval {
  title: string;
  detail: string;
  resolve: (allowed: boolean) => void;
}

/** Right dock: the conversation with Claude, which sees the workspace and can run its commands. */
export function AssistantPanel({
  items,
  busy,
  status,
  context,
  approval,
  hasHole,
  onSend,
  onStop,
  onReset,
  title,
  usage,
  chats,
  chatId,
  onOpenChat,
  onRenameChat,
  onDeleteChat,
  onAccount,
  onModel,
}: {
  items: ChatItem[];
  busy: boolean;
  status: AssistantStatus | null;
  /** Short labels of what Claude is told about the workspace. */
  context: string[];
  approval: Approval | null;
  hasHole: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  onReset: () => void;
  /** the open chat: its title and what it cost */
  title: string | null;
  usage: AssistantUsageTotals | null;
  /** saved chats of the project */
  chats: AssistantChatInfo[];
  chatId: string | null;
  onOpenChat: (id: string) => void;
  onRenameChat: (id: string, title: string) => void;
  onDeleteChat: (id: string) => void;
  /** opens the window of the connection */
  onAccount: () => void;
  onModel: (model: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [items, busy, approval]);

  const submit = () => {
    if (busy || !draft.trim()) return;
    onSend(draft);
    setDraft("");
  };

  const unavailable = status !== null && !status.configured;

  return (
    <div className="assistant">
      <header className="assistant-head">
        <Icon name="spark" size={16} />
        <strong className="assistant-title" title={title ?? "Новый диалог"}>{title ?? "Claude"}</strong>
        <span className="spacer" />
        {status?.configured && (status.models?.length ?? 0) > 0 && (
          <select className="assistant-model" value={status.model} aria-label="Модель Claude" title="Модель Claude для следующих сообщений" onChange={(e) => onModel(e.target.value)}>
            {status.models?.map((m) => <option key={m.id} value={m.id}>{m.title.replace("Claude ", "")}</option>)}
          </select>
        )}
        <button className={`icon${history ? " active" : ""}`} onClick={() => setHistory((h) => !h)} title="История диалогов проекта" aria-label="История диалогов" aria-pressed={history}>
          <Icon name="list" size={16} />
        </button>
        <button className="icon" onClick={() => { onReset(); setHistory(false); }} disabled={items.length === 0 && !chatId} title="Новый диалог" aria-label="Новый диалог">
          <Icon name="plus" size={16} />
        </button>
        <ClaudeAccountButton status={status} onClick={onAccount} />
      </header>
      {history && (
        <div className="assistant-history" role="list" aria-label="История диалогов">
          {chats.length === 0 && <span className="muted small">В этом проекте ещё нет диалогов.</span>}
          {chats.map((c) => (
            <div key={c.id} role="listitem" className={`assistant-chat${c.id === chatId ? " active" : ""}`}>
              <button className="assistant-chat-open" onClick={() => { onOpenChat(c.id); setHistory(false); }} title={`Открыть: ${c.title}`}>
                <span className="assistant-chat-title">{c.title}</span>
                <span className="muted small">
                  {c.updated_at ? new Date(c.updated_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}
                  {` · вопросов: ${c.messages}`}{c.cost_usd > 0 ? ` · $${c.cost_usd.toFixed(c.cost_usd < 0.01 ? 4 : 2)}` : ""}
                </span>
              </button>
              <button className="icon" title="Переименовать" aria-label={`Переименовать: ${c.title}`} onClick={async () => {
                const name = await askText({ title: "Переименовать диалог", value: c.title });
                if (name && name !== c.title) onRenameChat(c.id, name);
              }}><Icon name="edit" size={14} /></button>
              <button className="icon" title="Удалить диалог" aria-label={`Удалить: ${c.title}`} onClick={async () => (await askConfirm({ title: "Удаление диалога", message: `Удалить диалог «${c.title}»?`, danger: true })) && onDeleteChat(c.id)}><Icon name="trash" size={14} /></button>
            </div>
          ))}
        </div>
      )}

      <div className="assistant-log" ref={scroller} aria-live="polite">
        {unavailable && (
          <div className="alert warn">
            <strong>Claude не подключён.</strong> Войдите в аккаунт Anthropic или введите API-ключ.
            <div className="assistant-connect"><button className="primary" onClick={onAccount}>Подключить Claude</button></div>
          </div>
        )}
        {items.length === 0 && !unavailable && (
          <div className="assistant-intro">
            <p>Claude видит открытый проект, выбранную скважину и окна, читает данные и аудит, управляет интерфейсом.</p>
            <div className="quick">
              {QUICK_PROMPTS.map((q) => (
                <button key={q.label} disabled={busy || (q.needsHole && !hasHole)} onClick={() => onSend(q.prompt)}>
                  {q.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((item, i) => {
          switch (item.kind) {
            case "user":
              return <div key={i} className="msg user">{item.text}</div>;
            case "assistant":
              return <div key={i} className="msg bot"><Markdown text={item.text} /></div>;
            case "tool":
              return (
                <div key={i} className={item.ok ? "msg tool" : "msg tool failed"} title={item.detail}>
                  <Icon name="command" size={14} />
                  <span className="tool-title">{item.title}</span>
                  {item.detail && <span className="tool-detail">{item.detail}</span>}
                </div>
              );
            case "notice":
              return <div key={i} className="msg notice">{item.text}</div>;
            case "error":
              return <div key={i} className="alert error">{item.text}</div>;
          }
        })}
        {approval && (
          <div className="approval" role="alertdialog" aria-label="Подтверждение действия Claude">
            <p><strong>Claude хочет выполнить:</strong> {approval.title}</p>
            {approval.detail && <p className="muted small">{approval.detail}</p>}
            <div className="approval-actions">
              <button className="primary" onClick={() => approval.resolve(true)}>Разрешить</button>
              <button onClick={() => approval.resolve(false)}>Отклонить</button>
            </div>
          </div>
        )}
        {busy && !approval && <div className="msg notice thinking">Claude работает…</div>}
      </div>

      <div className="assistant-context" title="Этот контекст Claude получает с каждым сообщением">
        {context.map((label) => <span key={label} className="chip">{label}</span>)}
        {usage && usage.cost_usd > 0 && (
          <span className="chip assistant-cost" title={`Диалог: ${usage.input_tokens.toLocaleString("ru-RU")} токенов на входе, ${usage.output_tokens.toLocaleString("ru-RU")} на выходе (оценка по ценам API)`}>
            ${usage.cost_usd.toFixed(usage.cost_usd < 0.01 ? 4 : 2)}
          </span>
        )}
      </div>
      <div className="assistant-input">
        <textarea
          value={draft}
          rows={3}
          placeholder={unavailable ? "Claude не подключён" : "Спросите о данных или попросите что-то сделать…"}
          aria-label="Сообщение для Claude"
          disabled={unavailable}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <button className="send" onClick={onStop} title="Остановить" aria-label="Остановить"><Icon name="stop" size={16} /></button>
        ) : (
          <button className="send primary" onClick={submit} disabled={unavailable || !draft.trim()} title="Отправить (Enter)" aria-label="Отправить">
            <Icon name="send" size={16} />
          </button>
        )}
      </div>
    </div>
  );
}
