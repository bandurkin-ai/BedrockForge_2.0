import type { ReactNode } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { Icon } from "./icons";
import { PANE_COUNT, VIEW_KINDS, VIEW_TITLES, type Layout, type ViewKind } from "./workspace";

/** The central area: one to four panes, each showing a view of the same project and selection. */
export function ViewportGrid({
  layout,
  panes,
  active,
  renderView,
  onActivate,
  onChangeView,
  onMaximize,
}: {
  layout: Layout;
  panes: ViewKind[];
  active: number;
  renderView: (view: ViewKind, index: number) => ReactNode;
  onActivate: (index: number) => void;
  onChangeView: (index: number, view: ViewKind) => void;
  onMaximize: (index: number) => void;
}) {
  const count = PANE_COUNT[layout];
  return (
    <div className={`viewports layout-${layout}`}>
      {panes.slice(0, count).map((view, index) => (
        <section
          key={index}
          className={count > 1 && index === active ? "pane active" : "pane"}
          aria-label={`Окно ${index + 1}: ${VIEW_TITLES[view]}`}
          onPointerDownCapture={() => index !== active && onActivate(index)}
          onFocusCapture={() => index !== active && onActivate(index)}
        >
          <header className="pane-head">
            <select
              value={view}
              aria-label={`Вид в окне ${index + 1}`}
              onChange={(e) => onChangeView(index, e.target.value as ViewKind)}
            >
              {VIEW_KINDS.map((k) => <option key={k} value={k}>{VIEW_TITLES[k]}</option>)}
            </select>
            <span className="spacer" />
            {count > 1 && (
              <>
                <span className="pane-index" title="Номер окна">{index + 1}</span>
                <button className="icon" title="Развернуть окно на всю рабочую область" aria-label="Развернуть окно" onClick={() => onMaximize(index)}>
                  <Icon name="maximize" size={14} />
                </button>
              </>
            )}
          </header>
          <div className="pane-body"><ErrorBoundary key={view} label={VIEW_TITLES[view]} compact>{renderView(view, index)}</ErrorBoundary></div>
        </section>
      ))}
    </div>
  );
}
