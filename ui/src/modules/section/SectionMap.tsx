/**
 * The mini-plan beside a section (ТЗ 7.14; BF `SectionMap.tsx`): the collars and traces of the visible sets
 * (≤ 20 000 points, «упрощено» beyond), the line with its window and the side it is seen from (an orange
 * triangle). While a line is being drawn, two clicks on the plan draw it too. × hides it (`section.plan`).
 * With an active series of sections (ADR 0016 §4) — the traces of its sections, the current one as the line.
 */
import { useMemo, useRef } from "react";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import type { SectionLine } from "../../viz/section/math";
import { overviewBounds, PLAN_H, PLAN_W, planFrame, seriesTraces, type PlanFrame, type PlanOverview, type SeriesTraces } from "../../viz/section/miniPlan";
import { viewStore } from "../scene/bind";
import css from "../scene/Scene.module.css";
import { currentSection, seriesStore } from "./seriesState";
import type { SectionKey } from "./state";

/** The mini-plan (when open) shows the free line, a line being drawn or the traces of the active series. */
export const miniPlanWanted = (key: Pick<SectionKey, "line" | "series">, drawing: boolean): boolean => key.line !== null || !!key.series || drawing;

const TRACE = { stroke: "var(--accent)", strokeWidth: 1, opacity: 0.45, fill: "none" } as const;

/** The traces of a series on a plan frame: the others thin, the current one as the section line. */
export function SeriesLines({ f, traces }: { f: PlanFrame; traces: SeriesTraces }) {
  const d = (l: [number, number, number, number]) => `M${f.sx(l[0]).toFixed(1)} ${f.sy(l[1]).toFixed(1)}L${f.sx(l[2]).toFixed(1)} ${f.sy(l[3]).toFixed(1)}`;
  const others = traces.lines.filter((_, i) => i !== traces.active);
  return (
    <g data-testid="series-traces" data-count={traces.lines.length}>
      {others.length > 0 && <path style={TRACE} d={others.map(d).join("")} />}
      {traces.active !== null && <path className={css.smSection} d={d(traces.lines[traces.active])} data-testid="series-trace-active" />}
    </g>
  );
}

/** The traces of the active series for the overview, null — no active series. */
function useSeriesTraces(overview: PlanOverview): SeriesTraces | null {
  const keys = useStore(viewStore, (k) => k);
  const state = useStore(seriesStore, (s) => s);
  return useMemo(() => {
    const cur = currentSection(keys, state);
    return cur ? seriesTraces(cur.geom, cur.index, overviewBounds(overview, [])) : null;
  }, [keys, state, overview]);
}

export function SectionMap({ overview, section, draft, drawing, onPoint, onClose }: {
  overview: PlanOverview;
  section: SectionLine | null;
  /** the first point of a line being drawn */
  draft: { x: number; y: number } | null;
  drawing: boolean;
  onPoint: (x: number, y: number) => void;
  onClose: () => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const traces = useSeriesTraces(overview);
  const f = useMemo(() => planFrame(overview, section, PLAN_W, PLAN_H, traces?.extent ?? []), [overview, section, traces]);
  const pts = (list: [number, number][]) => list.map(([x, y]) => `${f.sx(x).toFixed(1)},${f.sy(y).toFixed(1)}`).join(" ");
  const click = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!drawing || !svg.current) return;
    const r = svg.current.getBoundingClientRect();
    const p = f.toWorld(((e.clientX - r.left) / r.width) * PLAN_W, ((e.clientY - r.top) / r.height) * PLAN_H);
    onPoint(p.x, p.y);
  };
  return (
    <div className={css.map} aria-label="План с линией разреза" data-testid="section-map">
      <div className={css.mapHead}>
        <span>
          План{drawing ? " · щёлкните начало и конец" : ""}
          {overview.simplified && <span className={css.simplified}> · упрощено</span>}
        </span>
        <button type="button" onClick={onClose} title="Скрыть план" aria-label="Скрыть план">
          <Icon name="close" size={12} />
        </button>
      </div>
      <svg ref={svg} viewBox={`0 0 ${PLAN_W} ${PLAN_H}`} className={drawing ? css.drawing : ""} onClick={click} role="img" aria-label="Мини-план">
        <defs>
          <clipPath id="bf-section-map-clip">
            <rect x={0} y={0} width={PLAN_W} height={PLAN_H} />
          </clipPath>
        </defs>
        <g clipPath="url(#bf-section-map-clip)">
          {overview.boxes.map((r, i) => (
            <rect key={`b${i}`} className={css.smBox} x={f.sx(r[0])} y={f.sy(r[3])} width={(r[2] - r[0]) * f.k} height={(r[3] - r[1]) * f.k} />
          ))}
          {overview.segments.length > 0 && <path className={css.smLines} d={overview.segments.map(([x1, y1, x2, y2]) => `M${f.sx(x1).toFixed(1)} ${f.sy(y1).toFixed(1)}L${f.sx(x2).toFixed(1)} ${f.sy(y2).toFixed(1)}`).join("")} />}
          {overview.holes.map((h, i) => h.length > 1 && <polyline key={`h${i}`} className={css.smHole} points={pts(h)} />)}
          {overview.collars.map(([x, y], i) => (
            <circle key={`c${i}`} className={css.smCollar} cx={f.sx(x)} cy={f.sy(y)} r={1.8} />
          ))}
          {section && (
            <>
              <polygon className={css.smWindow} points={pts(f.corners)} />
              <polyline className={css.smSection} points={pts(f.line)} />
              {section.length !== null && f.line.map(([x, y], i) => <circle key={`e${i}`} className={css.smEnd} cx={f.sx(x)} cy={f.sy(y)} r={3} />)}
              {f.eye && (
                <g transform={`translate(${f.sx(f.eye.x)} ${f.sy(f.eye.y)}) rotate(${f.eye.rotate})`}>
                  <title>Направление взгляда на разрез</title>
                  <path className={css.smEye} d="M0 -6 L-5 4 L5 4 Z" />
                </g>
              )}
            </>
          )}
          {traces && <SeriesLines f={f} traces={traces} />}
          {draft && <circle className={css.smDraft} cx={f.sx(draft.x)} cy={f.sy(draft.y)} r={4} />}
        </g>
        <g className={css.smNorth} transform={`translate(${PLAN_W - 12} 16)`}>
          <path d="M0 -8 L4 4 L0 1 L-4 4 Z" />
          <text y={14} textAnchor="middle">
            С
          </text>
        </g>
      </svg>
    </div>
  );
}
