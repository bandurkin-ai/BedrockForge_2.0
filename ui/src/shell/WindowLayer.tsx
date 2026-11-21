import { useEffect, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import type { App } from "./app";
import { useApp } from "./AppContext";
import { Icon } from "./Icon";
import { useStore } from "./useStore";
import { clampToViewport, focusWindow, rememberWindowRect, requestClose, setWindowMemory, updateWindow, windowStore, type FloatWin, type WindowMemory } from "./windows";
import { rememberWindow } from "./workspace";
import css from "./Window.module.css";

export type WindowRenderer = (win: FloatWin) => ReactNode;

/** The memory of window places in the user's settings (`workspace.windows`, saved with the workspace). */
export function settingsMemory(app: Pick<App, "store">): WindowMemory {
  return {
    get: (kind) => app.store.get().workspace.windows[kind],
    put: (kind, rect) => app.store.set((s) => ({ workspace: rememberWindow(s.workspace, kind, rect) })),
  };
}

/** One layer for all floating windows (ТЗ 7.0.12): drag by title, resize by the corner, maximize, close. */
export function WindowLayer({ render }: { render: WindowRenderer }) {
  const app = useApp();
  useEffect(() => {
    setWindowMemory(settingsMemory(app));
    return () => setWindowMemory(null);
  }, [app]);
  const wins = useStore(windowStore, (s) => s.windows);
  return (
    <div className={css.layer}>
      {wins.map((w) => (
        <FloatingWindow key={w.id} win={w}>
          {render(w)}
        </FloatingWindow>
      ))}
    </div>
  );
}

function drag(e: RPointerEvent, onMove: (dx: number, dy: number) => void, onEnd?: () => void) {
  e.preventDefault();
  const x0 = e.clientX;
  const y0 = e.clientY;
  const el = e.currentTarget as HTMLElement;
  el.setPointerCapture(e.pointerId);
  const move = (ev: PointerEvent) => onMove(ev.clientX - x0, ev.clientY - y0);
  const up = () => {
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    el.removeEventListener("pointercancel", up);
    onEnd?.();
  };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
}

/** After a move or a resize the window's place is remembered for its kind. */
const remember = (id: string) => () => {
  const now = windowStore.get().windows.find((w) => w.id === id);
  if (now) rememberWindowRect(now);
};

function FloatingWindow({ win, children }: { win: FloatWin; children: ReactNode }) {
  const style = win.maximized ? { left: 0, top: 0, width: "100vw", height: "100vh", zIndex: win.z } : { left: win.x, top: win.y, width: win.w, height: win.h, zIndex: win.z };
  const startMove = (e: RPointerEvent) => {
    if (win.maximized || (e.target as HTMLElement).closest("button")) return;
    const { x, y } = win;
    drag(e, (dx, dy) => updateWindow(win.id, clampToViewport({ ...win, x: x + dx, y: y + dy }, window.innerWidth, window.innerHeight)), remember(win.id));
  };
  const startResize = (e: RPointerEvent) => {
    const { w, h } = win;
    drag(e, (dx, dy) => updateWindow(win.id, { w: Math.max(280, w + dx), h: Math.max(160, h + dy) }), remember(win.id));
  };
  return (
    <section className={css.win} style={style} role="dialog" aria-label={win.title} onPointerDown={() => focusWindow(win.id)}>
      <div className={css.bar} onPointerDown={startMove} onDoubleClick={() => updateWindow(win.id, { maximized: !win.maximized })}>
        <span>{win.title}</span>
        <button type="button" className={css.btn} aria-label="Развернуть" title={win.maximized ? "Восстановить" : "Развернуть"} onClick={() => updateWindow(win.id, { maximized: !win.maximized })}>
          <Icon name="maximize" size={13} />
        </button>
        <button type="button" className={css.btn} aria-label="Закрыть" title="Закрыть" onClick={() => void requestClose(win.id)}>
          <Icon name="close" size={13} />
        </button>
      </div>
      <div className={css.content}>{children}</div>
      {!win.maximized && <div className={css.grip} onPointerDown={startResize} />}
    </section>
  );
}
