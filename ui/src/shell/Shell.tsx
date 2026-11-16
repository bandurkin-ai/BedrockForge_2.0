import { useEffect, useMemo } from "react";
import { useApp } from "./AppContext";
import { BottomDock, RightDock, Viewport } from "./Docks";
import { DataTree } from "./DataTree";
import { Header } from "./Header";
import { resolveKey, shortcutMap } from "./hotkeys";
import { eventShortcut } from "./keys";
import { Ribbon } from "./Ribbon";
import { buildRibbon, flattenRibbon } from "./ribbon";
import { Splitter } from "./Splitter";
import { StatusBar } from "./StatusBar";
import { useStore } from "./useStore";
import { resize } from "./workspace";
import css from "./Shell.module.css";

function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

/** Main window layout (ТЗ 7.0.3): header, ribbon, tree | 3D | right dock, bottom dock, status bar. */
export function Shell() {
  const app = useApp();
  const ws = useStore(app.store, (s) => s.workspace);
  const view = useStore(app.store, (s) => s.view);
  const overrides = useStore(app.store, (s) => s.shortcuts);
  const paletteOpen = useStore(app.palette, (p) => p.query !== null);
  const keys = useMemo(() => shortcutMap(flattenRibbon(buildRibbon(ws, view)), overrides), [ws, view, overrides]);

  useEffect(() => {
    document.documentElement.dataset.theme = ws.theme;
  }, [ws.theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat) return;
      const sc = eventShortcut(e);
      const modal = paletteOpen || app.store.get().modal;
      const item = resolveKey(sc, keys, { typing: isTyping(e.target), modal });
      if (!item) return;
      e.preventDefault();
      if (app.registry.reason(item.command)) return; // an unavailable button is not run by its key
      void app.run(item.command, item.args ?? {});
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [app, keys, paletteOpen]);

  const set = (dock: "left" | "right" | "bottom") => (px: number) => app.store.set((s) => ({ workspace: resize(s.workspace, dock, px) }));

  return (
    <div className={css.shell}>
      <Header />
      <Ribbon />
      <div className={css.middle}>
        {ws.left && (
          <>
            <DataTree />
            <Splitter axis="x" size={ws.sizes.left} onResize={set("left")} label="Ширина дерева" />
          </>
        )}
        <div className={css.center}>
          <Viewport />
          {ws.bottom && (
            <>
              <Splitter axis="y" size={ws.sizes.bottom} invert onResize={set("bottom")} label="Высота нижней панели" />
              <BottomDock />
            </>
          )}
        </div>
        {ws.right && (
          <>
            <Splitter axis="x" size={ws.sizes.right} invert onResize={set("right")} label="Ширина правой панели" />
            <RightDock />
          </>
        )}
      </div>
      <StatusBar />
    </div>
  );
}
