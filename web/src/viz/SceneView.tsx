import { useEffect, useMemo, useRef, useState } from "react";
import { blockMeta, getBlockExposed, getBlockRender, getBlockValues, getSections, rasterImageUrl, type Dataset, type Hole, type VerticalPlacement } from "../api";
import { VEXAG_STEPS, isShown, layerKey, type ViewPreset, type WorkspaceState } from "../shell/workspace";
import { ScaleStrip, type VizSettings } from "./ComponentPanel";
import { azimuthDeg, DEFAULT_WINDOW, defaultSection, shiftSection, type SectionLine } from "./geometry";
import { SectionMap } from "./SectionMap";
import { GeoScene, MAX_SHOWN_BLOCKS, type Hover } from "./scene3d";
import { DEFAULT_COLLAR_STYLE, type CollarStyle } from "./collarStyle";
import { blockColors, defaultBlockStyle, type BlockStyle } from "./blockStyle";
import { unitTitle } from "../shell/BlockModelPanel";
import type { ColorScale } from "./colorScale";
import { loadDownhole, loadHoles, loadLines, loadTrajectories, loadWireframeInfo, loadWireframeLevel } from "./useVizData";
import { buildDownhole, displayFields, stackOf, type LegendEntry, type StackStart } from "./downholeGeometry";
import type { StripLogSettings } from "../charts/stripLog";
import type { EditSession } from "../edit/session";
import { EditCursor, EditToolbar } from "./EditToolbar";
import { useEditScene } from "./useEditScene";
import { AssembleBar } from "./AssembleBar";
import { Icon } from "../shell/icons";
import { ToolButton } from "../shell/ToolButton";
import { pickTriangle, type PickResult } from "../api";

function auditColor(h: Hole | undefined): string {
  if (!h) return "#9aa3b5";
  return h.error ? "#c0392b" : h.warning ? "#e0a030" : h.info ? "#3b82d6" : "#2e9d57";
}

export interface SceneViewProps {
  datasets: Dataset[] | null;
  ws: WorkspaceState;
  refreshKey: number;
  viz: VizSettings;
  activeSet: number | null;
  selectedHole: string | null;
  section: SectionLine | null;
  onWorkspace: (patch: Partial<WorkspaceState>) => void;
  onPick: (setId: number, holeId: string | null) => void;
  onSection: (section: SectionLine | null) => void;
  onEmptyClick: () => void;
  /** Gives the shell the view actions, so ribbon buttons, hotkeys and Claude can use them. */
  registerApi?: (api: SceneApi) => void;
  /** How each block model is shown (default: by its first grade). */
  blockStyles?: Record<number, BlockStyle>;
  /** A block picked in the view. */
  onPickBlock?: (modelId: number, index: number) => void;
  /** A wireframe picked in the view. */
  onPickWireframe?: (id: number) => void;
  /** The wireframe shown as selected (lighter). */
  selectedWireframe?: number | null;
  /** Marker and labels of the collars, by set id (default: a circle coloured by the audit, no labels). */
  collarStyles?: Record<number, CollarStyle>;
  /** Saved displays of data along the holes that are on in the view. */
  logs?: DownholeLog[];
  /** A layer of a display clicked in the legend: open its form on that layer. */
  onLegendLayer?: (chartId: number, trackId: string) => void;
  /** The string editor's session, while a line layer is being edited. */
  edit?: EditSession | null;
  onEditSave?: () => void;
  onEditExit?: () => void;
  /** The exact triangle under a click on a wireframe (query by click). */
  onTriangle?: (result: PickResult) => void;
  /** A panel shown under the toolbar (e.g. the manual triangle edit). */
  banner?: React.ReactNode;
  /** «Собрать из контуров»: make the wireframe of these lines of the edited layer, in this order. */
  onAssemble?: (lines: number[], closed: boolean) => void;
}

export interface DownholeLog {
  /** id of the saved display (chart) */
  id: number;
  setId: number;
  name: string;
  settings: StripLogSettings;
}

export interface SceneApi {
  fit: () => void;
  view: (preset: ViewPreset) => void;
  /** Start drawing a section line: two clicks on the top view. */
  drawSection: () => void;
  /** Clip to the window of this line and turn the view into its plane (Z up). */
  openSection: (line: SectionLine) => void;
  /** The frame of the current section (to stand an image on it), or null without a line. */
  sectionFrame: () => VerticalPlacement | null;
  /** A section along the hole's direction through its collar; null if the hole is not shown. */
  sectionThroughHole: (setId: number, holeId: string) => SectionLine | null;
  /** Marks triangles in the view (corners in project coordinates, 9 numbers each); null removes them. */
  mark: (key: string, corners: number[] | null, color?: string) => void;
}

/** The single main view: every visible object of the project in 3D, plan or section. */
export function SceneView(props: SceneViewProps) {
  const { datasets, ws, refreshKey, activeSet, selectedHole, section } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<GeoScene | null>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<SceneApi | null>(null);
  const loadedSets = useRef(new Map<number, string>());
  /** id -> number of simplified levels it was loaded with */
  const loadedWireframes = useRef(new Map<number, number>());
  const [wireframesShown, setWireframesShown] = useState(0);
  /** id -> version of the geometry in the scene */
  const loadedLines = useRef(new Map<number, number>());
  const cuts = useRef(new Map<number, { origin: number[]; polylines: { closed: boolean; points: number[][] }[] }>());
  const [cutsKey, setCutsKey] = useState(0);
  const loadedRasters = useRef(new Set<number>());
  // Block models: those requested, those whose geometry is in the scene, attribute values by "id:key".
  const loadedBlocks = useRef(new Set<number>());
  const readyBlocks = useRef(new Set<number>());
  const blockValues = useRef(new Map<string, Float32Array>());
  const styleRun = useRef(0);
  const [blockLegend, setBlockLegend] = useState<{ name: string; unit: string | null; scale: ColorScale | null; shown: number; total: number; tooMany: boolean; shell: number | null; thinned: number } | null>(null);
  // Outer shells of big models, by model id (they do not change when values are edited).
  const blockShells = useRef(new Map<number, Uint8Array>());
  const holesBySet = useRef(new Map<number, Map<string, Hole>>());
  // Downhole displays: the settings each was built with, and their legends.
  const loadedLogs = useRef(new Map<number, string>());
  const [logLegends, setLogLegends] = useState<{ id: number; name: string; entries: LegendEntry[]; holes: number; intervals: number }[]>([]);
  const [webglError, setWebglError] = useState<string | null>(null);
  const [loading, setLoading] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [drawing, setDrawing] = useState<{ x: number; y: number } | null | false>(false);

  // Latest values for scene callbacks.
  const live = useRef(props);
  live.current = props;
  const drawingRef = useRef(drawing);
  drawingRef.current = drawing;
  /** Adds a point of the section line being drawn (from the 3D view or the plan map). */
  const addPointRef = useRef<(x: number, y: number) => boolean>(() => false);
  const [mapOpen, setMapOpen] = useState(() => {
    try {
      return localStorage.getItem("geo.sectionMap") !== "0";
    } catch {
      return true;
    }
  });
  const toggleMap = (open: boolean) => {
    setMapOpen(open);
    try {
      localStorage.setItem("geo.sectionMap", open ? "1" : "0");
    } catch {
      // the map just opens again next time
    }
  };

  const sets = useMemo(() => (datasets ?? []).filter((d) => d.kind === "drillholes"), [datasets]);
  const wireframes = useMemo(() => (datasets ?? []).flatMap((d) => d.wireframes ?? []), [datasets]);
  const lineLayers = useMemo(() => (datasets ?? []).flatMap((d) => d.lines ?? []), [datasets]);
  const rasters = useMemo(() => (datasets ?? []).flatMap((d) => d.rasters ?? []), [datasets]);
  const blockModels = useMemo(() => (datasets ?? []).filter((d) => d.kind === "blocks"), [datasets]);

  // ---- the scene itself, created once
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let scene: GeoScene;
    try {
      scene = new GeoScene(container);
    } catch (e) {
      setWebglError((e as Error).message || "WebGL недоступен");
      return;
    }
    scene.onPick = (hole) => {
      if (hole) live.current.onPick(hole.setId, hole.holeId);
      else live.current.onEmptyClick();
    };
    scene.onHover = setHover;
    scene.onPickBlock = (pick) => {
      if (!pick || !live.current.onPickBlock) return false;
      live.current.onPickBlock(pick.modelId, pick.index);
      return true;
    };
    scene.onPickWireframe = (id, ray) => {
      if (!live.current.onPickWireframe) return false;
      live.current.onPickWireframe(id);
      // The exact triangle under the click, from the float64 data on the server.
      if (ray && live.current.onTriangle) {
        pickTriangle([id], ray.origin, ray.dir).then((r) => live.current.onTriangle?.(r)).catch(() => undefined);
      }
      return true;
    };
    scene.onGroundClick = (x, y) => addPointRef.current(x, y);
    addPointRef.current = (x, y) => {
      const start = drawingRef.current;
      if (start === false) return false;
      if (start === null) {
        setDrawing({ x, y });
        return true;
      }
      const length = Math.hypot(x - start.x, y - start.y);
      if (length < 1) return true;
      const previous = live.current.section;
      const line: SectionLine = {
        x0: start.x, y0: start.y, az: azimuthDeg(x - start.x, y - start.y), length,
        towards: previous?.towards ?? DEFAULT_WINDOW, away: previous?.away ?? DEFAULT_WINDOW,
      };
      setDrawing(false);
      // The line is drawn: clip to its window and turn the view into the section plane, Z up.
      apiRef.current?.openSection(line);
      live.current.onSection(line);
      live.current.onWorkspace({ clip: true });
      return true;
    };
    scene.onRender = () => {
      const label = labelRef.current;
      const { activeSet: setId, selectedHole: holeId } = live.current;
      const p = setId !== null && holeId ? scene.screenPosition({ setId, holeId }) : null;
      if (label) {
        label.style.display = p ? "block" : "none";
        if (p) label.style.transform = `translate(${p.x + 10}px, ${p.y - 26}px)`;
      }
    };
    scene.setPerspective(live.current.ws.perspective);
    sceneRef.current = scene;
    // For tests in development: what the scene holds.
    if ((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV) (window as unknown as { __geoScene?: GeoScene }).__geoScene = scene;
    const api: SceneApi = {
      fit: () => scene.fit(),
      // "top" is "В план": the plan of what is on the screen now, not of everything.
      view: (preset) => (preset === "top" ? scene.toPlan() : scene.view(preset)),
      drawSection: () => {
        // Drawn where the view is now: the camera is not turned and nothing is refitted.
        setDrawing(null);
      },
      openSection: (line) => {
        scene.setSection(line);
        scene.setClip(true);
        scene.view("section");
      },
      sectionFrame: () => scene.sectionFrame(),
      sectionThroughHole: (setId, holeId) => throughHole(setId, holeId),
      mark: (key, corners, color) => scene.setMarks(key, corners, color),
    };
    apiRef.current = api;
    live.current.registerApi?.(api);
    return () => {
      scene.dispose();
      sceneRef.current = null;
      loadedSets.current.clear();
      loadedWireframes.current.clear();
      loadedLines.current.clear();
      loadedRasters.current.clear();
      loadedBlocks.current.clear();
      readyBlocks.current.clear();
      loadedLogs.current.clear();
    };
  }, []);

  // ---- drillhole sets: load what is shown, drop what was deleted
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(sets.map((d) => d.id));
    // Deleted sets go away from what the scene really holds (a load in flight may have drawn one after its key changed).
    for (const id of scene.loadedIds().sets) if (!ids.has(id)) scene.removeDrillholes(id);
    for (const id of [...loadedSets.current.keys()]) if (!ids.has(id)) loadedSets.current.delete(id);
    for (const d of sets) {
      if (!isShown(ws, layerKey.set(d.id)) || !d.counts?.collar) continue;
      // Holes are plain paths and collars; what is drawn along them comes from the displays under «Стволы».
      const key = `${refreshKey}`;
      if (loadedSets.current.get(d.id) === key) continue;
      loadedSets.current.set(d.id, key);
      setLoading((n) => n + 1);
      Promise.all([loadTrajectories(d.id, refreshKey), loadHoles(d.id, refreshKey)])
        .then(([trajectories, holes]) => {
          // Deleted or superseded meanwhile: the newer state decides.
          if (!sceneRef.current || loadedSets.current.get(d.id) !== key) return;
          holesBySet.current.set(d.id, new Map(holes.map((h) => [h.hole_id, h])));
          sceneRef.current.setDrillholes(d.id, trajectories, null, null, new Map(holes.map((h) => [h.hole_id, auditColor(h)])));
          applyStyles();
          setError(null);
        })
        .catch((e: Error) => {
          if (loadedSets.current.get(d.id) !== key) return;
          loadedSets.current.delete(d.id);
          setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sets, ws.hidden, refreshKey]);

  // ---- collars: marker and labels of every set
  const collarStyles = props.collarStyles;
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    for (const d of sets) scene.setCollarStyle(d.id, collarStyles?.[d.id] ?? DEFAULT_COLLAR_STYLE);
  }, [sets, collarStyles]);

  // ---- data along the holes (saved displays that are on): tubes, panels, graphs, text
  const logs = props.logs;
  const logsKey = JSON.stringify((logs ?? []).map((l) => [l.id, l.setId, l.settings]));
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const wanted = new Map((logs ?? []).map((l) => [l.id, l]));
    for (const key of scene.loadedIds().downholes) if (!wanted.has(Number(key.slice(4)))) scene.removeDownhole(key);
    for (const id of [...loadedLogs.current.keys()]) {
      if (!wanted.has(id)) {
        scene.removeDownhole(`log:${id}`);
        loadedLogs.current.delete(id);
        setLogLegends((prev) => prev.filter((x) => x.id !== id));
      }
    }
    // Displays of one set stand side by side: each starts after the columns and tubes of those before it.
    const taken = new Map<number, StackStart>();
    for (const log of wanted.values()) {
      const start = taken.get(log.setId) ?? { units: 0, tubes: 0 };
      taken.set(log.setId, stackOf(log.settings, start));
      const key = `${refreshKey}:${JSON.stringify(start)}:${JSON.stringify(log.settings)}`;
      if (loadedLogs.current.get(log.id) === key) continue;
      loadedLogs.current.set(log.id, key);
      const s = log.settings;
      const fields: Record<string, string[]> = {};
      Object.assign(fields, displayFields(s));
      if (s.tracks.length === 0 || (!s.allHoles && s.holes.length === 0)) {
        scene.removeDownhole(`log:${log.id}`);
        continue;
      }
      setLoading((n) => n + 1);
      Promise.all([loadTrajectories(log.setId, refreshKey), loadDownhole(log.setId, s.allHoles ? "all" : s.holes, fields, refreshKey)])
        .then(([trajectories, data]) => {
          if (!sceneRef.current || loadedLogs.current.get(log.id) !== key) return;
          const origin = sceneRef.current.sceneOrigin(trajectories.extent);
          if (!origin) {
            loadedLogs.current.delete(log.id); // no holes with coordinates yet: tried again on the next change
            return;
          }
          const build = buildDownhole(trajectories.holes, data, s, origin, start);
          sceneRef.current.setDownhole(`log:${log.id}`, build);
          setLogLegends((prev) => [...prev.filter((x) => x.id !== log.id), { id: log.id, name: log.name, entries: build.legend, holes: build.holes, intervals: build.intervals }]);
          setError(null);
        })
        .catch((e: Error) => {
          if (loadedLogs.current.get(log.id) === key) loadedLogs.current.delete(log.id);
          setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logsKey, refreshKey]);

  // ---- wireframes: coarse to fine — the coarsest simplified copy first (a picture within
  // seconds), then finer ones, then the full detail in spatial parts. Levels are loaded once.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(wireframes.map((w) => w.id));
    for (const id of scene.loadedIds().wireframes) if (!ids.has(id)) scene.removeWireframe(id);
    for (const id of [...loadedWireframes.current.keys()]) if (!ids.has(id)) loadedWireframes.current.delete(id);
    for (const w of wireframes) {
      if (loadedWireframes.current.get(w.id) === w.lod_levels || !isShown(ws, layerKey.wireframe(w.id))) continue;
      const first = !loadedWireframes.current.has(w.id);
      loadedWireframes.current.set(w.id, w.lod_levels);
      setLoading((n) => n + 1);
      // A load runs to its end unless the wireframe is deleted or reloaded meanwhile (its key changes).
      const current = () => !!sceneRef.current && loadedWireframes.current.get(w.id) === w.lod_levels;
      (async () => {
        const info = await loadWireframeInfo(w.id, w.lod_levels);
        const view = sceneRef.current;
        if (!current() || !view) throw new Error("cancelled");
        if (first || !view.hasWireframeLevel(w.id, 0)) {
          view.addWireframe(w.id, info.origin, w.extent.min, w.extent.max, w.color, ws.wireframeOpacity);
          applyStyles();
        }
        const levels = [...info.lods.map((l) => l.level).sort((x, y) => y - x), 0];
        for (const level of levels) {
          if (sceneRef.current?.hasWireframeLevel(w.id, level)) continue;
          const buffers = await loadWireframeLevel(info, level);
          if (!current() || sceneRef.current !== view) throw new Error("cancelled");
          view.setWireframeLevel(w.id, level, buffers.vertices, buffers.triangles, level === 0 ? info.chunks : null);
          setWireframesShown((n) => n + 1);
        }
      })()
        .catch((e: Error) => {
          // Deleted while loading: whatever was drawn goes too.
          if (!loadedWireframes.current.has(w.id)) sceneRef.current?.removeWireframe(w.id);
          if (e.message === "cancelled" || loadedWireframes.current.get(w.id) !== w.lod_levels) return;
          loadedWireframes.current.delete(w.id);
          setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wireframes, ws.hidden]);

  // ---- exact sections of wireframes (server, float64) while the scene is clipped to a section
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const shown = wireframes.filter((w) => isShown(ws, layerKey.wireframe(w.id)) && loadedWireframes.current.has(w.id)).map((w) => w.id);
    if (!ws.clip || !section || shown.length === 0) {
      for (const w of wireframes) scene.setWireframeCut(w.id, null);
      if (cuts.current.size) {
        cuts.current.clear();
        setCutsKey((k) => k + 1);
      }
      return;
    }
    const a = (section.az * Math.PI) / 180;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getSections(shown, [section.x0, section.y0, 0], [Math.cos(a), -Math.sin(a), 0], controller.signal)
        .then((r) => {
          cuts.current.clear();
          r.sections.forEach((cut) => {
            sceneRef.current?.setWireframeCut(cut.id, cut.polylines);
            cuts.current.set(cut.id, { origin: cut.origin, polylines: cut.polylines });
          });
          setCutsKey((k) => k + 1);
        })
        .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section?.x0, section?.y0, section?.az, ws.clip, wireframes, ws.hidden, wireframesShown]);

  // ---- line layers: geometry is loaded once per id and version (a save of the editor makes a new version)
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(lineLayers.map((l) => l.id));
    for (const id of scene.loadedIds().lines) if (!ids.has(id)) scene.removeLines(id);
    for (const id of [...loadedLines.current.keys()]) if (!ids.has(id)) loadedLines.current.delete(id);
    for (const layer of lineLayers) {
      if (loadedLines.current.get(layer.id) === layer.version || !isShown(ws, layerKey.lines(layer.id))) continue;
      loadedLines.current.set(layer.id, layer.version);
      if (layer.point_count === 0) {
        scene.removeLines(layer.id);
        continue;
      }
      setLoading((n) => n + 1);
      loadLines(layer.id, layer.version)
        .then((g) => {
          if (!sceneRef.current || loadedLines.current.get(layer.id) !== layer.version) return; // deleted or saved again meanwhile
          sceneRef.current.setLines(layer.id, g.origin, g.vertices, g.offsets, layer.color, g.bounds);
          applyStyles();
        })
        .catch((e: Error) => {
          if (loadedLines.current.get(layer.id) !== layer.version) return;
          loadedLines.current.delete(layer.id);
          setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineLayers, ws.hidden]);

  // ---- rasters: the image is a texture on a quad placed by its georeference
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(rasters.map((r) => r.id));
    for (const id of scene.loadedIds().rasters) if (!ids.has(id)) scene.removeRaster(id);
    for (const id of [...loadedRasters.current]) if (!ids.has(id)) loadedRasters.current.delete(id);
    for (const r of rasters) {
      if (loadedRasters.current.has(r.id) || !isShown(ws, layerKey.raster(r.id))) continue;
      loadedRasters.current.add(r.id);
      scene.setRaster(r.id, rasterImageUrl(r.id), r.georef.corners, r.elevation, r.opacity, true, r.georef.corners3d ?? null);
    }
    applyStyles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rasters, ws.hidden]);

  // ---- block models: geometry once, colours on every style change
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(blockModels.map((d) => d.id));
    for (const id of scene.loadedIds().blocks) if (!ids.has(id)) scene.removeBlockModel(id);
    for (const id of [...loadedBlocks.current]) {
      if (!ids.has(id)) {
        loadedBlocks.current.delete(id);
        readyBlocks.current.delete(id);
      }
    }
    for (const d of blockModels) {
      if (loadedBlocks.current.has(d.id) || !isShown(ws, layerKey.blocks(d.id))) continue;
      loadedBlocks.current.add(d.id);
      const meta = blockMeta(d);
      setLoading((n) => n + 1);
      getBlockRender(d.id)
        .then((render) => {
          if (!loadedBlocks.current.has(d.id) || sceneRef.current !== scene) return; // deleted meanwhile
          scene.addBlockModel(d.id, render, meta.render_origin, meta.extent.min, meta.extent.max);
          readyBlocks.current.add(d.id);
          void styleBlocks();
        })
        .catch((e: Error) => {
          loadedBlocks.current.delete(d.id);
          setError(`Блочная модель не загрузилась: ${e.message}`);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blockModels, ws.hidden]);

  async function styleBlocks() {
    const scene = sceneRef.current;
    if (!scene) return;
    const run = ++styleRun.current;
    let legend: typeof blockLegend = null;
    for (const d of live.current.datasets ?? []) {
      if (d.kind !== "blocks" || !readyBlocks.current.has(d.id)) continue;
      const meta = blockMeta(d);
      const style = live.current.blockStyles?.[d.id] ?? defaultBlockStyle(meta);
      const attr = meta.attributes.find((a) => a.key === style.attribute) ?? null;
      let values: Float32Array | null = null;
      if (attr) {
        // Values change when a block is edited: the statistics then change too.
        const key = `${d.id}:${attr.key}:${JSON.stringify(attr.stats)}:${attr.labels?.length ?? 0}`;
        values = blockValues.current.get(key) ?? null;
        if (!values) {
          try {
            values = await getBlockValues(d.id, attr.key);
          } catch (e) {
            setError(`Значения поля ${attr.name} не загрузились: ${(e as Error).message}`);
            continue;
          }
          blockValues.current.set(key, values);
        }
      }
      if (run !== styleRun.current) return;
      const shownLayer = isShown(live.current.ws, layerKey.blocks(d.id));
      const c = blockColors(meta.block_count, attr, values, style);
      // Too many blocks for the browser: draw only those that can be seen from outside.
      let shell = false;
      if (c.shown > MAX_SHOWN_BLOCKS) {
        let mask = blockShells.current.get(d.id) ?? null;
        if (!mask) {
          try {
            mask = await getBlockExposed(d.id);
            blockShells.current.set(d.id, mask);
          } catch (e) {
            setError(`Оболочка блочной модели не загрузилась: ${(e as Error).message}`);
          }
        }
        if (run !== styleRun.current) return;
        if (mask && mask.length === c.visible.length) {
          for (let i = 0; i < mask.length; i++) c.visible[i] &= mask[i];
          shell = true;
        }
      }
      // Still too many (a sparse model has a thick shell): every k-th block, so the model is seen at all.
      let thinned = 1;
      if (c.shown > MAX_SHOWN_BLOCKS) {
        let count = 0;
        for (let i = 0; i < c.visible.length; i++) count += c.visible[i];
        if (count > MAX_SHOWN_BLOCKS) {
          thinned = Math.ceil(count / MAX_SHOWN_BLOCKS);
          let k = 0;
          for (let i = 0; i < c.visible.length; i++) if (c.visible[i] && k++ % thinned !== 0) c.visible[i] = 0;
        }
      }
      const drawn = scene.setBlockStyle(d.id, c.colors, c.visible, style.opacity, shownLayer);
      if (shownLayer && !legend) {
        legend = { name: attr ? attr.name : d.name, unit: attr?.unit ?? null, scale: c.scale, shown: c.shown, total: meta.block_count, tooMany: drawn < 0, shell: shell || thinned > 1 ? Math.max(drawn, 0) : null, thinned };
      }
    }
    if (run === styleRun.current) setBlockLegend(legend);
  }
  useEffect(() => void styleBlocks(), [props.blockStyles, ws.hidden, datasets]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyStyles() {
    const scene = sceneRef.current;
    if (!scene) return;
    const { ws: state, datasets: list } = live.current;
    for (const d of list ?? []) {
      if (d.kind === "drillholes") {
        scene.setDrillholeStyle(d.id, {
          visible: isShown(state, layerKey.set(d.id)),
          paths: isShown(state, layerKey.setPart(d.id, "paths")),
          intervals: isShown(state, layerKey.setPart(d.id, "intervals")),
          collars: isShown(state, layerKey.setPart(d.id, "collars")),
        });
      }
      for (const w of d.wireframes ?? []) scene.setWireframeStyle(w.id, w.color, isShown(state, layerKey.wireframe(w.id)), state.wireframeOpacity);
      // The layer being edited is drawn by the editor (its working copy), not from the store.
      for (const l of d.lines ?? []) scene.setLinesStyle(l.id, l.color, isShown(state, layerKey.lines(l.id)) && live.current.edit?.layerId !== l.id);
      for (const r of d.rasters ?? []) scene.setRasterStyle(r.id, isShown(state, layerKey.raster(r.id)), r.opacity, r.elevation, r.georef.corners, r.georef.corners3d ?? null);
    }
  }

  const edit = props.edit ?? null;
  useEffect(applyStyles, [datasets, ws.hidden, ws.wireframeOpacity, edit]); // eslint-disable-line react-hooks/exhaustive-deps
  useEditScene(sceneRef, edit, {
    section, clip: ws.clip, refreshKey, cuts: cuts.current, cutsKey,
    lineLayers: lineLayers.filter((l) => l.id !== edit?.layerId && l.point_count > 0 && isShown(ws, layerKey.lines(l.id))).map((l) => [l.id, l.version]),
    sets: sets.filter((d) => d.counts?.collar && isShown(ws, layerKey.set(d.id))).map((d) => d.id),
    wireframes: wireframes.filter((w) => isShown(ws, layerKey.wireframe(w.id))).map((w) => w.id),
    onSave: () => props.onEditSave?.(),
  });
  useEffect(() => sceneRef.current?.setVerticalExaggeration(ws.vexag), [ws.vexag]);
  useEffect(() => sceneRef.current?.setGridVisible(ws.grid), [ws.grid]);
  useEffect(() => sceneRef.current?.setSelectedWireframe(props.selectedWireframe ?? null), [props.selectedWireframe, loading]);
  useEffect(() => sceneRef.current?.setSelected(activeSet !== null && selectedHole ? { setId: activeSet, holeId: selectedHole } : null), [activeSet, selectedHole, loading]);
  useEffect(() => sceneRef.current?.setSection(section), [section]);

  // Clipping needs a line: without one it stays off.
  useEffect(() => sceneRef.current?.setClip(ws.clip && section !== null), [ws.clip, section]);
  useEffect(() => sceneRef.current?.setPerspective(ws.perspective), [ws.perspective]);

  useEffect(() => sceneRef.current?.setDraft(drawing), [drawing]);
  useEffect(() => {
    if (drawing === false) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawing(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing]);

  function collarsXY(): [number, number][] {
    const out: [number, number][] = [];
    for (const [setId, holes] of holesBySet.current) {
      if (!isShown(live.current.ws, layerKey.set(setId))) continue;
      for (const h of holes.values()) if (h.x !== null && h.y !== null) out.push([h.x, h.y]);
    }
    return out;
  }

  function autoSection(): SectionLine {
    const towards = live.current.section?.towards ?? DEFAULT_WINDOW;
    const away = live.current.section?.away ?? DEFAULT_WINDOW;
    const points = collarsXY();
    if (points.length) return defaultSection(points, towards, away);
    const c = sceneRef.current?.centre();
    return { x0: c?.x ?? 0, y0: c?.y ?? 0, az: 90, length: null, towards, away };
  }

  const showMap = mapOpen && (section !== null || drawing !== false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const overview = useMemo(() => (showMap ? sceneRef.current?.planOverview() ?? null : null), [showMap, loading, datasets, ws.hidden, section === null]);

  const setSection = (patch: Partial<SectionLine>) => props.onSection({ ...(section ?? autoSection()), ...patch });

  /**
   * Through the collar along the hole: the whole hole lies in the section plane.
   * A vertical hole keeps the current azimuth (or the one across the collars).
   */
  function throughHole(setId: number, holeId: string): SectionLine | null {
    const collar = holesBySet.current.get(setId)?.get(holeId);
    if (!collar || collar.x === null || collar.y === null) return null;
    const base = live.current.section ?? autoSection();
    const az = sceneRef.current?.holeAzimuth({ setId, holeId }) ?? base.az;
    return { ...base, x0: collar.x, y0: collar.y, az, length: null };
  }
  /** A new line (auto, through a hole): clip to it and look at its plane. */
  const openSection = (line: SectionLine) => {
    props.onSection(line);
    props.onWorkspace({ clip: true });
    apiRef.current?.openSection(line);
  };
  // The next profile is one window further: away from the viewer for a positive step.
  const step = (direction: 1 | -1) => section && props.onSection(shiftSection(section, -(section.towards + section.away) * direction));
  const windowInput = (key: "towards" | "away", label: string, title: string) => (
    <label title={title}>
      {label}{" "}
      <input className="num" type="number" min={0} step={5} value={section?.[key] ?? DEFAULT_WINDOW}
        onChange={(e) => setSection({ [key]: Math.max(0, Number(e.target.value) || 0) })} aria-label={title} />
    </label>
  );
  const selectedCollar = activeSet !== null && selectedHole ? holesBySet.current.get(activeSet)?.get(selectedHole) : undefined;
  const hoverInfo = hover ? holesBySet.current.get(hover.setId)?.get(hover.holeId) : undefined;
  const hoverSet = hover ? sets.find((d) => d.id === hover.setId) : undefined;
  const nothing = datasets !== null && sets.every((d) => !d.counts?.collar) && wireframes.length === 0 && lineLayers.length === 0 && rasters.length === 0 && blockModels.length === 0 && !edit;

  return (
    <div className="scene-view">
      <div className="scene-toolbar">
        <div className="tool-group" role="toolbar" aria-label="Повернуть вид">
          <ToolButton icon="cubeTop" label="В план" hint="Вид сверху (Y вверх, X вправо) на то, что сейчас на экране" keys="T" onClick={() => sceneRef.current?.toPlan()} />
          <ToolButton icon="cubeLeft" label="Сбоку Z/X" hint="Вид сбоку на север: Z вверх, X вправо" keys="X" onClick={() => sceneRef.current?.view("front")} />
          <ToolButton icon="cubeRight" label="Сбоку Z/Y" hint="Вид сбоку на запад: Z вверх, Y вправо" keys="Y" onClick={() => sceneRef.current?.view("side")} />
          <ToolButton icon="maximize" label="Вписать" hint="Показать всё, не поворачивая вид" keys="F" onClick={() => sceneRef.current?.fit()} />
        </div>
        <div className="tool-group" role="toolbar" aria-label="Разрез">
          <ToolButton icon="vwSection" label="Разрез" hint="Линия разреза: два клика — начало и конец, в текущем виде или на плане" keys="S" active={drawing !== false}
            onClick={() => (drawing === false ? apiRef.current?.drawSection() : setDrawing(false))} />
          <ToolButton icon="vwHole" label="Разрез через скважину" hint="Разрез вдоль выбранной скважины через её устье: вся скважина в плоскости разреза"
            off="Выберите скважину (щелчок по устью)" disabled={!selectedCollar || selectedCollar.x === null}
            onClick={() => {
              const line = activeSet !== null && selectedHole ? throughHole(activeSet, selectedHole) : null;
              if (line) openSection(line);
            }} />
          {!section && <ToolButton icon="vwAuto" label="Авто-разрез" hint="Разрез поперёк простирания устьев, через их центр" onClick={() => openSection(autoSection())} />}
          {section && (
            <>
              <ToolButton icon="vwClip" label="Отсечение" hint="Отсекать всё за пределами окна разреза" keys="C" active={ws.clip} onClick={() => props.onWorkspace({ clip: !ws.clip })} />
              <ToolButton icon="vwPrev" label="Предыдущий профиль" hint="Сдвиг на ширину окна к наблюдателю" keys="[" onClick={() => step(-1)} />
              <ToolButton icon="vwNext" label="Следующий профиль" hint="Сдвиг на ширину окна от наблюдателя" keys="]" onClick={() => step(1)} />
              <ToolButton icon="vwToPlane" label="В плоскость разреза" hint="Повернуть вид в плоскость разреза: Z вверх, линия слева направо" onClick={() => sceneRef.current?.view("section")} />
              <ToolButton icon="vwMap" label="План" hint="Маленький план: данные, линия разреза и окно просмотра" active={mapOpen} onClick={() => toggleMap(!mapOpen)} />
              <ToolButton icon="vwReset" label="Сбросить разрез" hint="Убрать линию разреза и отсечение" onClick={() => { props.onSection(null); props.onWorkspace({ clip: false }); }} />
            </>
          )}
        </div>
        {section && (
          <>
            {windowInput("towards", "От", "Окно разреза к наблюдателю, в единицах карты")}
            {windowInput("away", "До", "Окно разреза от наблюдателя, в единицах карты")}
            <label title="Азимут линии разреза">
              Аз.{" "}
              <input className="num" type="number" step={5} value={Math.round(section.az * 10) / 10}
                onChange={(e) => setSection({ az: (((Number(e.target.value) || 0) % 360) + 360) % 360 })} aria-label="Азимут линии разреза" />°
            </label>
          </>
        )}
        <span className="toolbar-sep" />
        <label className="tool-field" title="Вертикальное преувеличение">
          <Icon name="vwVexag" size={16} />
          <select value={ws.vexag} onChange={(e) => props.onWorkspace({ vexag: Number(e.target.value) })} aria-label="Вертикальное преувеличение">
            {VEXAG_STEPS.map((v) => <option key={v} value={v}>×{v}</option>)}
          </select>
        </label>
        <label className="tool-field" title="Непрозрачность каркасов">
          <Icon name="vwOpacity" size={16} />
          <input type="range" min={0.1} max={1} step={0.05} value={ws.wireframeOpacity}
            onChange={(e) => props.onWorkspace({ wireframeOpacity: Number(e.target.value) })} aria-label="Непрозрачность каркасов" />
        </label>
        <div className="tool-group" role="toolbar" aria-label="Отображение">
          <ToolButton icon="vwPerspective" label="Перспектива" hint="Включено — перспектива; выключено — ортогональная проекция, как на планах и разрезах" active={ws.perspective} onClick={() => props.onWorkspace({ perspective: !ws.perspective })} />
          <ToolButton icon="vwGrid" label="Сетка" hint="Координатная сетка с подписями" active={ws.grid} onClick={() => props.onWorkspace({ grid: !ws.grid })} />
        </div>
      </div>
      {props.banner}
      {edit && <EditToolbar edit={edit} onSave={() => props.onEditSave?.()} onExit={() => props.onEditExit?.()} sectionOn={ws.clip && section !== null} />}
      {edit && <AssembleBar edit={edit} sceneRef={sceneRef} onCreate={(lines, closed) => props.onAssemble?.(lines, closed)} />}
      {blockLegend && (
        <div className="scene-legend block-legend">
          {blockLegend.scale ? <ScaleStrip component={`Блоки: ${blockLegend.name}`} unit={blockLegend.unit ? unitTitle(blockLegend.unit as never) : null} scale={blockLegend.scale} log={blockLegend.scale.log} /> : <strong>Блоки: {blockLegend.name}</strong>}
          <span className="muted small">
            {blockLegend.tooMany
              ? ` · показать можно до ${MAX_SHOWN_BLOCKS.toLocaleString("ru-RU")} блоков: сузьте отбор (${blockLegend.shown.toLocaleString("ru-RU")})`
              : blockLegend.shell !== null
              ? ` · отобрано ${blockLegend.shown.toLocaleString("ru-RU")} из ${blockLegend.total.toLocaleString("ru-RU")}, показано ${blockLegend.shell.toLocaleString("ru-RU")}: `
                + (blockLegend.thinned > 1 ? `внешние блоки, каждый ${blockLegend.thinned}-й — сузьте отбор, чтобы увидеть все` : "внешняя оболочка")
              : ` · показано ${blockLegend.shown.toLocaleString("ru-RU")} из ${blockLegend.total.toLocaleString("ru-RU")}`}
          </span>
        </div>
      )}

      {logLegends.length > 0 && (
        <div className="scene-legend downhole-legend" aria-label="Данные по скважинам">
          {logLegends.map((l) => (
            <div key={l.id} className="downhole-legend-item">
              <strong title={`${l.holes} скв., ${l.intervals} интервалов`}>{l.name}</strong>
              {l.intervals === 0 && <span className="legend-warning">нет интервалов на скважинах: проверьте имена скважин в таблице и в устьях</span>}
              {l.entries.map((e) => (
                <span key={e.trackId} className="downhole-legend-track">
                  <button type="button" className="legend-layer" title={l.id > 0 ? "Открыть настройки этого слоя" : "Настройки — в открытой форме"} onClick={() => props.onLegendLayer?.(l.id, e.trackId)}>
                    {e.title}{e.unit ? `, ${e.unit}` : ""}
                  </button>
                  {e.items.slice(0, 12).map(([label, color], i) => (
                    <span key={label} className="legend-swatch" title={e.counts[i] !== undefined ? `${e.counts[i].toLocaleString("ru-RU")} интервалов` : undefined}>
                      <i style={{ background: color }} />{label}{e.counts[i] ? <span className="muted"> · {e.counts[i].toLocaleString("ru-RU")}</span> : null}
                    </span>
                  ))}
                  {e.items.length > 12 && <span className="muted small">… ещё {e.items.length - 12}</span>}
                  {e.range && e.items.length === 0 && (
                    <span className="muted small legend-range">
                      {e.gradient && <i className="legend-gradient" style={{ background: e.gradient }} />}
                      {e.range[0].toLocaleString("ru-RU")} … {e.range[1].toLocaleString("ru-RU")}
                    </span>
                  )}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
      <div className={drawing !== false || (edit && edit.tool !== "select") ? "canvas three crosshair" : "canvas three"} ref={containerRef}>
        {edit && <EditCursor edit={edit} />}
        <div ref={labelRef} className="label3d" style={{ display: "none" }}>{selectedHole}</div>
        {hover && (
          <div className="tooltip" style={{ left: Math.min(hover.x + 14, (containerRef.current?.clientWidth ?? 400) - 200), top: Math.max(hover.y - 10, 4) }}>
            <strong>{hover.holeId}</strong>
            {hoverSet && sets.length > 1 && <div className="muted">{hoverSet.name}</div>}
            {hoverInfo && <div>Глубина: {hoverInfo.td ?? "—"} м</div>}
          </div>
        )}
        {webglError && <div className="canvas-note error">3D недоступно: {webglError}</div>}
        {loading > 0 && <div className="canvas-note corner">Загрузка…</div>}
        {error && <div className="canvas-note error">{error}</div>}
        {nothing && !webglError && <div className="canvas-note">Нет объектов для показа. Загрузите данные в дереве слева.</div>}
        {drawing !== false && (
          <div className="canvas-note top">{drawing === null ? "Укажите начало профиля" : "Укажите конец профиля"} — в виде или на плане · Esc — отмена</div>
        )}
        {showMap && overview && (
          <SectionMap overview={overview} section={section} draft={drawing || null} drawing={drawing !== false}
            onPoint={(x, y) => addPointRef.current(x, y)} onClose={() => toggleMap(false)} />
        )}
        <div className="coords">
          {section && ws.clip
            ? `Разрез: азимут ${Math.round(section.az)}°, окно от ${section.towards} до ${section.away} · `
            : ""}
          ЛКМ — вращение, колесо — масштаб, ПКМ — сдвиг · клик по устью — выбор скважины
        </div>
      </div>
    </div>
  );
}
