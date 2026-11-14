import { useEffect, useMemo, useState } from "react";
import type { IntervalsData, Trajectories } from "../api";
import type { ColorScale } from "./colorScale";
import { formatValue, NO_VALUE_COLOR } from "./colorScale";
import { defaultSection, inCorridor, niceTicks, toSection, type SectionLine } from "./geometry";
import { useViewport } from "./useViewport";

interface SectionHole {
  id: string;
  /** polyline in section coordinates: [u, elevation] */
  line: [number, number][];
  collar: [number, number];
  intervals: { a: [number, number]; b: [number, number]; value: number; from: number; to: number }[];
}

export function SectionView({
  trajectories,
  intervals,
  scale,
  unit,
  component,
  section,
  selectedHole,
  loading,
  error,
  onSection,
  onSelectHole,
}: {
  trajectories: Trajectories | null;
  intervals: IntervalsData | null;
  scale: ColorScale | null;
  unit: string | null;
  component: string | null;
  section: SectionLine | null;
  selectedHole: string | null;
  loading: boolean;
  error: string | null;
  onSection: (section: SectionLine) => void;
  onSelectHole: (holeId: string | null) => void;
}) {
  const [vexag, setVexag] = useState(1);
  const vp = useViewport(vexag);
  const { fit } = vp;
  const [hover, setHover] = useState<{ x: number; y: number; text: string[] } | null>(null);

  const collars = useMemo<[number, number][]>(
    () => (trajectories?.holes ?? []).map((h) => [h.path[0][0], h.path[0][1]]),
    [trajectories],
  );
  const line = useMemo(() => section ?? defaultSection(collars), [section, collars]);

  const holes = useMemo<SectionHole[]>(() => {
    if (!trajectories) return [];
    const out: SectionHole[] = [];
    for (const h of trajectories.holes) {
      const [x0, y0, z0] = h.path[0];
      if (!inCorridor(x0, y0, line)) continue;
      const rows = intervals?.holes[h.hole_id] ?? [];
      out.push({
        id: h.hole_id,
        line: h.path.map((p) => [toSection(p[0], p[1], line).u, p[2]]),
        collar: [toSection(x0, y0, line).u, z0],
        intervals: rows.map((r) => ({
          from: r[0], to: r[1], value: r[2],
          a: [toSection(r[3], r[4], line).u, r[5]],
          b: [toSection(r[6], r[7], line).u, r[8]],
        })),
      });
    }
    return out;
  }, [trajectories, intervals, line]);

  const bounds = useMemo(() => {
    const pts = holes.flatMap((h) => h.line);
    if (pts.length === 0) return null;
    return {
      minX: Math.min(...pts.map((p) => p[0])), maxX: Math.max(...pts.map((p) => p[0])),
      minY: Math.min(...pts.map((p) => p[1])), maxY: Math.max(...pts.map((p) => p[1])),
    };
  }, [holes]);

  // Re-fit when the section (or the exaggeration) changes, not on hover or selection.
  const boundsKey = bounds ? `${bounds.minX}|${bounds.maxX}|${bounds.minY}|${bounds.maxY}|${vexag}` : "";
  useEffect(() => {
    if (bounds) fit(bounds, 0.1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundsKey, fit]);

  const { toScreen, toWorld, size, view } = vp;
  const [wx0, wy1] = toWorld(0, 0);
  const [wx1, wy0] = toWorld(size.w, size.h);
  const xTicks = niceTicks(wx0, wx1, Math.max(Math.floor(size.w / 110), 2));
  const yTicks = niceTicks(wy0, wy1, Math.max(Math.floor(size.h / 70), 2));
  const set = (patch: Partial<SectionLine>) => onSection({ ...line, ...patch });
  const total = trajectories?.holes.length ?? 0;
  const selected = selectedHole ? trajectories?.holes.find((h) => h.hole_id === selectedHole) : undefined;

  return (
    <div className="viz">
      <div className="viz-toolbar">
        <label>
          Азимут профиля{" "}
          <input className="range-input" type="number" min={0} max={360} step={5} value={Math.round(line.az * 10) / 10}
                 onChange={(e) => set({ az: ((Number(e.target.value) % 360) + 360) % 360 })} aria-label="Азимут профиля" />°
        </label>
        <label>
          Ширина коридора ±{" "}
          <input className="range-input" type="number" min={1} step={5} value={line.halfWidth}
                 onChange={(e) => set({ halfWidth: Math.max(1, Number(e.target.value) || 1) })} aria-label="Полуширина коридора" /> м
        </label>
        <label>
          Верт. масштаб ×{" "}
          <select value={vexag} onChange={(e) => setVexag(Number(e.target.value))}>
            {[1, 2, 3, 5].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        {selected && (
          <button onClick={() => set({ x0: selected.path[0][0], y0: selected.path[0][1], length: null })} title="Профиль через выбранную скважину">
            Через {selected.hole_id}
          </button>
        )}
        <button onClick={() => onSection(defaultSection(collars, line.halfWidth))} title="Поперёк простирания устьев">Авто</button>
        <button onClick={() => bounds && fit(bounds, 0.1)} disabled={!bounds}>Вписать</button>
      </div>

      <div ref={vp.ref} className="canvas" onMouseDown={vp.onMouseDown}>
        <svg width={size.w} height={size.h} onClick={() => !vp.dragged.current && onSelectHole(null)} role="img" aria-label="Вертикальный разрез">
          {xTicks.map((t) => {
            const x = toScreen(t, 0)[0];
            return <g key={`x${t}`}><line x1={x} x2={x} y1={0} y2={size.h} className="grid" /><text x={x + 3} y={12} className="axis-label">{t.toFixed(0)}</text></g>;
          })}
          {yTicks.map((t) => {
            const y = toScreen(0, t)[1];
            return <g key={`y${t}`}><line x1={0} x2={size.w} y1={y} y2={y} className="grid" /><text x={4} y={y - 3} className="axis-label">{t.toFixed(0)}</text></g>;
          })}

          {holes.map((h) => {
            const isSelected = h.id === selectedHole;
            return (
              <g key={h.id} className="collar" onClick={(e) => { if (!vp.dragged.current) { e.stopPropagation(); onSelectHole(isSelected ? null : h.id); } }}>
                <polyline points={h.line.map(([u, z]) => toScreen(u, z).join(",")).join(" ")} className={isSelected ? "trace selected" : "trace"} />
                {h.intervals.map((iv, i) => {
                  const [x1, y1] = toScreen(...iv.a);
                  const [x2, y2] = toScreen(...iv.b);
                  return (
                    <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="interval" style={{ stroke: scale ? scale.css(iv.value) : NO_VALUE_COLOR }}
                          onMouseEnter={() => setHover({ x: (x1 + x2) / 2, y: (y1 + y2) / 2, text: [`${h.id}  ${iv.from}–${iv.to} м`, `${component ?? ""}: ${formatValue(iv.value)} ${unit ?? ""}`] })}
                          onMouseLeave={() => setHover(null)} />
                  );
                })}
                <text x={toScreen(...h.collar)[0]} y={toScreen(...h.collar)[1] - 8} textAnchor="middle" className={isSelected ? "map-label strong" : "map-label"}>{h.id}</text>
              </g>
            );
          })}
        </svg>
        {hover && <div className="tooltip" style={{ left: Math.min(hover.x + 12, size.w - 190), top: Math.max(hover.y - 12, 4) }}>{hover.text.map((t) => <div key={t}>{t}</div>)}</div>}
        {loading && <div className="canvas-note">Загрузка…</div>}
        {error && <div className="canvas-note error">{error}</div>}
        {!loading && !error && total > 0 && holes.length === 0 && (
          <div className="canvas-note">В коридоре нет скважин. Увеличьте ширину, поменяйте азимут или нажмите «Авто».</div>
        )}
        <div className="coords">
          Скважин в коридоре: {holes.length} из {total} · по горизонтали — расстояние вдоль профиля, м; по вертикали — отметка Z, м · масштаб {view.scale.toFixed(2)} px/м
        </div>
      </div>
    </div>
  );
}
