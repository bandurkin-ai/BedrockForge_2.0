/**
 * The mark of the account in the title bar (Э23, 23.17; ТЗ 7.0.3; ADR 0029 решение 35): a dot — `--ok` when Claude is connected, `--warn` at a login or
 * without the net, `--text-faint` when not connected; the hint is the text of the link of the status bar. A click opens the window of the connection.
 */
import { statusLabel, statusTone, useAssistantStatus } from "./StatusLink";
import css from "./AccountBadge.module.css";

export function AccountBadge({ onOpen }: { onOpen: () => void }) {
  const status = useAssistantStatus();
  const label = statusLabel(status);
  return (
    <button type="button" className={css.badge} data-testid="claude-account" title={label} aria-label={label} onClick={onOpen}>
      <span className={css.dot} data-tone={statusTone(status)} />
    </button>
  );
}
