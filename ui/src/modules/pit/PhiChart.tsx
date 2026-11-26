/**
 * The chart of the check of the template (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 9–12): the effective slope angle φ_эфф(az) of every domain at 360 azimuths (degrees from the north,
 * clockwise, in the axes of the project) against the angles of its table — a mark per sector with the whisker of the tolerance. A point with no value breaks the line (a gap is not
 * 0). SVG of the shared frame (`charts/core`), the brand colours (docs/brand: domains start from Cobalt and Copper Orange), a tooltip of the azimuth under the cursor.
 */
import { forwardRef, useMemo, useState, type PointerEvent as RPointerEvent } from "react";
import { XAxis, YAxis } from "../../charts/core/Axes";
import { domainOf, formatNumber, makeScale, type Tick } from "../../charts/core/axisScale";
import css from "../../charts/core/Chart.module.css";
import { seriesColor } from "../../charts/core/palette";
import { plotBox, PlotFrame } from "../../charts/core/PlotFrame";
import { pathOf, pieces } from "../../charts/gradetonnage/model";
import { domainTitle, nearestAz, phiRange, type CheckResult } from "./checkModel";

export const PHI_W = 640;
export const PHI_H = 300;

/** The azimuths marked on the axis: every 45° (the eight directions of the compass). */
export const AZ_TICKS: Tick[] = Array.from({ length: 9 }, (_, i) => ({ value: i * 45, label: `${i * 45}°` }));

/** The most lines of the tooltip: the first domains, then «…ещё N». */
const TIP_DOMAINS = 5;

export interface PhiChartProps {
  result: CheckResult;
  width?: number;
  height?: number;
}

export const PhiChart = forwardRef<SVGSVGElement, PhiChartProps>(function PhiChart({ result, width = PHI_W, height = PHI_H }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useMemo(() => plotBox(width, height, { left: 56, right: 16, top: 14, bottom: 46 }), [width, height]);
  const range = phiRange(result.domains);
  const label = "Эффективный угол борта по азимутам";
  const xTitle = "Азимут, ° (от севера по часовой стрелке)";
  const yTitle = "Угол борта, °";
  const dom = range ? domainOf([range.min, range.max], "linear", 0) : null;
  if (!dom) {
    return (
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle}>
        <text className={css.tick} data-empty="phi" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          График не построен: в ответе нет эффективного угла
        </text>
      </PlotFrame>
    );
  }
  const sx = makeScale("linear", { min: 0, max: 360 }, box.x0, box.x1);
  const sy = makeScale("linear", dom, box.y1, box.y0);
  const mx = (v: number) => sx.map(v);
  const my = (v: number) => sy.map(v);
  const first = result.domains.find((d) => d.az.length) ?? null;
  const pick = (e: RPointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * width;
    setHover(first === null || px < box.x0 || px > box.x1 ? null : nearestAz(first.az, sx.invert(px)));
  };
  const tipAz = hover !== null && first ? first.az[hover] : null;
  const tipX = tipAz === null ? 0 : (sx.map(tipAz) ?? 0);
  const tipLines =
    hover === null || tipAz === null
      ? []
      : [
          `Азимут ${tipAz}°`,
          ...result.domains.slice(0, TIP_DOMAINS).map((d) => `${domainTitle(d)}: ${d.phi[hover] === null || d.phi[hover] === undefined ? "нет значения" : `${formatNumber(d.phi[hover], 4)}°`}`),
          ...(result.domains.length > TIP_DOMAINS ? [`…ещё доменов: ${result.domains.length - TIP_DOMAINS}`] : []),
        ];
  const tol = result.tolerance;
  return (
    <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle} onPointerMove={pick} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={box} count={6} />
      <XAxis scale={sx} box={box} ticks={AZ_TICKS} />
      <g data-series="phi" data-points={result.domains.reduce((n, d) => n + d.phi.filter((v) => v !== null).length, 0)}>
        {result.domains.map((d, i) => (
          <g key={i} data-domain={domainTitle(d)} fill="none" stroke={seriesColor(i)}>
            {pieces(d.az, d.phi, mx, my).map((p, k) => (
              <path key={k} d={pathOf(p)} strokeWidth={1.8} strokeLinejoin="round" />
            ))}
          </g>
        ))}
      </g>
      <g data-series="table">
        {result.domains.map((d, i) =>
          d.table.map((s, k) => {
            const x = mx(s.az);
            const y = my(s.angle);
            if (x === null || y === null) return null;
            const lo = tol === null ? null : my(s.angle - tol);
            const hi = tol === null ? null : my(s.angle + tol);
            return (
              <g key={`${i}-${k}`} data-sector={`${s.az}:${s.angle}`} stroke={seriesColor(i)}>
                {lo !== null && hi !== null && <line x1={x} x2={x} y1={Math.max(box.y0, Math.min(box.y1, lo))} y2={Math.max(box.y0, Math.min(box.y1, hi))} strokeWidth={1.5} opacity={0.7} />}
                <circle cx={x} cy={y} r={3.6} fill="var(--bg-elev)" strokeWidth={2} />
              </g>
            );
          }),
        )}
      </g>
      {tipAz !== null && (
        <g pointerEvents="none" data-hover={tipAz}>
          <line className={css.mark} x1={tipX} x2={tipX} y1={box.y0} y2={box.y1} />
          <g className={css.tip} transform={`translate(${Math.min(tipX + 10, box.x1 - 214)} ${box.y0 + 6})`}>
            <rect width={208} height={14 + tipLines.length * 15} rx={4} />
            {tipLines.map((t, i) => (
              <text key={i} x={8} y={16 + i * 15}>
                {t}
              </text>
            ))}
          </g>
        </g>
      )}
    </PlotFrame>
  );
});
