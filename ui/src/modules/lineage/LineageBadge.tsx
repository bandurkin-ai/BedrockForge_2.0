/**
 * The badge of the state of a computed object (Д-35; ADR 0025 §10): one component for every kind of object in the tree, in
 * place of the badges of the modules. «актуален» — a quiet dot, «устарел», «источник удалён», «закреплён», «считается» — a word
 * in its colour; the tooltip is the chain of the causes («модель вариограммы «M» ← изменено: таблица «Пробы»»). The badge asks
 * its state itself (the asks of one moment go in one request) and keeps it fresh; a row that has not got the answer yet shows
 * nothing. In a row of the tree: `after={<LineageBadge node={{kind: "dh_result", id}} />}`.
 */
import { stateTitle, STATE_TITLES, type LNode, type LState, type LStatus } from "./api";
import { useStatus } from "./hooks";
import css from "./Lineage.module.css";

/** The mark of a known state (no request): the badge itself, the panels and the window. */
export function StateMark({ status, hideCurrent = false, testId }: { status: Pick<LStatus, "state" | "reason" | "changed">; hideCurrent?: boolean; testId?: string }) {
  const { state } = status;
  if (state === "current" && hideCurrent) return null;
  const title = stateTitle(status);
  const word = state === "pinned" && status.changed ? "закреплён · входы изменились" : STATE_TITLES[state];
  return (
    <span className={`${css.badge} ${css[state as LState]} ${state === "pinned" && status.changed ? css.pinChanged : ""}`} title={title} aria-label={title.replace(/\n/g, " ")} role="img" data-state={state} data-testid={testId}>
      <span className={css.dot} aria-hidden />
      {state !== "current" && <span>{word}</span>}
    </span>
  );
}

export function LineageBadge({ node, hideCurrent = false }: { node: LNode; hideCurrent?: boolean }) {
  const status = useStatus(node);
  if (!status || status.state === "deleted") return null;
  return <StateMark status={status} hideCurrent={hideCurrent} testId={`lineage-badge-${node.kind}-${node.id}${node.sub ? `-${node.sub}` : ""}`} />;
}
