import { useEffect, useMemo } from "react";
import { useApp } from "./AppContext";
import { BottomDock, RightDock, Viewport } from "./Docks";
import { DataTree } from "./DataTree";
import { ContextMenuHost } from "./ContextMenu";
import { CoreDown } from "./CoreDown";
import { DialogHost } from "./DialogHost";
import { showMessage } from "./dialogs";
import { detectImportKind, IMPORT_TITLES } from "./tree";
import { LicenseWindow } from "./LicenseWindow";
import { Palette } from "./Palette";
import { SettingsWindow } from "./SettingsWindow";
import { WindowLayer, type WindowRenderer } from "./WindowLayer";
import { Header } from "./Header";
import { resolveKey, shortcutMap } from "./hotkeys";
import { eventShortcut } from "./keys";
import { Ribbon } from "./Ribbon";
import { buildRibbon, flattenRibbon } from "./ribbonConfig";
import { Splitter } from "./Splitter";
import { StatusBar } from "./StatusBar";
import { useStore } from "./useStore";
import { resize } from "./workspace";
import css from "./Shell.module.css";

const renderWindow: WindowRenderer = (w) => {
  switch (w.kind) {
    case "settings":
      return <SettingsWindow tab={w.props?.tab as string | undefined} />;
    case "license":
      return <LicenseWindow />;
    default:
      return null;
  }
};

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

  useEffect(() => {
    // Files dropped from Explorer open the import wizard with the detected type (ТЗ 7.1, 7.8).
    const over = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    const drop = async (e: DragEvent) => {
      const files = [...(e.dataTransfer?.files ?? [])];
      if (!files.length) return;
      e.preventDefault();
      const kind = detectImportKind(files.map((f) => f.name));
      if (!kind) {
        await showMessage({ title: "Импорт файлов", message: "Тип файлов не распознан. Поддерживаются DAT, CSV, Excel, TRIDB, DXF, OBJ, STL, STR, растры с привязкой, OMF, .bfggis." });
        return;
      }
      const reason = app.registry.reason("import.files");
      if (reason) await showMessage({ title: `Импорт: ${IMPORT_TITLES[kind]}`, message: reason });
      else await app.run("import.files", { kind });
    };
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [app]);

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
      <WindowLayer render={renderWindow} />
      <CoreDown />
      <ContextMenuHost />
      <Palette />
      <DialogHost />
    </div>
  );
}
