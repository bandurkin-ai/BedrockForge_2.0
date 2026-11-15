import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./icons";

export interface MenuItem {
  label: string;
  icon?: IconName;
  /** Shown greyed out with this explanation (e.g. "В разработке"). */
  disabledNote?: string;
  run?: () => void;
}

export type MenuEntry = MenuItem | "separator";

/** A right-click menu at the pointer; closes on a click elsewhere, Esc, scroll or resize. */
export function ContextMenu({ x, y, title, items, onClose }: { x: number; y: number; title?: string; items: MenuEntry[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

  // Keep the menu inside the window.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({ x: Math.min(x, window.innerWidth - r.width - 4), y: Math.min(y, window.innerHeight - r.height - 4) });
  }, [x, y]);

  useEffect(() => {
    const close = (e: Event) => {
      if (e instanceof MouseEvent && ref.current?.contains(e.target as Node)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("mousedown", close, true);
    window.addEventListener("contextmenu", close, true);
    window.addEventListener("resize", onClose);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close, true);
      window.removeEventListener("contextmenu", close, true);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  useEffect(() => ref.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus(), []);

  return (
    <div ref={ref} className="context-menu" role="menu" style={{ left: pos.x, top: pos.y }} onContextMenu={(e) => e.preventDefault()}>
      {title && <div className="context-title">{title}</div>}
      {items.map((item, i) =>
        item === "separator" ? (
          <div key={i} className="context-separator" role="separator" />
        ) : (
          <button
            key={i} role="menuitem" disabled={!!item.disabledNote || !item.run} title={item.disabledNote}
            onClick={() => {
              onClose();
              item.run?.();
            }}
          >
            {item.icon ? <Icon name={item.icon} size={14} /> : <span className="context-icon" />}
            <span className="grow">{item.label}</span>
            {item.disabledNote && <span className="muted small">{item.disabledNote}</span>}
          </button>
        ),
      )}
    </div>
  );
}
