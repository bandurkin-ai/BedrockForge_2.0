/**
 * The mini-plan beside a section (ТЗ 7.14; BF `SectionMap.tsx`): the collars and traces of the visible sets
 * (≤ 20 000 points, «упрощено» beyond), the line with its window and the side it is seen from (an orange
 * triangle). While a line is being drawn, two clicks on the plan draw it too. × hides it (`section.plan`).
 */
import { useMemo, useRef } from "react";
import { Icon } from "../../shell/Icon";
import type { SectionLine } from "../../viz/section/math";
import { PLAN_H, PLAN_W, planFrame, type PlanOverview } from "../../viz/section/miniPlan";
import css from "../scene/Scene.module.css";

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
  const f = useMemo(() => planFrame(overview, section), [overview, section]);
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
