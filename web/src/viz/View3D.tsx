import { useEffect, useMemo, useRef, useState } from "react";
import type { Hole, IntervalsData, Trajectories } from "../api";
import type { ColorScale } from "./colorScale";
import { DrillScene, type Hover, type Layers } from "./scene3d";

function auditColor(h: Hole | undefined): string {
  if (!h) return "#9aa3b5";
  return h.error ? "#c0392b" : h.warning ? "#e0a030" : h.info ? "#3b82d6" : "#2e9d57";
}

export function View3D({
  trajectories,
  intervals,
  scale,
  holes,
  selectedHole,
  loading,
  error,
  onSelectHole,
}: {
  trajectories: Trajectories | null;
  intervals: IntervalsData | null;
  scale: ColorScale | null;
  holes: Hole[];
  selectedHole: string | null;
  loading: boolean;
  error: string | null;
  onSelectHole: (holeId: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<DrillScene | null>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef(selectedHole);
  selectedRef.current = selectedHole;
  const pickRef = useRef(onSelectHole);
  pickRef.current = onSelectHole;
  const [webglError, setWebglError] = useState<string | null>(null);
  const [vexag, setVexag] = useState(1);
  const [layers, setLayers] = useState<Layers>({ paths: true, intervals: true, collars: true, grid: true });
  const [hover, setHover] = useState<Hover | null>(null);

  const collarColors = useMemo(() => new Map(holes.map((h) => [h.hole_id, auditColor(h)])), [holes]);
  const holeInfo = useMemo(() => new Map(holes.map((h) => [h.hole_id, h])), [holes]);

  // Create the scene once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let scene: DrillScene;
    try {
      scene = new DrillScene(container);
    } catch (e) {
      setWebglError((e as Error).message || "WebGL недоступен");
      return;
    }
    scene.onPick = (id) => pickRef.current(id);
    scene.onHover = setHover;
    scene.onRender = () => {
      const label = labelRef.current;
      const id = selectedRef.current;
      const p = id ? scene.screenPosition(id) : null;
      if (label) {
        label.style.display = p ? "block" : "none";
        if (p) label.style.transform = `translate(${p.x + 10}px, ${p.y - 26}px)`;
      }
    };
    sceneRef.current = scene;
    return () => {
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.setData(trajectories, intervals, scale, collarColors);
    // re-apply view state that setData resets
    sceneRef.current?.setVerticalExaggeration(vexag);
    sceneRef.current?.setLayers(layers);
    sceneRef.current?.setSelected(selectedRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trajectories, intervals, scale, collarColors]);

  useEffect(() => sceneRef.current?.setVerticalExaggeration(vexag), [vexag]);
  useEffect(() => sceneRef.current?.setLayers(layers), [layers]);
  useEffect(() => sceneRef.current?.setSelected(selectedHole), [selectedHole]);

  const toggle = (key: keyof Layers) => setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  const hoverInfo = hover ? holeInfo.get(hover.id) : undefined;

  return (
    <div className="viz">
      <div className="viz-toolbar">
        <label className="check"><input type="checkbox" checked={layers.paths} onChange={() => toggle("paths")} /> стволы</label>
        <label className="check"><input type="checkbox" checked={layers.intervals} onChange={() => toggle("intervals")} /> содержания</label>
        <label className="check"><input type="checkbox" checked={layers.collars} onChange={() => toggle("collars")} /> устья</label>
        <label className="check"><input type="checkbox" checked={layers.grid} onChange={() => toggle("grid")} /> сетка</label>
        <label>
          Верт. масштаб ×{" "}
          <select value={vexag} onChange={(e) => setVexag(Number(e.target.value))}>
            {[1, 2, 3, 5].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <span className="toolbar-sep" />
        <button onClick={() => sceneRef.current?.fit("iso")}>Вписать</button>
        <button onClick={() => sceneRef.current?.fit("top")}>Сверху</button>
        <button onClick={() => sceneRef.current?.fit("side")}>Сбоку</button>
      </div>
      <div className="canvas three" ref={containerRef}>
        <div ref={labelRef} className="label3d" style={{ display: "none" }}>{selectedHole}</div>
        {hover && (
          <div className="tooltip" style={{ left: Math.min(hover.x + 14, (containerRef.current?.clientWidth ?? 400) - 180), top: Math.max(hover.y - 10, 4) }}>
            <strong>{hover.id}</strong>
            {hoverInfo && <div>Глубина: {hoverInfo.td ?? "—"} м</div>}
          </div>
        )}
        {webglError && <div className="canvas-note error">3D недоступно: {webglError}</div>}
        {loading && <div className="canvas-note">Загрузка…</div>}
        {error && <div className="canvas-note error">{error}</div>}
        {!loading && !error && trajectories && trajectories.holes.length === 0 && <div className="canvas-note">Нет устьев с координатами.</div>}
        <div className="coords">ЛКМ — вращение, колесо — масштаб, ПКМ — сдвиг · клик по устью — выбор скважины</div>
      </div>
    </div>
  );
}
