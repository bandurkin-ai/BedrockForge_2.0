/**
 * The card of a call of a tool in the feed (Э23, 23.14; ADR 0029 решение 33; docs/progress/e23-api.md §5, §7): a `data_*` call the core made
 * (the title is the core's, «Дерево проекта») or a command of the window (`ui_*`; the title is the registry's). Folded by default: the line shows the
 * title, the status and a brief result; the arguments (`key = value`) and the whole result (≤ 4000 characters) open on a click. The buttons of a card
 * that waits for the window («Выполнить» / «Отклонить», 23.15) are the `actions` slot — this file does not know them. A text is a text node, never markup.
 */
import { memo, type ReactNode } from "react";
import type { ToolItem, ToolStatus } from "./wireTypes";
import css from "./ToolCard.module.css";

const BRIEF_MAX = 90;
const ARG_MAX = 200;

export const STATUS_TITLES: Record<ToolStatus, string> = { ok: "Готово", error: "Ошибка", declined: "Отклонено", waiting: "Ожидание" };

const clip = (s: string, max: number): string => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

/** The status as a sentence: a `data_*` call that has no end yet runs, a command of the window waits for the window. */
export function statusText(item: Pick<ToolItem, "status" | "side">): string {
  if (item.status !== "waiting") return STATUS_TITLES[item.status];
  return item.side === "ui" ? "Ожидает выполнения в окне" : "Выполняется…";
}

/** One value of an argument as the card shows it; a gap is «—», never 0 or an empty string. */
export function argValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return clip(v, ARG_MAX);
  return clip(JSON.stringify(v) ?? String(v), ARG_MAX);
}

/** `key = value` lines of the arguments. */
export function argLines(args: Record<string, unknown>): string[] {
  return Object.keys(args).map((k) => `${k} = ${argValue(args[k])}`);
}

/** The short result of the line of a card (the whole text is under the fold). */
export function briefOf(item: Pick<ToolItem, "status" | "side" | "summary">): string {
  if (item.status === "waiting") return statusText(item);
  const text = item.summary.trim();
  if (!text) return STATUS_TITLES[item.status];
  if (item.status === "ok" && item.side === "data" && /^[[{]/.test(text)) return `Получено: ${text.length} зн.`;
  return clip(text.split("\n", 1)[0], BRIEF_MAX);
}

export interface ToolCardProps {
  item: ToolItem;
  /** the buttons of a command that waits for the window (`ConfirmCard`/`pending`, 23.15) */
  actions?: ReactNode;
}

export const ToolCard = memo(function ToolCard({ item, actions }: ToolCardProps) {
  const lines = argLines(item.args);
  const side = item.side === "data" ? "Данные" : "Команда";
  return (
    <div className={css.card} data-testid="tool-card" data-status={item.status} data-side={item.side} data-name={item.name}>
      <details className={css.fold}>
        <summary className={css.line}>
          <span className={css.side} title={item.side === "data" ? "Чтение данных проекта ядром" : "Команда программы"}>
            {side}
          </span>
          <span className={css.title}>{item.title}</span>
          <span className={`${css.badge} ${css[item.status]}`} data-testid="tool-status">
            {statusText(item)}
          </span>
          {item.status !== "waiting" ? <span className={css.brief}>{briefOf(item)}</span> : null}
        </summary>
        <div className={css.body}>
          {lines.length > 0 ? (
            <pre className={css.args} data-testid="tool-args">
              {lines.join("\n")}
            </pre>
          ) : (
            <div className={css.none}>Без аргументов</div>
          )}
          {item.summary ? (
            <pre className={css.result} data-testid="tool-result">
              {item.summary}
            </pre>
          ) : null}
        </div>
      </details>
      {actions ? <div className={css.actions}>{actions}</div> : null}
    </div>
  );
});
