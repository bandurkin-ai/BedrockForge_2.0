/**
 * The feed of the panel «Claude» (Э23, 23.14; ADR 0029 решение 33): the items of `ChatState` in order — questions, answers (a Markdown stream), cards of
 * tools, notices, errors — and «Claude думает…». The feed follows the end while the user has not scrolled up (and always after a new question). Every item is a
 * memoised view of its own: the reducer keeps the identity of the items an event did not touch, so a delta of the text rebuilds one message only.
 * `renderToolActions` (23.15) gives a card that waits for the window its buttons; `tail` (23.15) is what follows the last item (the confirmation card).
 */
import { useEffect, useRef, type ReactNode } from "react";
import { useStore } from "../../shell/useStore";
import { confirmStore } from "./confirm";
import type { ChatStatus } from "./reduce";
import type { Item, ToolItem } from "./wireTypes";
import { MessageView } from "./MessageView";
import { ToolCard } from "./ToolCard";
import css from "./MessageList.module.css";

export const THINKING_TEXT = "Claude думает…";

export interface MessageListProps {
  items: readonly Item[];
  status: ChatStatus;
  thinking: boolean;
  renderToolActions?: (item: ToolItem) => ReactNode;
  tail?: ReactNode;
}

/** The answer that is still being written: the last item of a feed of a running turn. */
export function streamingIndex(items: readonly Item[], status: ChatStatus): number {
  const last = items.length - 1;
  return status === "running" && last >= 0 && items[last].kind === "assistant" ? last : -1;
}

/** Nothing is on the screen yet after a question (the model has not written, thought or called a tool): the wait is shown. */
export function isWaiting(items: readonly Item[], status: ChatStatus, thinking: boolean): boolean {
  if (status !== "running") return false;
  if (thinking) return true;
  const last = items[items.length - 1];
  return !last || last.kind === "user";
}

const NEAR_END = 48;

export function MessageList({ items, status, thinking, renderToolActions, tail }: MessageListProps) {
  const box = useRef<HTMLDivElement | null>(null);
  const follow = useRef(true);
  const streaming = streamingIndex(items, status);
  const waiting = isWaiting(items, status, thinking);
  const last = items[items.length - 1];
  // the confirmation card is drawn by `tail` on its own: the feed follows the store of the questions, or the card would appear below the visible part
  const asked = useStore(confirmStore, (s) => s.queue[0]?.id ?? 0);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (last?.kind === "user") follow.current = true;
    if (asked) follow.current = true; // an open question must be in sight whatever the user scrolled
    if (follow.current) el.scrollTop = el.scrollHeight;
  }, [items, waiting, tail, last, asked]);

  return (
    <div
      ref={box}
      className={css.list}
      role="log"
      aria-label="Диалог с Claude"
      aria-busy={status === "running"}
      data-testid="message-list"
      onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_END;
      }}
    >
      {items.map((it, i) =>
        it.kind === "tool" ? <ToolCard key={it.id} item={it} actions={it.status === "waiting" ? renderToolActions?.(it) : undefined} /> : <MessageView key={it.id} item={it} streaming={i === streaming} />,
      )}
      {tail}
      {waiting ? (
        <div className={css.thinking} role="status" data-testid="thinking">
          <span className={css.dots} aria-hidden="true" />
          {THINKING_TEXT}
        </div>
      ) : null}
    </div>
  );
}
