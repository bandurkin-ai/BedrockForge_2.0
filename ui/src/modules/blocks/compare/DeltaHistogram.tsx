/**
 * The histogram of the difference Δ = A − B of the two estimates (ТЗ 7.35 Д-37; ADR 0027 решение 25): the bars are the number of blocks (the bins of the core, 50 of them),
 * the line at Δ = 0 is «no difference», the line at the mean is dashed; the tooltip of the bar under the cursor. The shared frame of the charts (`charts/core`), the brand
 * colours; PNG, SVG and the CSV of the bins — the toolbar of the charts. Blocks with a gap in A or B are not in it (their Δ is a gap, not 0). Presentation only.
 */
import { useMemo, useRef, useState } from "react";
import { XAxis, YAxis } from "../../../charts/core/Axes";
import { domainOf, formatNumber, makeScale } from "../../../charts/core/axisScale";
import css from "../../../charts/core/Chart.module.css";
import { ChartToolbar } from "../../../charts/core/ChartToolbar";
import { csvText } from "../../../charts/core/csv";
import { seriesColor } from "../../../charts/core/palette";
import { plotBox, PlotFrame } from "../../../charts/core/PlotFrame";
import type { EstimateCompare } from "./api";
import { bars } from "./model";
import mod from "./Compare.module.css";

const W = 640;
const H = 300;

export function DeltaHistogram({ result }: { result: EstimateCompare }) {
  const svg = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const box = useMemo(() => plotBox(W, H, { left: 66, right: 20, top: 16, bottom: 46 }), []);
  const list = bars(result.histogram);
  const lo = list.length ? list[0].x0 : null;
  const hi = list.length ? list[list.length - 1].x1 : null;
  const xDom = lo === null || hi === null ? null : domainOf([lo, hi], "linear", 0.02);
  const top = Math.max(0, ...list.map((b) => b.count));
  if (!xDom || !list.length) {
    return (
      <p className={mod.empty} data-testid="ecmp-hist-empty">
        Гистограммы нет: ни в одном блоке нет значения и в A, и в B.
      </p>
    );
  }
  const sx = makeScale("linear", xDom, box.x0, box.x1);
  const sy = makeScale("linear", { min: 0, max: top * 1.08 || 1 }, box.y1, box.y0);
  const x = (v: number) => sx.map(v) ?? box.x0;
  const y0 = sy.map(0) ?? box.y1;
  const zero = xDom.min <= 0 && xDom.max >= 0 ? x(0) : null;
  const mean = result.stats.mean;
  const meanX = mean !== null && mean >= xDom.min && mean <= xDom.max ? x(mean) : null;
  const color = seriesColor(0);
  const b = hover === null ? null : list[hover];
  return (
    <div className={mod.chart} data-testid="ecmp-hist" data-bars={list.length}>
      <PlotFrame ref={svg} box={box} label={`Гистограмма разности Δ = ${result.a} − ${result.b}`} xTitle={`Δ = A − B (${result.a} − ${result.b})`} yTitle="Число блоков" onPointerLeave={() => setHover(null)}>
        <YAxis scale={sy} box={box} />
        <XAxis scale={sx} box={box} count={7} />
        <g data-bars={list.length}>
          {list.map((r, i) => {
            const t = sy.map(r.count) ?? y0;
            return <rect key={i} x={x(r.x0) + 0.5} y={t} width={Math.max(0.5, x(r.x1) - x(r.x0) - 1)} height={Math.max(0, y0 - t)} fill={color} opacity={hover === null || hover === i ? 0.9 : 0.55} onPointerEnter={() => setHover(i)} />;
          })}
        </g>
        {zero !== null && (
          <g data-mark="zero">
            <line className={css.mark} x1={zero} x2={zero} y1={box.y0} y2={box.y1} />
            <text className={css.markLabel} x={zero + 4} y={box.y0 + 11}>
              Δ = 0
            </text>
          </g>
        )}
        {meanX !== null && (
          <g data-mark="mean">
            <line className={css.mark} x1={meanX} x2={meanX} y1={box.y0} y2={box.y1} strokeDasharray="5 4" />
            <text className={css.markLabel} x={meanX + 4} y={box.y0 + 23}>
              Среднее {formatNumber(mean)}
            </text>
          </g>
        )}
        {b && (
          <g className={css.tip} transform={`translate(${Math.min(x(b.x0) + 8, box.x1 - 210)} ${box.y0 + 30})`} pointerEvents="none">
            <rect width={204} height={42} rx={4} />
            <text x={8} y={16}>
              {formatNumber(b.x0, 4)} … {formatNumber(b.x1, 4)}
            </text>
            <text x={8} y={32}>Блоков: {b.count.toLocaleString("ru-RU")}</text>
          </g>
        )}
      </PlotFrame>
      <ChartToolbar svgRef={svg} fileName={`Гистограмма Δ ${result.field}`} csv={() => csvText(["Δ от", "Δ до", "Число блоков"], list.map((r) => [r.x0, r.x1, r.count]))} csvLabel="CSV корзин" />
    </div>
  );
}
