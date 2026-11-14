import { useEffect, useMemo, useState } from "react";
import type { Hole } from "../api";
import { ColorScale } from "./colorScale";
import { azimuthDeg, niceStep, type SectionLine } from "./geometry";
import { useTrajectories } from "./useVizData";
import { useViewport } from "./useViewport";

type ColorMode = "audit" | "status" | "td";

const STATUS_COLORS = ["#2e9d57", "#2b5fa8", "#b9770e", "#8e44ad", "#16a085", "#c0392b", "#7f8c8d"];

export function PlanView({
  projectId,
  refreshKey,
  holes,
  selectedHole,
  section,
  onSelectHole,
  onSection,
  onOpenSection,
}: {
  projectId: number;
  refreshKey: number;
  holes: Hole[];
  selectedHole: string | null;
  section: SectionLine | null;
  onSelectHole: (holeId: string | null) => void;
  onSection: (section: SectionLine | null) => void;
  onOpenSection: () => void;
}) {
  const { data, loading, error } = useTrajectories(projectId, refreshKey);
  const vp = useViewport();
  const { fit } = vp;
  const [mode, setMode] = useState<ColorMode>("audit");
  const [showPaths, setShowPaths] = useState(true);
  const [showLabels, setShowLabels] = useState(false);
  const [drawing, setDrawing] = useState<{ x: number; y: number } | null | false>(false);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  const holeInfo = useMemo(() => new Map(holes.map((h) => [h.hole_id, h])), [holes]);
  const bounds = useMemo(() => {
    if (!data?.extent) return null;
    return { minX: data.extent.min[0], maxX: data.extent.max[0], minY: data.extent.min[1], maxY: data.extent.max[1] };
  }, [data]);

  // Fit once when data arrives, not on every re-render.
  useEffect(() => {
    if (bounds) fit(bounds);
  }, [bounds, fit]);

  useEffect(() => {
    if (drawing === false) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawing(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing]);

  const statuses = useMemo(
    () => [...new Set(holes.map((h) => h.status || "(пусто)"))].sort(),
    [holes],
  );
  const tdScale = useMemo(() => {
    const values = holes.map((h) => h.td).filter((v): v is number => v !== null);
    return values.length > 1 ? new ColorScale(Math.min(...values), Math.max(...values), false) : null;
  }, [holes]);

  function collarColor(holeId: string): string {
    const h = holeInfo.get(holeId);
    if (mode === "audit") {
      if (!h) return "var(--muted)";
      return h.error ? "var(--sev-error)" : h.warning ? "var(--sev-warning)" : h.info ? "var(--sev-info)" : "#2e9d57";
    }
    if (mode === "status") return STATUS_COLORS[statuses.indexOf(h?.status || "(пусто)") % STATUS_COLORS.length];
    return tdScale && h?.td != null ? tdScale.css(h.td) : "var(--muted)";
  }

  function onBackgroundClick(e: React.MouseEvent) {
    if (vp.dragged.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const [x, y] = vp.toWorld(e.clientX - rect.left, e.clientY - rect.top);
    if (drawing === false) {
      onSelectHole(null);
    } else if (drawing === null) {
      setDrawing({ x, y });
    } else {
      const dx = x - drawing.x;
      const dy = y - drawing.y;
      const length = Math.hypot(dx, dy);
      if (length > 1) {
        onSection({ x0: drawing.x, y0: drawing.y, az: azimuthDeg(dx, dy), length, halfWidth: section?.halfWidth ?? 25 });
        setDrawing(false);
      }
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    const rect = e.currentTarget.getBoundingClientRect();
    setCursor(vp.toWorld(e.clientX - rect.left, e.clientY - rect.top));
  }

  const { toScreen, size, view } = vp;
  const barMeters = niceStep(110 / view.scale, 3);
  const sectionGeometry = section && sectionCorners(section, bounds);

  return (
    <div className="viz">
      <div className="viz-toolbar">
        <label>
          Цвет устий{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as ColorMode)}>
            <option value="audit">по замечаниям аудита</option>
            <option value="status">по статусу</option>
            <option value="td">по глубине</option>
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} /> стволы</label>
        <label className="check"><input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /> подписи</label>
        <button onClick={() => bounds && fit(bounds)} disabled={!bounds}>Вписать</button>
        <span className="toolbar-sep" />
        <button className={drawing === false ? "" : "active"} onClick={() => setDrawing(drawing === false ? null : false)} disabled={!bounds}>
          {drawing === false ? "Линия разреза" : drawing === null ? "Отметьте начало линии…" : "Отметьте конец линии…"}
        </button>
        {section && <button onClick={onOpenSection}>Открыть разрез →</button>}
        {section && <button onClick={() => onSection(null)} title="Удалить линию разреза">✕ линия</button>}
      </div>

      <div
        ref={vp.ref}
        className={`canvas ${drawing === false ? "" : "crosshair"}`}
        onMouseDown={vp.onMouseDown}
        onMouseMove={onMouseMove}
        onMouseLeave={() => setCursor(null)}
      >
        <svg width={size.w} height={size.h} onClick={onBackgroundClick} role="img" aria-label="План скважин">
          {sectionGeometry && (
            <g>
              <polygon points={sectionGeometry.corners.map(([x, y]) => toScreen(x, y).join(",")).join(" ")} className="corridor" />
              <line {...lineProps(toScreen, sectionGeometry.start, sectionGeometry.end)} className="section-line" />
              <text x={toScreen(...sectionGeometry.start)[0] + 6} y={toScreen(...sectionGeometry.start)[1] - 6} className="map-label">A</text>
              <text x={toScreen(...sectionGeometry.end)[0] + 6} y={toScreen(...sectionGeometry.end)[1] - 6} className="map-label">B</text>
            </g>
          )}
          {showPaths && data?.holes.map((h) => (
            <polyline
              key={h.hole_id}
              points={h.path.map((p) => toScreen(p[0], p[1]).join(",")).join(" ")}
              className={h.hole_id === selectedHole ? "trace selected" : "trace"}
            />
          ))}
          {data?.holes.map((h) => {
            const [sx, sy] = toScreen(h.path[0][0], h.path[0][1]);
            const selected = h.hole_id === selectedHole;
            return (
              <g key={h.hole_id} onClick={(e) => { if (!vp.dragged.current && drawing === false) { e.stopPropagation(); onSelectHole(selected ? null : h.hole_id); } }}
                 onMouseEnter={() => setHover(h.hole_id)} onMouseLeave={() => setHover(null)} className="collar">
                {selected && <circle cx={sx} cy={sy} r={10} className="ring" />}
                <circle cx={sx} cy={sy} r={selected ? 6 : 4.5} style={{ fill: collarColor(h.hole_id) }} className="collar-dot" />
                {(showLabels || selected) && <text x={sx + 8} y={sy - 6} className="map-label">{h.hole_id}</text>}
              </g>
            );
          })}
          {drawing && cursor && (
            <line {...lineProps(toScreen, [drawing.x, drawing.y], cursor)} className="section-line draft" />
          )}
          <g transform={`translate(16, ${size.h - 28})`} className="scalebar">
            <line x1={0} y1={0} x2={barMeters * view.scale} y2={0} />
            <line x1={0} y1={-4} x2={0} y2={4} />
            <line x1={barMeters * view.scale} y1={-4} x2={barMeters * view.scale} y2={4} />
            <text x={0} y={-8}>{barMeters >= 1000 ? `${barMeters / 1000} км` : `${barMeters} м`}</text>
          </g>
          <g transform={`translate(${size.w - 36}, 16)`} className="north">
            <polygon points="0,0 8,26 0,20 -8,26" />
            <text x={0} y={42} textAnchor="middle">С</text>
          </g>
        </svg>
        {hover && holeInfo.get(hover) && (
          <div className="tooltip" style={tooltipPosition(toScreen, data?.holes.find((h) => h.hole_id === hover)?.path[0], size)}>
            <strong>{hover}</strong>
            <div>Глубина: {holeInfo.get(hover)!.td ?? "—"} м</div>
            <div>Статус: {holeInfo.get(hover)!.status || "—"}</div>
            {(holeInfo.get(hover)!.error + holeInfo.get(hover)!.warning + holeInfo.get(hover)!.info) > 0 && (
              <div>Замечаний аудита: {holeInfo.get(hover)!.error + holeInfo.get(hover)!.warning + holeInfo.get(hover)!.info}</div>
            )}
          </div>
        )}
        {loading && <div className="canvas-note">Загрузка…</div>}
        {error && <div className="canvas-note error">{error}</div>}
        {!loading && data && data.holes.length === 0 && <div className="canvas-note">Нет устьев с координатами. Импортируйте Collar.</div>}
        <div className="coords">
          {cursor ? `X ${cursor[0].toFixed(1)}  Y ${cursor[1].toFixed(1)}` : "Колесо — масштаб, перетаскивание — сдвиг"}
        </div>
        <div className="map-legend">{legendItems(mode, statuses, tdScale)}</div>
      </div>
      {data && data.skipped.length > 0 && (
        <p className="muted small viz-note">Не показаны устья без координат: {data.skipped.length}.</p>
      )}
    </div>
  );
}

function lineProps(toScreen: (x: number, y: number) => [number, number], a: [number, number] | { x: number; y: number } | number[], b: [number, number] | number[]) {
  const pa = Array.isArray(a) ? a : [a.x, a.y];
  const [x1, y1] = toScreen(pa[0], pa[1]);
  const [x2, y2] = toScreen(b[0], b[1]);
  return { x1, y1, x2, y2 };
}

function sectionCorners(section: SectionLine, bounds: { minX: number; maxX: number; minY: number; maxY: number } | null) {
  const a = (section.az * Math.PI) / 180;
  const dir: [number, number] = [Math.sin(a), Math.cos(a)];
  const right: [number, number] = [Math.cos(a), -Math.sin(a)];
  const length = section.length ?? (bounds ? Math.hypot(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY) : 500);
  const start: [number, number] = section.length === null ? [section.x0 - dir[0] * length / 2, section.y0 - dir[1] * length / 2] : [section.x0, section.y0];
  const end: [number, number] = [start[0] + dir[0] * length, start[1] + dir[1] * length];
  const w = section.halfWidth;
  const corners: [number, number][] = [
    [start[0] - right[0] * w, start[1] - right[1] * w],
    [end[0] - right[0] * w, end[1] - right[1] * w],
    [end[0] + right[0] * w, end[1] + right[1] * w],
    [start[0] + right[0] * w, start[1] + right[1] * w],
  ];
  return { start, end, corners };
}

function tooltipPosition(toScreen: (x: number, y: number) => [number, number], p: number[] | undefined, size: { w: number; h: number }) {
  if (!p) return {};
  const [x, y] = toScreen(p[0], p[1]);
  return { left: Math.min(x + 14, size.w - 180), top: Math.max(y - 10, 4) };
}

function legendItems(mode: ColorMode, statuses: string[], tdScale: ColorScale | null) {
  if (mode === "audit") {
    return [
      ["var(--sev-error)", "критично"], ["var(--sev-warning)", "предупреждения"], ["var(--sev-info)", "информация"], ["#2e9d57", "без замечаний"],
    ].map(([color, label]) => <span key={label}><i style={{ background: color }} />{label}</span>);
  }
  if (mode === "status") {
    return statuses.map((s, i) => <span key={s}><i style={{ background: STATUS_COLORS[i % STATUS_COLORS.length] }} />{s}</span>);
  }
  return tdScale ? <span><i className="gradient" style={{ background: tdScale.gradientCss() }} />{tdScale.min.toFixed(0)} – {tdScale.max.toFixed(0)} м</span> : null;
}
