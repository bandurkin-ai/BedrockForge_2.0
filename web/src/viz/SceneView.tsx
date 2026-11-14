import { useEffect, useMemo, useRef, useState } from "react";
import { rasterImageUrl, type Dataset, type Hole, type IntervalsData, type ValueStats } from "../api";
import { MODE_TITLES, MODES, VEXAG_STEPS, isShown, layerKey, type WorkspaceState } from "../shell/workspace";
import { makeScale } from "./colorScale";
import { ScaleStrip, type VizSettings } from "./ComponentPanel";
import { azimuthDeg, defaultSection, type SectionLine } from "./geometry";
import { GeoScene, type Hover, type SceneMode } from "./scene3d";
import { loadHoles, loadIntervals, loadLines, loadTrajectories, loadWireframe } from "./useVizData";

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
  onMode: (mode: SceneMode) => void;
  onWorkspace: (patch: Partial<WorkspaceState>) => void;
  onPick: (setId: number, holeId: string | null) => void;
  onSection: (section: SectionLine | null) => void;
  onEmptyClick: () => void;
  /** Gives the shell a way to fit the view (the "Вписать" command). */
  registerFit?: (fit: () => void) => void;
}

/** The single main view: every visible object of the project in 3D, plan or section. */
export function SceneView(props: SceneViewProps) {
  const { datasets, ws, refreshKey, viz, activeSet, selectedHole, section } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<GeoScene | null>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const loadedSets = useRef(new Map<number, string>());
  const loadedWireframes = useRef(new Set<number>());
  const loadedLines = useRef(new Set<number>());
  const loadedRasters = useRef(new Set<number>());
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

  const sets = useMemo(() => (datasets ?? []).filter((d) => d.kind === "drillholes"), [datasets]);
  const wireframes = useMemo(() => (datasets ?? []).flatMap((d) => d.wireframes ?? []), [datasets]);
  const lineLayers = useMemo(() => (datasets ?? []).flatMap((d) => d.lines ?? []), [datasets]);
  const rasters = useMemo(() => (datasets ?? []).flatMap((d) => d.rasters ?? []), [datasets]);
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
    scene.onPlanClick = (x, y) => {
      const start = drawingRef.current;
      if (start === false) return false;
      if (start === null) {
        setDrawing({ x, y });
        return true;
      }
      const length = Math.hypot(x - start.x, y - start.y);
      if (length < 1) return true;
      live.current.onSection({ x0: start.x, y0: start.y, az: azimuthDeg(x - start.x, y - start.y), length, halfWidth: live.current.section?.halfWidth ?? 25 });
      setDrawing(false);
      live.current.onMode("section");
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
    scene.setMode(live.current.ws.mode);
    sceneRef.current = scene;
    live.current.registerFit?.(() => scene.fit());
    return () => {
      scene.dispose();
      sceneRef.current = null;
      loadedSets.current.clear();
      loadedWireframes.current.clear();
      loadedLines.current.clear();
      loadedRasters.current.clear();
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

  // ---- wireframes: geometry is loaded once per id
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const ids = new Set(wireframes.map((w) => w.id));
    for (const id of [...loadedWireframes.current]) {
      if (!ids.has(id)) {
        scene.removeWireframe(id);
        loadedWireframes.current.delete(id);
      }
    }
    let cancelled = false;
    for (const w of wireframes) {
      if (loadedWireframes.current.has(w.id) || !isShown(ws, layerKey.wireframe(w.id))) continue;
      loadedWireframes.current.add(w.id);
      setLoading((n) => n + 1);
      loadWireframe(w.id)
        .then((g) => {
          if (cancelled || !sceneRef.current) {
            loadedWireframes.current.delete(w.id);
            return;
          }
          sceneRef.current.setWireframe(w.id, g.origin, g.vertices, g.triangles, w.color, ws.wireframeOpacity);
          applyStyles();
        })
        .catch((e: Error) => {
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
          sceneRef.current.setLines(layer.id, g.origin, g.vertices, g.offsets, layer.color);
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
      scene.setRaster(r.id, rasterImageUrl(r.id), r.georef.corners, r.elevation, r.opacity, true);
    }
    applyStyles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rasters, ws.hidden]);

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
      for (const r of d.rasters ?? []) scene.setRasterStyle(r.id, isShown(state, layerKey.raster(r.id)), r.opacity, r.elevation);
    }
  }

  useEffect(applyStyles, [datasets, ws.hidden, ws.wireframeOpacity]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => sceneRef.current?.setVerticalExaggeration(ws.vexag), [ws.vexag]);
  useEffect(() => sceneRef.current?.setGridVisible(ws.grid), [ws.grid]);
  useEffect(() => sceneRef.current?.setSelected(activeSet !== null && selectedHole ? { setId: activeSet, holeId: selectedHole } : null), [activeSet, selectedHole, loading]);
  useEffect(() => sceneRef.current?.setSection(section), [section]);

  // ---- mode: entering the section without a line picks one through the collars
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (ws.mode === "section" && !live.current.section) live.current.onSection(autoSection());
    scene.setMode(ws.mode);
    if (ws.mode !== "plan") setDrawing(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws.mode]);

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
    const halfWidth = live.current.section?.halfWidth ?? 25;
    const points = collarsXY();
    if (points.length) return defaultSection(points, halfWidth);
    const c = sceneRef.current?.centre();
    return { x0: c?.x ?? 0, y0: c?.y ?? 0, az: 90, length: null, halfWidth };
  }

  const setSection = (patch: Partial<SectionLine>) => props.onSection({ ...(section ?? autoSection()), ...patch });
  const step = (direction: 1 | -1) => {
    if (!section) return;
    const distance = section.halfWidth * 2 * direction;
    const a = (section.az * Math.PI) / 180;
    // Move across the line, to its right for a positive step.
    setSection({ x0: section.x0 + Math.cos(a) * distance, y0: section.y0 - Math.sin(a) * distance });
  };
  const selectedCollar = activeSet !== null && selectedHole ? holesBySet.current.get(activeSet)?.get(selectedHole) : undefined;
  const hoverInfo = hover ? holesBySet.current.get(hover.setId)?.get(hover.holeId) : undefined;
  const hoverSet = hover ? sets.find((d) => d.id === hover.setId) : undefined;
  const nothing = datasets !== null && sets.every((d) => !d.counts?.collar) && wireframes.length === 0 && lineLayers.length === 0 && rasters.length === 0;

  return (
    <div className="scene-view">
      <div className="scene-toolbar">
        <div className="segmented" role="radiogroup" aria-label="Режим вида">
          {MODES.map((m) => (
            <button key={m} role="radio" aria-checked={ws.mode === m} className={ws.mode === m ? "active" : ""} onClick={() => props.onMode(m)}>
              {MODE_TITLES[m]}
            </button>
          ))}
        </div>
        <button onClick={() => sceneRef.current?.fit()} title="Показать всё (F)">Вписать</button>
        {ws.mode === "3d" && (
          <>
            <button onClick={() => sceneRef.current?.fit("top")}>Сверху</button>
            <button onClick={() => sceneRef.current?.fit("side")}>Сбоку</button>
          </>
        )}
        {ws.mode === "plan" && (
          <button className={drawing !== false ? "active" : ""} onClick={() => setDrawing(drawing === false ? null : false)} title="Два клика на плане: начало и конец профиля">
            Линия разреза
          </button>
        )}
        {ws.mode === "section" && section && (
          <>
            <button onClick={() => step(-1)} title="Предыдущий профиль: сдвиг на ширину коридора">◀</button>
            <button onClick={() => step(1)} title="Следующий профиль: сдвиг на ширину коридора">▶</button>
            <label>
              Азимут{" "}
              <input className="num" type="number" step={5} value={Math.round(section.az * 10) / 10}
                onChange={(e) => setSection({ az: (((Number(e.target.value) || 0) % 360) + 360) % 360 })} aria-label="Азимут профиля" />°
            </label>
            <label>
              Коридор ±{" "}
              <input className="num" type="number" min={1} step={5} value={section.halfWidth}
                onChange={(e) => setSection({ halfWidth: Math.max(1, Number(e.target.value) || 1) })} aria-label="Полуширина коридора" /> м
            </label>
            {section.length !== null && <button onClick={() => setSection({ length: null })} title="Не ограничивать профиль по длине">На всю длину</button>}
            <button onClick={() => selectedCollar?.x != null && selectedCollar.y != null && setSection({ x0: selectedCollar.x, y0: selectedCollar.y, length: null })}
              disabled={!selectedCollar || selectedCollar.x === null} title="Профиль через выбранную скважину">Через скважину</button>
            <button onClick={() => props.onSection(autoSection())} title="Поперёк простирания устьев, через их центр">Авто</button>
          </>
        )}
        <span className="toolbar-sep" />
        {ws.mode !== "plan" && (
          <label title="Вертикальное преувеличение">
            Верт. ×{" "}
            <select value={ws.vexag} onChange={(e) => props.onWorkspace({ vexag: Number(e.target.value) })}>
              {VEXAG_STEPS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        )}
        <label title="Прозрачность каркасов">
          Каркасы{" "}
          <input type="range" min={0.1} max={1} step={0.05} value={ws.wireframeOpacity}
            onChange={(e) => props.onWorkspace({ wireframeOpacity: Number(e.target.value) })} aria-label="Непрозрачность каркасов" />
        </label>
        <label className="check"><input type="checkbox" checked={ws.grid} onChange={(e) => props.onWorkspace({ grid: e.target.checked })} /> сетка</label>
      </div>
      {component && <div className="scene-legend"><ScaleStrip component={component} unit={unit} scale={scale} log={viz.log} /></div>}

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
          <div className="canvas-note top">{drawing === null ? "Укажите начало профиля" : "Укажите конец профиля"} · Esc — отмена</div>
        )}
        <div className="coords">
          {ws.mode === "3d" && "ЛКМ — вращение, колесо — масштаб, ПКМ — сдвиг · клик по устью — выбор скважины"}
          {ws.mode === "plan" && "Перетаскивание — сдвиг, колесо — масштаб · клик по устью — выбор скважины"}
          {ws.mode === "section" && section && `Разрез: азимут ${Math.round(section.az)}°, коридор ±${section.halfWidth} м · перетаскивание — сдвиг, колесо — масштаб`}
        </div>
      </div>
    </div>
  );
}
