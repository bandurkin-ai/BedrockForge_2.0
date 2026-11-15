import { useRef, useState } from "react";
import { axisPosition, axisValue, cumulative, type Histogram } from "./histogram";
import { showNum } from "./ui";

const W = 360;
const H = 96;
const PAD = 8;

/**
 * The distribution of a field with bounds that are dragged with the mouse or moved with the arrow keys.
 * A double click adds a bound, Delete removes the focused one (when the editor allows it).
 */
export function HistogramPlot({ hist, colorAt, handles, onMove, onAdd, onRemove, showCumulative, strip, label }: {
  hist: Histogram;
  /** colour of a bin by the value at its middle */
  colorAt: (v: number) => string;
  handles: number[];
  /** a bound moved; `done` is false while it is being dragged */
  onMove: (index: number, value: number) => void;
  onAdd?: (value: number) => void;
  onRemove?: (index: number) => void;
  showCumulative: boolean;
  /** css background of a strip under the plot (the gradient of a continuous scale) */
  strip?: string;
  label: string;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const top = Math.max(...hist.counts, 1e-9);
  const bins = hist.counts.length;
  const x = (p: number) => PAD + p * (W - 2 * PAD);
  const valueAt = (clientX: number) => {
    const r = svg.current?.getBoundingClientRect();
    if (!r || r.width === 0) return hist.min;
    const p = ((clientX - r.left) / r.width) * W;
    return Number(axisValue(hist, (p - PAD) / (W - 2 * PAD)).toPrecision(3));
  };
  const cum = showCumulative ? cumulative(hist) : null;
  const step = (i: number, dir: number, big: boolean) => {
    const p = axisPosition(hist, handles[i]) + dir * (big ? 0.05 : 0.01);
    onMove(i, Number(axisValue(hist, p).toPrecision(3)));
  };
  return (
    <div className="dh-hist">
      <svg
        ref={svg} viewBox={`0 0 ${W} ${H + 14}`} role="img" aria-label={label} preserveAspectRatio="none"
        onDoubleClick={(e) => onAdd?.(valueAt(e.clientX))}
        onPointerMove={(e) => drag !== null && onMove(drag, valueAt(e.clientX))}
        onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}
      >
        {hist.counts.map((c, i) => {
          const h = (c / top) * (H - 6);
          const mid = hist.log ? Math.sqrt(hist.edges[i] * hist.edges[i + 1]) : (hist.edges[i] + hist.edges[i + 1]) / 2;
          return <rect key={i} x={x(i / bins)} y={H - h} width={Math.max(0.5, (W - 2 * PAD) / bins - 0.6)} height={h} fill={colorAt(mid)} />;
        })}
        {cum && <polyline className="dh-hist-cum" fill="none" points={cum.map((s, i) => `${x((i + 1) / bins)},${H - s * (H - 6)}`).join(" ")} />}
        <line x1={PAD} x2={W - PAD} y1={H} y2={H} className="dh-hist-axis" />
        <text x={PAD} y={H + 11} className="dh-hist-tick">{showNum(hist.min, 3)}</text>
        <text x={W - PAD} y={H + 11} textAnchor="end" className="dh-hist-tick">{showNum(hist.max, 3)}</text>
        {handles.map((v, i) => {
          const px = x(axisPosition(hist, v));
          return (
            <g
              key={i} className={`dh-hist-handle${drag === i ? " drag" : ""}`} tabIndex={0} role="slider" aria-label={`Граница ${i + 1}`}
              aria-valuenow={v} aria-valuemin={hist.min} aria-valuemax={hist.max} aria-valuetext={showNum(v)}
              onPointerDown={(e) => {
                e.preventDefault();
                (e.currentTarget as SVGGElement).focus();
                setDrag(i);
              }}
              onDoubleClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  e.preventDefault();
                  step(i, e.key === "ArrowLeft" ? -1 : 1, e.shiftKey);
                } else if ((e.key === "Delete" || e.key === "Backspace") && onRemove) {
                  e.preventDefault();
                  onRemove(i);
                }
              }}
            >
              <line x1={px} x2={px} y1={2} y2={H} />
              <rect x={px - 5} y={H - 3} width={10} height={9} rx={2} />
              <rect x={px - 7} y={0} width={14} height={H + 8} fill="transparent" stroke="none" />
              <title>{showNum(v)} — тяните мышью или двигайте стрелками{onRemove ? "; Delete удаляет" : ""}</title>
            </g>
          );
        })}
      </svg>
      {strip && <div className="dh-hist-strip" style={{ background: strip }} />}
    </div>
  );
}
