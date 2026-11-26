/**
 * The chart of the panel «Pit-by-pit» (ТЗ 8.11.4–8.11.5; ADR 0028 решения 18, 19, 29): the shells on the axis (№, and RF in the tooltip), the bars of the ore and of the waste (tonnes,
 * the left axis), the lines of the NPV of the best case, of the NPV of the worst case and of the CF of a shell ($, the right axis). It draws `series` of the report (`chartData`): a shell with
 * no NPV (no calendar) breaks the line — a gap is not 0. SVG of the shared frame (`charts/core`), the brand colours (docs/brand: Copper Orange — the ore, Slate — the waste, Cobalt — the best
 * NPV, the dashed line — the worst NPV, green — the CF); a tooltip of the shell under the cursor. 200 shells are a frame of a few ms.
 */
import { forwardRef, useMemo, useState, type PointerEvent as RPointerEvent } from "react";
import { YAxis } from "../../charts/core/Axes";
import { domainOf, makeScale } from "../../charts/core/axisScale";
import css from "../../charts/core/Chart.module.css";
import { csvText } from "../../charts/core/csv";
import { BRAND, mix } from "../../charts/column/classes";
import { seriesColor } from "../../charts/core/palette";
import { plotBox, PlotFrame } from "../../charts/core/PlotFrame";
import { pathOf, pieces } from "../../charts/gradetonnage/model";
import { chartData, formatNumber, type PitReport } from "./reportModel";

export const PIT_W = 760;
export const PIT_H = 320;

/** The colours of the series (the brand: docs/brand, ADR 0006); the legend of the panel reads them too. */
export const PIT_COLORS = { ore: seriesColor(1), waste: BRAND.slate, best: seriesColor(0), worst: mix(BRAND.cobalt, BRAND.anthracite, 0.4), cf: seriesColor(2) } as const;

export const PIT_SERIES: readonly { key: keyof typeof PIT_COLORS; label: string; hint: string }[] = [
  { key: "ore", label: "Руда, т", hint: "Руда приращения оболочки при базовой цене (RF = 1)" },
  { key: "waste", label: "Пустая, т", hint: "Пустая порода приращения оболочки" },
  { key: "best", label: "NPV лучший, $", hint: "NPV лучшего случая: оболочки 1…k по порядку" },
  { key: "worst", label: "NPV худший, $", hint: "NPV худшего случая: оболочка k по уровням сверху вниз" },
  { key: "cf", label: "CF оболочки, $", hint: "Денежный поток приращения оболочки" },
];

export interface PitChartProps {
  report: PitReport;
  width?: number;
  height?: number;
}

/** The ticks of the axis of the shells: at most about twelve, the first and every `step`-th. */
export const shellTicks = (n: number): number[] => {
  const step = Math.max(1, Math.ceil(n / 12));
  return Array.from({ length: n }, (_, i) => i).filter((i) => i % step === 0);
};

export const PitChart = forwardRef<SVGSVGElement, PitChartProps>(function PitChart({ report, width = PIT_W, height = PIT_H }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useMemo(() => plotBox(width, height, { left: 74, right: 78, top: 14, bottom: 50 }), [width, height]);
  const d = useMemo(() => chartData(report), [report]);
  const n = d.k.length;
  const label = "Pit-by-pit: руда и пустая порода по оболочкам, NPV и CF";
  const xTitle = n <= 30 ? "Оболочка: № и RF" : "Оболочка №";
  const yTitle = "Руда и пустая порода, т";
  const tDom = domainOf([0, ...d.ore, ...d.waste], "linear", 0.05, 0);
  const money = [...d.best, ...d.worst, ...d.cf];
  const mDom = money.some((v) => v !== null) ? domainOf([0, ...money], "linear", 0.08) : null;
  if (!n || !tDom) {
    return (
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle}>
        <text className={css.tick} data-empty="pit" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          График не построен: у отчёта нет оболочек
        </text>
      </PlotFrame>
    );
  }
  const st = makeScale("linear", tDom, box.y1, box.y0);
  const sm = mDom ? makeScale("linear", mDom, box.y1, box.y0) : null;
  const band = (box.x1 - box.x0) / n;
  const cx = (i: number) => box.x0 + (i + 0.5) * band;
  const bw = Math.max(0.6, Math.min(18, band * 0.36));
  const base = st.map(0) ?? box.y1;
  const idx = Array.from({ length: n }, (_, i) => i);
  const line = (values: readonly (number | null)[]) => (sm ? pieces(idx, values, cx, (v) => sm.map(v)) : []);
  const best = line(d.best);
  const worst = line(d.worst);
  const cf = line(d.cf);
  const dots = n <= 60;
  const bar = (v: number | null, x: number, color: string, key: string) => {
    const y = v === null ? null : st.map(v);
    return y === null ? null : <rect key={key} x={x} y={y} width={bw} height={Math.max(0, base - y)} fill={color} />;
  };
  const pick = (e: RPointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * width;
    setHover(px < box.x0 || px > box.x1 ? null : Math.min(n - 1, Math.max(0, Math.floor((px - box.x0) / band))));
  };
  const tipLines =
    hover === null
      ? []
      : [
          `Оболочка ${d.k[hover]} · RF ${formatNumber(d.rf[hover], 2)}`,
          `Руда: ${formatNumber(d.ore[hover], 0)} т`,
          `Пустая: ${formatNumber(d.waste[hover], 0)} т`,
          `CF: ${formatNumber(d.cf[hover], 0)} $`,
          `NPV лучший: ${formatNumber(d.best[hover], 0)} $`,
          `NPV худший: ${formatNumber(d.worst[hover], 0)} $`,
        ];
  const tipX = hover === null ? 0 : cx(hover);
  const path = (parts: [number, number][][], key: string, color: string, dash?: string) => (
    <g data-series={key} data-pieces={parts.length} fill={color}>
      {parts.map((p, i) => (
        <path key={i} d={pathOf(p)} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeDasharray={dash} />
      ))}
      {dots && parts.flatMap((p, i) => p.map(([x, y], k) => <circle key={`${i}-${k}`} cx={x} cy={y} r={2.4} />))}
    </g>
  );
  return (
    <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle} yTitleRight={sm ? "NPV и CF, $" : undefined} onPointerMove={pick} onPointerLeave={() => setHover(null)}>
      <YAxis scale={st} box={box} />
      {sm && <YAxis scale={sm} box={box} side="right" grid={false} />}
      <g data-axis="x" data-shells={n}>
        {shellTicks(n).map((i) => (
          <g key={i}>
            <text className={css.tick} x={cx(i)} y={box.y1 + 14} textAnchor="middle">
              {d.k[i]}
            </text>
            {n <= 30 && (
              <text className={css.tick} x={cx(i)} y={box.y1 + 26} textAnchor="middle">
                {formatNumber(d.rf[i], 2)}
              </text>
            )}
          </g>
        ))}
      </g>
      <g data-series="ore" data-points={n}>
        {idx.map((i) => bar(d.ore[i], cx(i) - bw, PIT_COLORS.ore, `o${i}`))}
      </g>
      <g data-series="waste" data-points={n}>
        {idx.map((i) => bar(d.waste[i], cx(i), PIT_COLORS.waste, `w${i}`))}
      </g>
      {path(best, "best", PIT_COLORS.best)}
      {path(worst, "worst", PIT_COLORS.worst, "6 3")}
      {path(cf, "cf", PIT_COLORS.cf)}
      {!sm && (
        <text className={css.markLabel} data-empty="money" x={box.x1 - 6} y={box.y0 + 16} textAnchor="end">
          NPV и CF: нет значений
        </text>
      )}
      {sm && !d.best.some((v) => v !== null) && (
        <text className={css.markLabel} data-empty="npv" x={box.x1 - 6} y={box.y0 + 16} textAnchor="end">
          NPV не считается: не задана производительность по руде
        </text>
      )}
      {hover !== null && (
        <g pointerEvents="none" data-hover={hover}>
          <line className={css.mark} x1={tipX} x2={tipX} y1={box.y0} y2={box.y1} />
          <g className={css.tip} transform={`translate(${Math.min(tipX + 10, box.x1 - 206)} ${box.y0 + 6})`}>
            <rect width={200} height={14 + tipLines.length * 15} rx={4} />
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

/** The data of the chart as CSV: «№; RF; Руда, т; Пустая, т; CF, $; NPV лучший, $; NPV худший, $»; a gap stays an empty cell. */
export function chartCsv(report: PitReport): string {
  const d = chartData(report);
  return csvText(
    ["№", "RF", "Руда, т", "Пустая, т", "CF, $", "NPV лучший, $", "NPV худший, $"],
    d.k.map((k, i) => [k, d.rf[i], d.ore[i], d.waste[i], d.cf[i], d.best[i], d.worst[i]]),
  );
}
