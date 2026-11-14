import { useEffect, useRef, useState } from "react";
import type { AssistantStatus } from "../api";
import { Icon } from "./icons";
import { Markdown } from "./markdown";
import { QUICK_PROMPTS } from "./ribbon";
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
}) {
  const [draft, setDraft] = useState("");
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
        <strong>Claude</strong>
        {status?.configured && <span className="muted small">{status.model}</span>}
        <span className="spacer" />
        <button className="icon" onClick={onReset} disabled={items.length === 0} title="Новый диалог" aria-label="Новый диалог">
          <Icon name="plus" size={16} />
        </button>
      </header>

      <div className="assistant-log" ref={scroller} aria-live="polite">
        {unavailable && (
          <div className="alert warn">
            <strong>Claude не подключён.</strong> На сервере API не задан ключ: добавьте переменную
            окружения <code>ANTHROPIC_API_KEY</code> и перезапустите сервис <code>api</code>.
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
