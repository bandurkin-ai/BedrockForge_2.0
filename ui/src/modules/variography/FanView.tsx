/**
 * The tab «Веер» of the window «Вариография» (ТЗ 8.6.4 «Веер»; ADR 0022 §5, §15, decision 8): the experimental
 * variograms of the rays of the fan in the plane of the axes of the model (each with the curve of the working model
 * along it, the world azimuth and plunge in its label) and the rose of ranges (the first distance where γ̂ ≥ q·σ²;
 * not reached — a dashed spoke «> h»), the plan seen from above: a1 up, a2 to the left, θ counter-clockwise. A click
 * on the rose turns the model: its a1 goes to the nearest ray (`turnTo`); the line of the a1 of the model is marked.
 * The parameters and the job — `MapRun` (shared with «Карта»).
 */
import { useRef } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { RoseChart } from "../../charts/variogram/RoseChart";
import { curvesCsv, roseCsv } from "../../charts/variogram/scale";
import { VariogramChart, type VariogramSeries } from "../../charts/variogram/VariogramChart";
import css from "../../shell/PropsParts.module.css";
import { chartType } from "./calc";
import { rayLabel, roseWithWorld, type MapCalc } from "./mapCalc";
import { MapRun, planeAxes, selectedTheta, turnTo, type MapJob } from "./MapView";
import { direction, type VModel } from "./model";
import type { Work } from "./state";
import own from "./Variography.module.css";

/** One series per ray: the label «θ 30° · Аз 330° · Пог 0°», the model curve along the world direction of the ray. */
export function fanSeries(m: Pick<MapCalc, "fan">): VariogramSeries[] {
  return (m.fan ?? []).map((d) => ({ label: rayLabel(d), curve: d, u: direction(d.az, d.plunge) }));
}

/** The farthest lag of the fan (the radius of the rose). */
export function fanHMax(m: Pick<MapCalc, "fan">): number | null {
  let hi = 0;
  for (const d of m.fan ?? []) for (const x of d.lags) if (Number.isFinite(x) && x > hi) hi = x;
  return hi > 0 ? hi : null;
}

export function FanView({ work, winId, job }: { work: Work; winId: string; job: MapJob }) {
  const rays = useRef<SVGSVGElement | null>(null);
  const roseRef = useRef<SVGSVGElement | null>(null);
  const m = work.mapCalc ?? null;
  const type = chartType(work.model, work.data.transform);
  const model: VModel | null = work.model.structures.length || work.model.c0 > 0 ? work.model : null;
  const rose = m ? roseWithWorld(m) : null;
  const name = `Веер ${work.data.field ?? ""}`.trim();
  return (
    <div className={own.charts} data-testid="variography-fan">
      <MapRun work={work} winId={winId} job={job} />
      {!m && <p className={own.empty}>Веер не рассчитан: нажмите «Рассчитать веер и карту»</p>}
      {m && !m.fan && <p className={own.empty}>Веер не заказан в расчёте</p>}
      {m?.fan && (
        <figure className={own.chart}>
          <VariogramChart ref={rays} series={fanSeries(m)} type={type} model={model} sigma2={m.sigma2} title="Лучи веера" width={520} height={340} counts={false} />
          <ChartToolbar svgRef={rays} fileName={name} csv={() => curvesCsv(fanSeries(m), type)} />
        </figure>
      )}
      {rose && (
        <figure className={own.chart}>
          <RoseChart ref={roseRef} rose={rose} hMax={fanHMax(m!)} axes={planeAxes(m!.plane)} selected={selectedTheta(work, m!)} onPick={(t) => turnTo(winId, m!, t)} />
          <ChartToolbar svgRef={roseRef} fileName={`Роза дальностей ${work.data.field ?? ""}`.trim()} csv={() => roseCsv(rose)} />
          <p className={css.note}>Щелчок по розе — ось a1 модели по ближайшему лучу</p>
        </figure>
      )}
    </div>
  );
}
