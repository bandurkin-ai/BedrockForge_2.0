/**
 * The contact analysis (ТЗ 8.3; ADR 0019 §11): the distance from the contact along the hole (side A to the left,
 * B to the right) against the mean of each step — a step line in the colours of the sides, the contact at 0;
 * n of the steps as bars on the right axis. An empty step breaks the line (never 0). SVG of a fixed size.
 */
import { forwardRef, useState } from "react";
import type { ContactResult } from "../../modules/charts/apiMore";
import { domainOf, formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { BRAND } from "../column/classes";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { contactSteps, stepRuns } from "./model";

export const CONTACT_W = 720;
export const CONTACT_H = 420;
const BOX = plotBox(CONTACT_W, CONTACT_H, { left: 62, right: 56, top: 26, bottom: 46 });
const SIDE = { a: BRAND.cobalt, b: BRAND.copper };

export const ContactChart = forwardRef<SVGSVGElement, { result: ContactResult; counts: boolean; xLabel: string }>(function ContactChart({ result: r, counts, xLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const steps = contactSteps(r);
  const dy = domainOf(steps.map((s) => s.mean), "linear", 0.08);
  if (!steps.length || !dy) return null;
  const sx = makeScale("linear", { min: r.edges[0], max: r.edges[r.edges.length - 1] }, BOX.x0, BOX.x1);
  const sy = makeScale("linear", dy, BOX.y1, BOX.y0);
  const top = Math.max(1, ...steps.map((s) => s.n));
  const sn = makeScale("linear", { min: 0, max: top * 3 }, BOX.y1, BOX.y0);
  const px = (v: number) => sx.map(v) ?? BOX.x0;
  const zero = px(0);
  const h = hover !== null ? steps[hover] : null;
  return (
    <PlotFrame ref={ref} box={BOX} label={`Контактный анализ: ${xLabel}`} xTitle="Расстояние от контакта по стволу, м" yTitle={xLabel} yTitleRight={counts ? "Проб в шаге" : undefined} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} />
      <XAxis scale={sx} box={BOX} />
      {counts && (
        <g data-counts={steps.length}>
          {steps.map((s, i) => {
            const y = sn.map(s.n) ?? BOX.y1;
            return <rect key={i} x={px(s.from) + 0.5} y={y} width={Math.max(0.5, px(s.to) - px(s.from) - 1)} height={Math.max(0, BOX.y1 - y)} fill={SIDE[s.side]} opacity={0.18} />;
          })}
          <YAxis scale={sn} box={BOX} side="right" grid={false} ticks={sn.ticks(4).filter((t) => t.value <= top)} />
        </g>
      )}
      <line className={css.mark} data-contact="0" x1={zero} x2={zero} y1={BOX.y0} y2={BOX.y1} />
      <text className={css.markLabel} x={zero + 4} y={BOX.y0 - 8}>
        контакт
      </text>
      <text className={css.axisTitle} x={BOX.x0 + 6} y={BOX.y0 + 14} fill={SIDE.a}>
        {r.a.label}
      </text>
      <text className={css.axisTitle} x={BOX.x1 - 6} y={BOX.y0 + 14} textAnchor="end" fill={SIDE.b}>
        {r.b.label}
      </text>
      <g data-means={steps.filter((s) => s.mean !== null).length}>
        {stepRuns(steps).map((run, k) => (
          <polyline key={k} fill="none" stroke={SIDE[run[0].side]} strokeWidth={2} points={run.flatMap((s) => [`${px(s.from).toFixed(1)},${(sy.map(s.mean) ?? 0).toFixed(1)}`, `${px(s.to).toFixed(1)},${(sy.map(s.mean) ?? 0).toFixed(1)}`]).join(" ")} />
        ))}
        {steps.map((s, i) =>
          s.mean === null ? null : <circle key={i} cx={px(s.mid)} cy={sy.map(s.mean) ?? 0} r={hover === i ? 4.5 : 2.6} fill={SIDE[s.side]} onPointerEnter={() => setHover(i)} />,
        )}
      </g>
      {h && (
        <g className={css.tip} transform={`translate(${Math.min(px(h.mid) + 10, BOX.x1 - 190)} ${BOX.y0 + 20})`}>
          <rect width={184} height={58} rx={4} />
          <text x={8} y={16}>
            {formatNumber(h.from)} … {formatNumber(h.to)} м ({h.side === "a" ? r.a.label : r.b.label})
          </text>
          <text x={8} y={32}>Среднее: {formatNumber(h.mean, 5)}</text>
          <text x={8} y={48}>Проб: {h.n.toLocaleString("ru-RU")}</text>
        </g>
      )}
    </PlotFrame>
  );
});
