import { forwardRef } from "react";
import type { DownholeData, DownholeInterval } from "../api";
import { ColorScale } from "../viz/colorScale";
import { formatNumber, parseNumber } from "../stats/chartMath";
import { along, categoryColors, depthTicks, fieldRange, type StripLogSettings, type Track } from "./stripLog";

const AXIS = 46;
const GAP = 26;
const HEAD = 58;
const PAD = 10;

interface Prepared {
  track: Track;
  title: string;
  unit: string;
  numeric: boolean;
  range: [number, number] | null;
  colors: Map<string, string> | null;
  scale: ColorScale | null;
}

function prepare(data: DownholeData, t: Track): Prepared {
  const table = data.tables.find((x) => x.key === t.table);
  const column = table?.columns.find((c) => c.name === t.field);
  const numeric = column?.kind === "number";
  const auto = numeric ? fieldRange(data, t.table, t.field, t.log) : null;
  const lo = parseNumber(t.min) ?? auto?.[0] ?? null;
  const hi = parseNumber(t.max) ?? auto?.[1] ?? null;
  const range: [number, number] | null = lo !== null && hi !== null && hi > lo ? [lo, hi] : auto;
  const wantsColor = t.type === "color" || t.type === "color_text";
  return {
    track: t,
    title: `${t.field}${table && table.key !== "assay" ? ` · ${table.name}` : ""}`,
    unit: column?.unit ?? "",
    numeric,
    range,
    colors: !numeric && wantsColor ? categoryColors(data, t.table, t.field) : null,
    scale: numeric && wantsColor && range ? new ColorScale(range[0], range[1], t.log) : null,
  };
}

const valueText = (v: unknown) => (typeof v === "number" ? formatNumber(v, 3) : v === null || v === undefined ? "" : String(v));

/** The downhole log: per hole a depth axis and the columns of the settings. */
export const StripLogChart = forwardRef<SVGSVGElement, { data: DownholeData; settings: StripLogSettings; fitHeight: number }>(
  function StripLogChart({ data, settings: s, fitHeight }, ref) {
    const tracks = s.tracks.map((t) => prepare(data, t));
    const from = parseNumber(s.from);
    const to = parseNumber(s.to);
    const spans = data.holes.map((h) => {
      const top = from ?? 0;
      const bottom = to ?? Math.max(h.max_depth, top + 1);
      return { top, bottom: Math.max(bottom, top + 0.1) };
    });
    const commonTop = Math.min(...spans.map((x) => x.top));
    const commonBottom = Math.max(...spans.map((x) => x.bottom));
    const longest = s.grid ? commonBottom - commonTop : Math.max(...spans.map((x) => x.bottom - x.top));
    const pxPerM = s.scale > 0 ? s.scale : Math.max(0.05, (fitHeight - HEAD - 2 * PAD - 70) / Math.max(longest, 1e-6));
    const trackWidth = tracks.reduce((w, t) => w + t.track.width, 0);
    const holeWidth = AXIS + Math.max(trackWidth, 60);
    const legends = tracks.filter((t) => t.colors && t.colors.size > 0);
    const legendRows = legends.reduce((n, t) => n + 1 + Math.ceil(Math.min(t.colors!.size, 40) / 4), 0);
    const W = PAD * 2 + data.holes.length * holeWidth + Math.max(0, data.holes.length - 1) * GAP;
    const bodyH = longest * pxPerM;
    const H = PAD * 2 + HEAD + bodyH + (legendRows ? 16 + legendRows * 16 : 0);

    return (
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="hist-chart strip-chart" role="img" aria-label="Данные по стволу">
        <rect x={0} y={0} width={W} height={H} className="chart-bg" />
        <defs>
          {data.holes.map((_, hi) =>
            tracks.map((_t, ti) => {
              const x0 = PAD + hi * (holeWidth + GAP) + AXIS + tracks.slice(0, ti).reduce((w, t) => w + t.track.width, 0);
              return <clipPath key={`c${hi}-${ti}`} id={`strip-clip-${hi}-${ti}`}><rect x={x0} y={PAD + HEAD} width={tracks[ti].track.width} height={bodyH} /></clipPath>;
            }),
          )}
        </defs>
        {data.holes.map((h, hi) => {
          const span = s.grid ? { top: commonTop, bottom: commonBottom } : spans[hi];
          const own = spans[hi];
          const y = (d: number) => PAD + HEAD + (d - span.top) * pxPerM;
          const hx = PAD + hi * (holeWidth + GAP);
          const ticks = depthTicks(span.top, span.bottom, pxPerM);
          const end = Math.min(own.bottom, h.td ?? own.bottom);
          return (
            <g key={h.hole_id}>
              <text className="chart-axis strip-hole" x={hx + AXIS} y={PAD + 13}>{h.hole_id}{h.found ? "" : " — нет устья"}</text>
              <text className="chart-tick" x={hx + AXIS} y={PAD + 26}>{h.td !== null ? `глубина ${formatNumber(h.td, 2)} м` : ""}</text>
              {ticks.map((d) => (
                <g key={d}>
                  <line className="chart-grid" x1={hx + AXIS - 4} x2={hx + AXIS + trackWidth} y1={y(d)} y2={y(d)} />
                  <text className="chart-tick" x={hx + AXIS - 6} y={y(d) + 4} textAnchor="end">{formatNumber(d)}</text>
                </g>
              ))}
              {/* the hole itself, down to its depth */}
              <line className="strip-hole-line" x1={hx + AXIS - 2} x2={hx + AXIS - 2} y1={y(Math.max(span.top, own.top))} y2={y(Math.min(end, span.bottom))} />
              {tracks.map((p, ti) => {
                const tx = hx + AXIS + tracks.slice(0, ti).reduce((w, t) => w + t.track.width, 0);
                const w = p.track.width;
                const rows = (h.tables[p.track.table] ?? []).filter((iv) => iv.from !== null && iv.to !== null && iv.to! > span.top && iv.from! < span.bottom);
                return (
                  <g key={p.track.id}>
                    <text className="chart-tick strip-head" x={tx + 3} y={PAD + 40}>{p.title.length > w / 6 ? `${p.title.slice(0, Math.max(3, Math.floor(w / 6) - 1))}…` : p.title}<title>{p.title}</title></text>
                    {p.numeric && p.range && (
                      <>
                        <text className="chart-tick" x={tx + 3} y={PAD + 53}>{formatNumber(p.range[0], 3)}</text>
                        <text className="chart-tick" x={tx + w - 5} y={PAD + 53} textAnchor="end">{formatNumber(p.range[1], 3)}{p.unit && w >= 80 ? ` ${p.unit}` : ""}</text>
                      </>
                    )}
                    <g clipPath={`url(#strip-clip-${hi}-${ti})`}>
                      <TrackBody prepared={p} rows={rows} x={tx} w={w} y={y} hole={h.hole_id} />
                    </g>
                    <rect className="chart-frame" x={tx} y={PAD + HEAD} width={w} height={bodyH} fill="none" />
                  </g>
                );
              })}
            </g>
          );
        })}
        {legends.length > 0 && (() => {
          let row = 0;
          const top = PAD + HEAD + bodyH + 16;
          return legends.map((p) => {
            const items = [...p.colors!].slice(0, 40);
            const head = row++;
            const out = (
              <g key={p.track.id}>
                <text className="chart-axis" x={PAD} y={top + head * 16 + 10}>{p.title}</text>
                {items.map(([label, color], i) => {
                  const r = head + 1 + Math.floor(i / 4);
                  const x = PAD + 12 + (i % 4) * 150;
                  return (
                    <g key={label}>
                      <rect x={x} y={top + r * 16} width={12} height={11} fill={color} className="strip-swatch" />
                      <text className="chart-tick" x={x + 16} y={top + r * 16 + 10}>{label.length > 20 ? `${label.slice(0, 19)}…` : label}</text>
                    </g>
                  );
                })}
              </g>
            );
            row += Math.ceil(items.length / 4);
            return out;
          });
        })()}
      </svg>
    );
  },
);

function TrackBody({ prepared: p, rows, x, w, y, hole }: { prepared: Prepared; rows: DownholeInterval[]; x: number; w: number; y: (d: number) => number; hole: string }) {
  const t = p.track;
  const tip = (iv: DownholeInterval) => `${hole} ${formatNumber(iv.from!, 2)}–${formatNumber(iv.to!, 2)} м: ${t.field} = ${valueText(iv.values[t.field]) || "—"}`;
  const pos = (v: number) => (p.range ? along(v, p.range[0], p.range[1], t.log) : 0);
  const out: JSX.Element[] = [];
  const showColor = t.type === "color" || t.type === "color_text";
  const showText = t.type === "text" || t.type === "color_text";
  if (showColor) {
    rows.forEach((iv, i) => {
      const v = iv.values[t.field];
      let fill: string | null = null;
      if (p.colors && v !== null && v !== undefined && v !== "") fill = p.colors.get(String(v)) ?? null;
      if (p.scale && typeof v === "number") fill = p.scale.css(v);
      if (fill) out.push(<rect key={`c${i}`} x={x} y={y(iv.from!)} width={w} height={Math.max(0.5, y(iv.to!) - y(iv.from!))} fill={fill}><title>{tip(iv)}</title></rect>);
    });
  }
  if (t.type === "bars" && p.numeric) {
    rows.forEach((iv, i) => {
      const v = iv.values[t.field];
      if (typeof v !== "number" || !Number.isFinite(v)) return;
      out.push(<rect key={`b${i}`} className="strip-bar" x={x} y={y(iv.from!)} width={Math.max(0.5, pos(v) * w)} height={Math.max(0.5, y(iv.to!) - y(iv.from!))} fill={t.color}><title>{tip(iv)}</title></rect>);
    });
  }
  if (t.type === "line" && p.numeric) {
    // A stepped line; a gap between intervals breaks it.
    const parts: string[] = [];
    let path = "";
    let lastTo: number | null = null;
    for (const iv of rows) {
      const v = iv.values[t.field];
      if (typeof v !== "number" || !Number.isFinite(v)) {
        lastTo = null;
        continue;
      }
      const vx = (x + pos(v) * w).toFixed(1);
      if (lastTo === null || iv.from! - lastTo > 1e-6) {
        if (path) parts.push(path);
        path = `M${vx} ${y(iv.from!).toFixed(1)}`;
      } else {
        path += `H${vx}`;
      }
      path += `V${y(iv.to!).toFixed(1)}`;
      lastTo = iv.to!;
    }
    if (path) parts.push(path);
    out.push(<path key="line" d={parts.join("")} fill="none" stroke={t.color} strokeWidth={1.5} />);
    rows.forEach((iv, i) => {
      if (typeof iv.values[t.field] === "number") out.push(<rect key={`h${i}`} x={x} y={y(iv.from!)} width={w} height={Math.max(0.5, y(iv.to!) - y(iv.from!))} fill="transparent"><title>{tip(iv)}</title></rect>);
    });
  }
  if (t.boundaries) {
    rows.forEach((iv, i) => out.push(<line key={`l${i}`} className="strip-boundary" x1={x} x2={x + w} y1={y(iv.from!)} y2={y(iv.from!)} />));
  }
  if (showText) {
    rows.forEach((iv, i) => {
      const h = y(iv.to!) - y(iv.from!);
      const text = valueText(iv.values[t.field]);
      if (h < 10 || !text) return;
      out.push(
        <text key={`t${i}`} className="strip-text" x={x + 3} y={(y(iv.from!) + y(iv.to!)) / 2 + 4}>
          {text}
          {!showColor && <title>{tip(iv)}</title>}
        </text>,
      );
    });
  }
  return <>{out}</>;
}
