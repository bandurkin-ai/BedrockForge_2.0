import { useEffect, useRef } from "react";
import { Store } from "./store";
import { useStore } from "./useStore";
import css from "./ContextMenu.module.css";

export interface MenuItem {
  label: string;
  run?: () => void;
  /** Why the item is unavailable (ТЗ 7.0.13: an item without data is disabled with a reason). */
  reason?: string | null;
  separator?: boolean;
}

export const menuStore = new Store<{ x: number; y: number; items: MenuItem[] } | { items: null }>({ items: null });

export function openMenu(x: number, y: number, items: MenuItem[]): void {
  menuStore.set({ x, y, items });
}

/** Own context menu; the Chromium one is shown only in text fields (ТЗ 17.4.1). */
export function ContextMenuHost() {
  const st = useStore(menuStore, (s) => s);
  const ref = useRef<HTMLDivElement>(null);
  const close = () => menuStore.set({ items: null });

  useEffect(() => {
    const block = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
      e.preventDefault();
    };
    window.addEventListener("contextmenu", block);
    return () => window.removeEventListener("contextmenu", block);
  }, []);

  useEffect(() => {
    if (!st.items) return;
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [st.items]);

  if (!st.items || !("x" in st)) return null;
  const x = Math.min(st.x, window.innerWidth - 220);
  const y = Math.min(st.y, window.innerHeight - st.items.length * 28 - 10);
  return (
    <div ref={ref} className={css.menu} style={{ left: x, top: y }} role="menu">
      {st.items.map((it, i) =>
        it.separator ? (
          <div key={i} className={css.sep} />
        ) : (
          <button
            key={i}
            type="button"
            role="menuitem"
            className={`${css.item} ${it.reason ? css.disabled : ""}`}
            aria-disabled={it.reason ? true : undefined}
            title={it.reason ? `Недоступно: ${it.reason}` : undefined}
            onClick={() => {
              if (it.reason) return;
              close();
              it.run?.();
            }}
          >
            {it.label}
          </button>
        ),
      )}
    </div>
  );
}
