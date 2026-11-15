import { useEffect, useMemo, useRef, useState } from "react";
import { blockMeta, getBlockRender, getBlockValues, getSections, rasterImageUrl, type Dataset, type Hole, type IntervalsData, type ValueStats, type VerticalPlacement } from "../api";
import { VEXAG_STEPS, isShown, layerKey, type ViewPreset, type WorkspaceState } from "../shell/workspace";
import { makeScale } from "./colorScale";
import { ScaleStrip, type VizSettings } from "./ComponentPanel";
import { azimuthDeg, DEFAULT_WINDOW, defaultSection, shiftSection, type SectionLine } from "./geometry";
import { SectionMap } from "./SectionMap";
import { GeoScene, MAX_SHOWN_BLOCKS, type Hover } from "./scene3d";
import { blockColors, defaultBlockStyle, type BlockStyle } from "./blockStyle";
import { unitTitle } from "../shell/BlockModelPanel";
import type { ColorScale } from "./colorScale";
import { loadHoles, loadIntervals, loadLines, loadTrajectories, loadWireframeInfo, loadWireframeLevel } from "./useVizData";

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
}

/** The single main view: every visible object of the project in 3D, plan or section. */
export function SceneView(props: SceneViewProps) {
  const { datasets, ws, refreshKey, viz, activeSet, selectedHole, section } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<GeoScene | null>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<SceneApi | null>(null);
  const loadedSets = useRef(new Map<number, string>());
  /** id -> number of simplified levels it was loaded with */
  const loadedWireframes = useRef(new Map<number, number>());
  const [wireframesShown, setWireframesShown] = useState(0);
  const loadedLines = useRef(new Set<number>());
  const loadedRasters = useRef(new Set<number>());
  // Block models: those requested, those whose geometry is in the scene, attribute values by "id:key".
  const loadedBlocks = useRef(new Set<number>());
  const readyBlocks = useRef(new Set<number>());
  const blockValues = useRef(new Map<string, Float32Array>());
  const styleRun = useRef(0);
  const [blockLegend, setBlockLegend] = useState<{ name: string; unit: string | null; scale: ColorScale | null; shown: number; total: number; tooMany: boolean } | null>(null);
  const holesBySet = useRef(new Map<number, Map<string, Hole>>());
  const [webglError, setWebglError] = useState<string | null>(null);
  const [loading, setLoading] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [drawing, setDrawing] = useState<{ x: number; y: number } | null | false>(false);
  const [refStats, setRefStats] = useState<ValueStats | null>(null);

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
  const component = viz.component ?? sets.find((d) => d.id === activeSet)?.components?.[0]?.name ?? sets[0]?.components?.[0]?.name ?? null;
  const scale = useMemo(() => makeScale(viz, refStats), [viz, refStats]);
  const unit = sets.flatMap((d) => d.components ?? []).find((c) => c.name === component)?.unit ?? null;

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
    };
  }, []);

  // ---- drillhole sets: load what is shown, drop what was deleted
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(sets.map((d) => d.id));
    for (const id of [...loadedSets.current.keys()]) {
      if (!ids.has(id)) {
        scene.removeDrillholes(id);
        loadedSets.current.delete(id);
      }
    }
    let cancelled = false;
    const reference = sets.find((d) => d.id === activeSet) ?? sets[0];
    for (const d of sets) {
      if (!isShown(ws, layerKey.set(d.id)) || !d.counts?.collar) continue;
      const hasComponent = !!component && !!d.components?.some((c) => c.name === component);
      const key = `${refreshKey}:${hasComponent ? component : ""}:${scale ? `${scale.min}:${scale.max}:${viz.log}` : ""}`;
      if (loadedSets.current.get(d.id) === key) continue;
      loadedSets.current.set(d.id, key);
      setLoading((n) => n + 1);
      Promise.all([
        loadTrajectories(d.id, refreshKey),
        hasComponent ? loadIntervals(d.id, component!, refreshKey) : Promise.resolve<IntervalsData | null>(null),
        loadHoles(d.id, refreshKey),
      ])
        .then(([trajectories, intervals, holes]) => {
          if (cancelled || !sceneRef.current) {
            loadedSets.current.delete(d.id);
            return;
          }
          holesBySet.current.set(d.id, new Map(holes.map((h) => [h.hole_id, h])));
          if (d.id === reference?.id) setRefStats(intervals?.stats ?? null);
          sceneRef.current.setDrillholes(d.id, trajectories, intervals, scale, new Map(holes.map((h) => [h.hole_id, auditColor(h)])));
          applyStyles();
          setError(null);
        })
        .catch((e: Error) => {
          loadedSets.current.delete(d.id);
          if (!cancelled) setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    // The reference statistics follow the active set even when nothing reloads.
    if (reference && component && reference.components?.some((c) => c.name === component)) {
      loadIntervals(reference.id, component, refreshKey).then((i) => !cancelled && setRefStats(i.stats)).catch(() => undefined);
    } else {
      setRefStats(null);
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sets, ws.hidden, component, refreshKey, scale, activeSet]);

  // ---- wireframes: coarse to fine — the coarsest simplified copy first (a picture within
  // seconds), then finer ones, then the full detail in spatial parts. Levels are loaded once.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(wireframes.map((w) => w.id));
    for (const id of [...loadedWireframes.current.keys()]) {
      if (!ids.has(id)) {
        scene.removeWireframe(id);
        loadedWireframes.current.delete(id);
      }
    }
    let cancelled = false;
    for (const w of wireframes) {
      if (loadedWireframes.current.get(w.id) === w.lod_levels || !isShown(ws, layerKey.wireframe(w.id))) continue;
      const first = !loadedWireframes.current.has(w.id);
      loadedWireframes.current.set(w.id, w.lod_levels);
      setLoading((n) => n + 1);
      (async () => {
        const info = await loadWireframeInfo(w.id, w.lod_levels);
        if (cancelled || !sceneRef.current) throw new Error("cancelled");
        if (first || !sceneRef.current.hasWireframeLevel(w.id, 0)) {
          sceneRef.current.addWireframe(w.id, info.origin, w.extent.min, w.extent.max, w.color, ws.wireframeOpacity);
          applyStyles();
        }
        const levels = [...info.lods.map((l) => l.level).sort((x, y) => y - x), 0];
        for (const level of levels) {
          if (sceneRef.current?.hasWireframeLevel(w.id, level)) continue;
          const buffers = await loadWireframeLevel(info, level);
          if (cancelled || !sceneRef.current) throw new Error("cancelled");
          sceneRef.current.setWireframeLevel(w.id, level, buffers.vertices, buffers.triangles, level === 0 ? info.chunks : null);
          setWireframesShown((n) => n + 1);
        }
      })()
        .catch((e: Error) => {
          if (e.message === "cancelled") {
            if (loadedWireframes.current.get(w.id) === w.lod_levels) loadedWireframes.current.delete(w.id);
            return;
          }
          loadedWireframes.current.delete(w.id);
          if (!cancelled) setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wireframes, ws.hidden]);

  // ---- exact sections of wireframes (server, float64) while the scene is clipped to a section
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const shown = wireframes.filter((w) => isShown(ws, layerKey.wireframe(w.id)) && loadedWireframes.current.has(w.id)).map((w) => w.id);
    if (!ws.clip || !section || shown.length === 0) {
      for (const w of wireframes) scene.setWireframeCut(w.id, null);
      return;
    }
    const a = (section.az * Math.PI) / 180;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getSections(shown, [section.x0, section.y0, 0], [Math.cos(a), -Math.sin(a), 0], controller.signal)
        .then((r) => r.sections.forEach((cut) => sceneRef.current?.setWireframeCut(cut.id, cut.polylines)))
        .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section?.x0, section?.y0, section?.az, ws.clip, wireframes, ws.hidden, wireframesShown]);

  // ---- line layers: geometry is loaded once per id
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(lineLayers.map((l) => l.id));
    for (const id of [...loadedLines.current]) {
      if (!ids.has(id)) {
        scene.removeLines(id);
        loadedLines.current.delete(id);
      }
    }
    let cancelled = false;
    for (const layer of lineLayers) {
      if (loadedLines.current.has(layer.id) || !isShown(ws, layerKey.lines(layer.id))) continue;
      loadedLines.current.add(layer.id);
      setLoading((n) => n + 1);
      loadLines(layer.id)
        .then((g) => {
          if (cancelled || !sceneRef.current) {
            loadedLines.current.delete(layer.id);
            return;
          }
          sceneRef.current.setLines(layer.id, g.origin, g.vertices, g.offsets, layer.color, g.bounds);
          applyStyles();
        })
        .catch((e: Error) => {
          loadedLines.current.delete(layer.id);
          if (!cancelled) setError(e.message);
        })
        .finally(() => setLoading((n) => n - 1));
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineLayers, ws.hidden]);

  // ---- rasters: the image is a texture on a quad placed by its georeference
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(rasters.map((r) => r.id));
    for (const id of [...loadedRasters.current]) {
      if (!ids.has(id)) {
        scene.removeRaster(id);
        loadedRasters.current.delete(id);
      }
    }
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
    for (const id of [...loadedBlocks.current]) {
      if (!ids.has(id)) {
        scene.removeBlockModel(id);
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
          if (!loadedBlocks.current.has(d.id)) return;
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
        const key = `${d.id}:${attr.key}`;
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
      const drawn = scene.setBlockStyle(d.id, c.colors, c.visible, style.opacity, shownLayer);
      if (shownLayer && !legend) {
        legend = { name: attr ? attr.name : d.name, unit: attr?.unit ?? null, scale: c.scale, shown: c.shown, total: meta.block_count, tooMany: drawn < 0 };
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
      for (const l of d.lines ?? []) scene.setLinesStyle(l.id, l.color, isShown(state, layerKey.lines(l.id)));
      for (const r of d.rasters ?? []) scene.setRasterStyle(r.id, isShown(state, layerKey.raster(r.id)), r.opacity, r.elevation, r.georef.corners, r.georef.corners3d ?? null);
    }
  }

  useEffect(applyStyles, [datasets, ws.hidden, ws.wireframeOpacity]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => sceneRef.current?.setVerticalExaggeration(ws.vexag), [ws.vexag]);
  useEffect(() => sceneRef.current?.setGridVisible(ws.grid), [ws.grid]);
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
  const nothing = datasets !== null && sets.every((d) => !d.counts?.collar) && wireframes.length === 0 && lineLayers.length === 0 && rasters.length === 0 && blockModels.length === 0;

  return (
    <div className="scene-view">
      <div className="scene-toolbar">
        <div className="segmented" aria-label="Повернуть вид">
          <button onClick={() => sceneRef.current?.toPlan()} title="В план: вид сверху (Y вверх, X вправо) на то, что сейчас на экране (T)">В план</button>
          <button onClick={() => sceneRef.current?.view("front")} title="Вид сбоку на север: Z вверх, X вправо (X)">Z/X</button>
          <button onClick={() => sceneRef.current?.view("side")} title="Вид сбоку на запад: Z вверх, Y вправо (Y)">Z/Y</button>
          <button onClick={() => sceneRef.current?.view("iso")} title="Изометрия (3)">3D</button>
        </div>
        <button onClick={() => sceneRef.current?.fit()} title="Показать всё, не поворачивая вид (F)">Вписать</button>
        <span className="toolbar-divider" />
        <button className={drawing !== false ? "active" : ""} title="Линия разреза: два клика — начало и конец, в текущем виде или на плане (S)"
          onClick={() => (drawing === false ? apiRef.current?.drawSection() : setDrawing(false))}>
          Разрез
        </button>
        {section && (
          <>
            <label className="check" title="Отсекать всё за пределами окна разреза (C)">
              <input type="checkbox" checked={ws.clip} onChange={(e) => props.onWorkspace({ clip: e.target.checked })} /> отсечение
            </label>
            {windowInput("towards", "От", "Окно разреза к наблюдателю, в единицах карты")}
            {windowInput("away", "До", "Окно разреза от наблюдателя, в единицах карты")}
            <button onClick={() => step(-1)} title="Предыдущий профиль: сдвиг на ширину окна к наблюдателю ([)">◀</button>
            <button onClick={() => step(1)} title="Следующий профиль: сдвиг на ширину окна от наблюдателя (])">▶</button>
            <label title="Азимут линии разреза">
              Аз.{" "}
              <input className="num" type="number" step={5} value={Math.round(section.az * 10) / 10}
                onChange={(e) => setSection({ az: (((Number(e.target.value) || 0) % 360) + 360) % 360 })} aria-label="Азимут линии разреза" />°
            </label>
            <button onClick={() => sceneRef.current?.view("section")} title="Повернуть вид в плоскость разреза: Z вверх, линия слева направо">В плоскость</button>
            <button className={mapOpen ? "active" : ""} onClick={() => toggleMap(!mapOpen)} title="Маленький план: данные, линия разреза и окно просмотра">План</button>
            <button onClick={() => { props.onSection(null); props.onWorkspace({ clip: false }); }} title="Убрать линию разреза и отсечение">Сбросить</button>
          </>
        )}
        <button
          onClick={() => {
            const line = activeSet !== null && selectedHole ? throughHole(activeSet, selectedHole) : null;
            if (line) openSection(line);
          }}
          disabled={!selectedCollar || selectedCollar.x === null}
          title="Разрез вдоль выбранной скважины через её устье: вся скважина в плоскости разреза"
        >
          Через скважину
        </button>
        {!section && <button onClick={() => openSection(autoSection())} title="Разрез поперёк простирания устьев, через их центр">Авто-разрез</button>}
        <span className="toolbar-sep" />
        <label title="Вертикальное преувеличение">
          Верт. ×{" "}
          <select value={ws.vexag} onChange={(e) => props.onWorkspace({ vexag: Number(e.target.value) })}>
            {VEXAG_STEPS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label title="Непрозрачность каркасов">
          Каркасы{" "}
          <input type="range" min={0.1} max={1} step={0.05} value={ws.wireframeOpacity}
            onChange={(e) => props.onWorkspace({ wireframeOpacity: Number(e.target.value) })} aria-label="Непрозрачность каркасов" />
        </label>
        <label className="check" title="Перспектива; выключено — ортогональная проекция, как на планах и разрезах">
          <input type="checkbox" checked={ws.perspective} onChange={(e) => props.onWorkspace({ perspective: e.target.checked })} /> перспектива
        </label>
        <label className="check"><input type="checkbox" checked={ws.grid} onChange={(e) => props.onWorkspace({ grid: e.target.checked })} /> сетка</label>
      </div>
      {component && <div className="scene-legend"><ScaleStrip component={component} unit={unit} scale={scale} log={viz.log} /></div>}
      {blockLegend && (
        <div className="scene-legend block-legend">
          {blockLegend.scale ? <ScaleStrip component={`Блоки: ${blockLegend.name}`} unit={blockLegend.unit ? unitTitle(blockLegend.unit as never) : null} scale={blockLegend.scale} log={blockLegend.scale.log} /> : <strong>Блоки: {blockLegend.name}</strong>}
          <span className="muted small">
            {blockLegend.tooMany
              ? ` · показать можно до ${MAX_SHOWN_BLOCKS.toLocaleString("ru-RU")} блоков: сузьте отбор (${blockLegend.shown.toLocaleString("ru-RU")})`
              : ` · показано ${blockLegend.shown.toLocaleString("ru-RU")} из ${blockLegend.total.toLocaleString("ru-RU")}`}
          </span>
        </div>
      )}

      <div className={drawing !== false ? "canvas three crosshair" : "canvas three"} ref={containerRef}>
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
