import { useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "../shell/icons";

/**
 * A floating window over the workspace (charts): drag by the title, resize by the corner,
 * maximise, close. Several can be open; the one clicked last is on top.
 */
export function ChartWindow({
  title, icon = "histogram", index, z, onFocus, onClose, children, size,
}: {
  title: string;
  icon?: IconName;
  /** Opening order: each next window is offset a little. */
  index: number;
  z: number;
  onFocus: () => void;
  onClose: () => void;
  children: ReactNode;
  /** a narrow window for a form, or a medium one for a table (default: a large chart window) */
  size?: "compact" | "medium" | "form";
}) {
  const [pos, setPos] = useState(() => ({ x: 120 + (index % 6) * 28, y: 90 + (index % 6) * 28 }));
  const [max, setMax] = useState(false);
  const drag = useRef<{ dx: number; dy: number } | null>(null);

  const onDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("button") || max) return;
    drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const x = Math.min(Math.max(e.clientX - drag.current.dx, -200), window.innerWidth - 120);
    const y = Math.min(Math.max(e.clientY - drag.current.dy, 0), window.innerHeight - 40);
    setPos({ x, y });
  };

  const win = (
    <section
      className={`chart-window${max ? " maximized" : ""}${size ? ` ${size}` : ""}`} style={max ? { zIndex: z } : { left: pos.x, top: pos.y, zIndex: z }}
      onMouseDown={onFocus} role="dialog" aria-label={title}
    >
      <header className="chart-window-head" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={() => (drag.current = null)} onDoubleClick={() => setMax((m) => !m)}>
        <Icon name={icon} size={15} />
        <strong className="grow">{title}</strong>
        <button className="icon" onClick={() => setMax((m) => !m)} title={max ? "Обычный размер" : "Развернуть"} aria-label={max ? "Обычный размер" : "Развернуть"}>
          <Icon name="maximize" size={14} />
        </button>
        <button className="icon" onClick={onClose} title="Закрыть" aria-label="Закрыть"><Icon name="close" size={14} /></button>
      </header>
      <div className="chart-window-body">{children}</div>
    </section>
  );
  // All floating windows live in one layer of the page: however high their own order grows,
  // dialogs (export, import, confirmations) and menus stay above them.
  const layer = document.getElementById(WINDOW_LAYER_ID);
  return layer ? createPortal(win, layer) : win;
}

/** id of the element that holds every floating window (rendered once by the application). */
export const WINDOW_LAYER_ID = "window-layer";
