import { ColorScale } from "../viz/colorScale";
import { domainOf, formatNumber, linePath, linearScale, ticksFor, type Domain } from "./chartMath";

export interface Series {
  label: string;
  x: (number | null)[];
  y: (number | null)[];
  /** "line" (default) draws a polyline with points, "points" only points, "dashed" a model curve */
  style?: "line" | "points" | "dashed";
}

const COLORS = ["#5b9dff", "#e8873a", "#5fbf6a", "#c06fd8", "#e0c040", "#46c7c0", "#e05d6f", "#9a8f7a"];
export const seriesColor = (i: number) => COLORS[i % COLORS.length];

const M = { left: 52, right: 12, top: 10, bottom: 34 };

/** A line chart with axes and a legend, drawn in SVG so it follows the theme through CSS variables. */
export function LineChart({
  series,
  xLabel,
  yLabel,
  width = 420,
  height = 220,
  xDomain,
  yDomain,
  hLine,
}: {
  series: Series[];
  xLabel: string;
  yLabel: string;
  width?: number;
  height?: number;
  xDomain?: Domain;
  yDomain?: Domain;
  /** a horizontal reference line, e.g. the sill */
  hLine?: { value: number; label: string };
}) {
  const allX = series.flatMap((s) => s.x);
  const allY = [...series.flatMap((s) => s.y), ...(hLine ? [hLine.value] : [])];
  const dx = xDomain ?? domainOf(allX);
  const dy = yDomain ?? domainOf(allY);
  if (!dx || !dy) return <p className="muted small">Нет данных для графика</p>;
  const sx = linearScale(dx, M.left, width - M.right);
  const sy = linearScale(dy, height - M.bottom, M.top);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`${yLabel} от ${xLabel}`}>
        {ticksFor(dy).map((t) => (
          <g key={`y${t}`}>
            <line className="chart-grid" x1={M.left} x2={width - M.right} y1={sy(t)} y2={sy(t)} />
            <text className="chart-tick" x={M.left - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{formatNumber(t)}</text>
          </g>
        ))}
        {ticksFor(dx).map((t) => (
          <g key={`x${t}`}>
            <line className="chart-grid" x1={sx(t)} x2={sx(t)} y1={M.top} y2={height - M.bottom} />
            <text className="chart-tick" x={sx(t)} y={height - M.bottom + 14} textAnchor="middle">{formatNumber(t)}</text>
          </g>
        ))}
        <rect className="chart-frame" x={M.left} y={M.top} width={width - M.left - M.right} height={height - M.top - M.bottom} />
        {hLine && (
          <g>
            <line className="chart-ref" x1={M.left} x2={width - M.right} y1={sy(hLine.value)} y2={sy(hLine.value)} />
            <text className="chart-tick" x={width - M.right - 2} y={sy(hLine.value) - 4} textAnchor="end">{hLine.label}</text>
          </g>
        )}
        {series.map((s, i) => {
          const color = seriesColor(i);
          const style = s.style ?? "line";
          return (
            <g key={s.label} stroke={color} fill={color}>
              {style !== "points" && <path d={linePath(s.x, s.y, sx, sy)} fill="none" strokeWidth={1.6} strokeDasharray={style === "dashed" ? "5 3" : undefined} />}
              {style !== "dashed" &&
                s.x.map((x, k) => {
                  const y = s.y[k];
                  return x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y) ? null : <circle key={k} cx={sx(x)} cy={sy(y)} r={2.6} stroke="none" />;
                })}
            </g>
          );
        })}
        <text className="chart-axis" x={(M.left + width - M.right) / 2} y={height - 4} textAnchor="middle">{xLabel}</text>
        <text className="chart-axis" transform={`translate(12 ${(M.top + height - M.bottom) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
      </svg>
      {series.length > 1 && (
        <figcaption className="chart-legend">
          {series.map((s, i) => (
            <span key={s.label}><i style={{ background: seriesColor(i) }} />{s.label}</span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}

/** A grid of cells coloured by value; `null` cells stay empty. Row 0 is the bottom (smallest y). */
export function Heatmap({
  x,
  y,
  values,
  scale,
  xLabel,
  yLabel,
  size = 300,
  format = formatNumber,
}: {
  x: number[];
  y: number[];
  values: (number | null)[][];
  scale: ColorScale;
  xLabel: string;
  yLabel: string;
  size?: number;
  /** axis labels and tooltips; coordinates need more digits than the default */
  format?: (value: number) => string;
}) {
  const cols = x.length;
  const rows = y.length;
  const cell = size / Math.max(cols, rows);
  const w = cell * cols;
  const h = cell * rows;
  const ax = 46;
  const ay = 8;
  // every k-th cell counted from the centre (lag 0), so the labels are symmetric and include 0
  const pickTicks = (axis: number[]) => {
    const k = Math.max(1, Math.ceil(axis.length / 7));
    const centre = Math.floor(axis.length / 2);
    return axis.filter((_, i) => (i - centre) % k === 0);
  };
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${ax + w + 10} ${ay + h + 34}`} width={Math.min(ax + w + 10, 460)} role="img" aria-label={`Карта вариограммы: ${yLabel} от ${xLabel}`}>
        {values.map((row, r) =>
          row.map((v, c) =>
            v === null ? null : <rect key={`${r}-${c}`} x={ax + c * cell} y={ay + (rows - 1 - r) * cell} width={cell + 0.4} height={cell + 0.4} fill={scale.css(v)}><title>{`${xLabel}=${format(x[c])}, ${yLabel}=${format(y[r])}: ${formatNumber(v)}`}</title></rect>,
          ),
        )}
        <rect className="chart-frame" x={ax} y={ay} width={w} height={h} />
        {pickTicks(x).map((t) => {
          const c = x.indexOf(t);
          return <text key={`x${t}`} className="chart-tick" x={ax + (c + 0.5) * cell} y={ay + h + 13} textAnchor="middle">{format(t)}</text>;
        })}
        {pickTicks(y).map((t) => {
          const r = y.indexOf(t);
          return <text key={`y${t}`} className="chart-tick" x={ax - 5} y={ay + (rows - 1 - r + 0.5) * cell} textAnchor="end" dominantBaseline="middle">{format(t)}</text>;
        })}
        <text className="chart-axis" x={ax + w / 2} y={ay + h + 30} textAnchor="middle">{xLabel}</text>
        <text className="chart-axis" transform={`translate(10 ${ay + h / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
      </svg>
      <figcaption className="chart-scale">
        <span>{formatNumber(scale.min)}</span>
        <i style={{ background: scale.gradientCss() }} />
        <span>{formatNumber(scale.max)}</span>
      </figcaption>
    </figure>
  );
}

/** Bars of a histogram with vertical marker lines (e.g. the observed value and an interval). */
export function Histogram({
  edges,
  counts,
  marks,
  xLabel,
  width = 420,
  height = 220,
}: {
  edges: number[];
  counts: number[];
  marks: { value: number; label: string; dashed?: boolean }[];
  xLabel: string;
  width?: number;
  height?: number;
}) {
  const dx = domainOf([...edges, ...marks.map((m) => m.value)], 0.02);
  const top = Math.max(1, ...counts);
  if (!dx) return <p className="muted small">Нет данных для графика</p>;
  const sx = linearScale(dx, M.left, width - M.right);
  const sy = linearScale({ min: 0, max: top * 1.08 }, height - M.bottom, M.top);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`Распределение: ${xLabel}`}>
        {ticksFor({ min: 0, max: top }, 4).map((t) => (
          <g key={`y${t}`}>
            <line className="chart-grid" x1={M.left} x2={width - M.right} y1={sy(t)} y2={sy(t)} />
            <text className="chart-tick" x={M.left - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{formatNumber(t)}</text>
          </g>
        ))}
        {ticksFor(dx).map((t) => (
          <text key={`x${t}`} className="chart-tick" x={sx(t)} y={height - M.bottom + 14} textAnchor="middle">{formatNumber(t)}</text>
        ))}
        {counts.map((c, i) => (
          <rect key={i} className="chart-bar" x={sx(edges[i]) + 0.5} y={sy(c)} width={Math.max(0, sx(edges[i + 1]) - sx(edges[i]) - 1)} height={sy(0) - sy(c)}>
            <title>{`${formatNumber(edges[i])} … ${formatNumber(edges[i + 1])}: ${c}`}</title>
          </rect>
        ))}
        <rect className="chart-frame" x={M.left} y={M.top} width={width - M.left - M.right} height={height - M.top - M.bottom} />
        {marks.map((m, i) => (
          <g key={m.label + i}>
            <line className="chart-mark" x1={sx(m.value)} x2={sx(m.value)} y1={M.top} y2={height - M.bottom} strokeDasharray={m.dashed ? "4 3" : undefined} />
            <text className="chart-tick" x={sx(m.value) + 3} y={M.top + 9 + (i % 2) * 11}>{m.label}</text>
          </g>
        ))}
        <text className="chart-axis" x={(M.left + width - M.right) / 2} y={height - 4} textAnchor="middle">{xLabel}</text>
        <text className="chart-axis" transform={`translate(12 ${(M.top + height - M.bottom) / 2}) rotate(-90)`} textAnchor="middle">Реализаций</text>
      </svg>
    </figure>
  );
}
