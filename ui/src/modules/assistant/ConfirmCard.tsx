/**
 * The cards of 23.15 in the chat (Э23; ТЗ 7.32 «Панель чата»; ADR 0029 решения 23, 26): `ConfirmCard` — Claude asks to run a command that changes data: the title and the description
 * of the command, ALL the arguments as they are, «Разрешить» / «Отклонить». `PendingButtons` — «Выполнить» / «Отклонить» of the card of a dialog opened with a command that waited for the
 * window (nothing runs by itself, решение 23). `RunNotice` — «Claude выполняет команды» with «Стоп». `AssistantChat` is `ChatPanel` with these as its slots (`renderToolActions`, `tail`): `Docks.tsx` draws it.
 *
 * `ConfirmCard`, `PendingButtons` and `RunNotice` are pure views (the tests call them); the connected ones read `confirmStore` and `pendingStore`. A text is a text node, never markup.
 */
import { useEffect, useRef, type ButtonHTMLAttributes } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import type { ToolItem } from "./wireTypes";
import { ChatPanel } from "./ChatPanel";
import { answerConfirm, confirmStore, type ConfirmView } from "./confirm";
import { buttonsItemId, declinePendingCards, pendingStore, runPendingCards } from "./pending";
import { chatStore } from "./state";
import css from "./ConfirmCard.module.css";

export const CONFIRM_TITLE = "Claude просит выполнить действие";
export const RESTORED_HINT = "Команды ждали окна: сами они не выполнятся";

export interface ConfirmCardProps {
  view: ConfirmView;
  onAnswer: (allow: boolean) => void;
}

/** A button that takes the focus when it appears (a view called as a function has no hooks: the effect lives here). */
function FocusButton(props: ButtonHTMLAttributes<HTMLButtonElement> & { "data-testid"?: string }) {
  const ref = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return <button type="button" ref={ref} {...props} />;
}

export function ConfirmCard({ view, onAnswer }: ConfirmCardProps) {
  const uid = `confirm-${view.id}`;
  return (
    // the question must not be missed: an alert dialog whose focus is on the safe answer («Отклонить»), which also scrolls the card into sight
    <div className={css.confirm} role="alertdialog" aria-labelledby={`${uid}-head`} aria-describedby={`${uid}-desc`} data-testid="confirm-card" data-command={view.commandId}>
      <div className={css.head} id={`${uid}-head`}>
        {CONFIRM_TITLE}
      </div>
      <div className={css.title} data-testid="confirm-title">
        {view.title}
      </div>
      <p className={css.desc} id={`${uid}-desc`} data-testid="confirm-description">
        {view.description}
      </p>
      {view.args.length > 0 ? (
        <pre className={css.args} data-testid="confirm-args">
          {view.args.join("\n")}
        </pre>
      ) : (
        <div className={css.none}>Без аргументов</div>
      )}
      <div className={css.row}>
        <FocusButton className={css.deny} data-testid="confirm-deny" onClick={() => onAnswer(false)}>
          Отклонить
        </FocusButton>
        <button type="button" className={css.allow} data-testid="confirm-allow" onClick={() => onAnswer(true)}>
          Разрешить
        </button>
      </div>
    </div>
  );
}

export interface PendingButtonsProps {
  onRun: () => void;
  onDecline: () => void;
}

export function PendingButtons({ onRun, onDecline }: PendingButtonsProps) {
  return (
    <>
      <span className={css.hint} data-testid="pending-hint">
        {RESTORED_HINT}
      </span>
      <button type="button" className={css.allow} data-testid="pending-run" onClick={onRun}>
        Выполнить
      </button>
      <button type="button" className={css.deny} data-testid="pending-decline" onClick={onDecline}>
        Отклонить
      </button>
    </>
  );
}

export function RunNotice({ onStop }: { onStop: () => void }) {
  return (
    <div className={css.run} role="status" data-testid="run-notice">
      <span>Claude выполняет команды…</span>
      <button type="button" className={css.deny} data-testid="run-stop" onClick={onStop}>
        Стоп
      </button>
    </div>
  );
}

/** The end of the feed: the question of confirmation (the first of the queue) or the notice of a run that goes on. */
export function ConfirmTail() {
  const app = useApp();
  const queue = useStore(confirmStore, (s) => s.queue);
  const running = useStore(pendingStore, (s) => s.running);
  const head = queue[0];
  if (head) return <ConfirmCard key={head.id} view={head} onAnswer={(allow) => answerConfirm(head.id, allow)} />;
  if (running) return <RunNotice onStop={() => void app.run("assistant.stop")} />;
  return null;
}

/** The buttons of the restored card: on the first card that still waits, none on the others. */
export function PendingActions({ item }: { item: ToolItem }) {
  const chat = useStore(chatStore, (s) => s);
  const running = useStore(pendingStore, (s) => s.running);
  if (buttonsItemId(chat, running !== null) !== item.id) return null;
  return <PendingButtons onRun={() => void runPendingCards()} onDecline={() => void declinePendingCards()} />;
}

/** The panel «Claude» of the right dock with the cards of 23.15. */
export function AssistantChat() {
  return <ChatPanel renderToolActions={(item) => <PendingActions item={item} />} tail={<ConfirmTail />} />;
}
