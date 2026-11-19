import { useEffect, useMemo } from "react";
import { useApp } from "./AppContext";
import { BottomDock, RightDock, Viewport } from "./Docks";
import { DataTree } from "./DataTree";
import { ContextMenuHost } from "./ContextMenu";
import { CoreDown } from "./CoreDown";
import { DialogHost } from "./DialogHost";
import { dialogStore, showMessage } from "./dialogs";
import { LicenseWindow } from "./LicenseWindow";
import { importPaths } from "../modules/importing/bind";
import { ImportWizard } from "../modules/importing/ImportWizard";
import { ObjectsWindow } from "../modules/importing/ObjectsWindow";
import type { WizardMode } from "../modules/importing/wizard";
import { OpLogWindow } from "./OpLogWindow";
import { startEvents } from "../modules/jobs/state";
import { StripLogWindow } from "../charts/striplog/StripLogWindow";
import { DownholeWindow } from "../modules/downhole/DownholeWindow";
import { BuildDtmWindow } from "../modules/dtm/BuildDtmWindow";
import { GroundWindow } from "../modules/dtm/GroundWindow";
import { DzWindow } from "../modules/lines/DzWindow";
import { LinesExportWindow } from "../modules/lines/LinesExportWindow";
import { RasterExportWindow } from "../modules/rasters/RasterExportWindow";
import type { ExportOptions } from "../modules/wireframes/export";
import { WireframesExportWindow } from "../modules/wireframes/WireframesExportWindow";
import { StylesImportWindow } from "../modules/downhole/StylesImportWindow";
import { SetStyleWindow } from "../modules/scene/SetStyleWindow";
import { ExportWindow } from "../modules/projects/ExportWindow";
import { ImportWindow } from "../modules/projects/ImportWindow";
import { ProjectsWindow } from "../modules/projects/ProjectsWindow";
import { restoreLast } from "../modules/projects/state";
import { bridge } from "../bridge";
import { api } from "./api";
import { Palette } from "./Palette";
import { pushOdaPath } from "./odaTool";
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
      return <SettingsWindow tab={w.props?.tab as string | undefined} asked={w.props?.asked as number | undefined} />;
    case "license":
      return <LicenseWindow />;
    case "op-log":
      return <OpLogWindow logId={w.props?.logId as number | undefined} />;
    case "import-drillholes":
      return <ImportWizard winId={w.id} paths={w.props?.paths as string[] | undefined} mode={w.props?.mode as WizardMode | undefined} />;
    case "import-objects":
      return <ObjectsWindow winId={w.id} paths={w.props?.paths as string[] | undefined} pick={w.props?.pick as "lines" | "rasters" | "wireframes" | undefined} cloud={w.props?.cloud as "las" | "xyz" | undefined} />;
    case "projects":
      return <ProjectsWindow />;
    case "project-export":
      return <ExportWindow winId={w.id} />;
    case "project-import":
      return <ImportWindow winId={w.id} path={String(w.props?.path ?? "")} />;
    case "collars-style":
      return <SetStyleWindow kind="collars" setId={Number(w.props?.setId)} />;
    case "traces-style":
      return <SetStyleWindow kind="traces" setId={Number(w.props?.setId)} />;
    case "striplog":
      return <StripLogWindow setId={Number(w.props?.setId)} setName={String(w.props?.setName ?? "")} tableHint={(w.props?.tableHint as string | null) ?? null} holeHint={(w.props?.holeHint as string | null) ?? null} />;
    case "downhole":
      return <DownholeWindow winId={w.id} setId={Number(w.props?.setId)} form={(w.props?.form as "grade" | "lith" | "event" | undefined) ?? "grade"} chartId={typeof w.props?.chartId === "number" ? w.props.chartId : null} table={(w.props?.table as string | null) ?? null} field={(w.props?.field as string | null) ?? null} focus={(w.props?.focus as string | undefined) ?? null} />;
    case "styles-import":
      return <StylesImportWindow winId={w.id} token={String(w.props?.token ?? "")} />;
    case "lines-export":
      return <LinesExportWindow winId={w.id} format={w.props?.format as string | undefined} layerIds={w.props?.layerIds as number[] | undefined} />;
    case "raster-export":
      return <RasterExportWindow winId={w.id} rasterId={w.props?.rasterId as number | undefined} format={w.props?.format as "source" | "geotiff" | undefined} />;
    case "dtm-build":
      return <BuildDtmWindow winId={w.id} cloudId={typeof w.props?.cloudId === "number" ? w.props.cloudId : undefined} />;
    case "dtm-ground":
      return <GroundWindow winId={w.id} cloudId={typeof w.props?.cloudId === "number" ? w.props.cloudId : undefined} />;
    case "cloud-dz":
      return <DzWindow winId={w.id} cloudId={typeof w.props?.cloudId === "number" ? w.props.cloudId : undefined} />;
    case "wireframes-export":
      return <WireframesExportWindow winId={w.id} format={w.props?.format as string | undefined} wireframeIds={w.props?.wireframeIds as number[] | undefined} options={w.props?.options as Partial<ExportOptions> | undefined} />;
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

  // The core is up: the last project opens (ТЗ 7.3), job events flow (ТЗ 7.6). Again after a restart.
  const coreOnline = useStore(app.store, (s) => s.core === "online");
  useEffect(() => {
    if (!coreOnline) return;
    restoreLast(app).catch((e: unknown) => void showMessage({ title: "Проект", message: e instanceof Error ? e.message : String(e) }));
    const heavy = app.store.get().heavyWorkers;
    if (heavy !== 1) void api.put("/jobs/workers", { heavy }).catch(() => undefined);
    void pushOdaPath(app.store); // «Настройка › Внешние программы»: the core does not keep the path itself
  }, [app, coreOnline]);
  useEffect(() => startEvents(), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat) return;
      const sc = eventShortcut(e);
      // Palette, own dialogs (incl. Claude's confirmation) and shortcut capture switch the ribbon keys off.
      const modal = paletteOpen || dialogStore.get().queue.length > 0 || app.store.get().modal > 0;
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
      const b = bridge();
      const paths = b ? files.map((f) => b.pathForFile(f)).filter(Boolean) : [];
      if (!paths.length) return;
      if (app.registry.reason("import.files")) {
        await showMessage({ title: "Импорт файлов", message: app.registry.reason("import.files") ?? "" });
        return;
      }
      await importPaths(app, paths);
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
