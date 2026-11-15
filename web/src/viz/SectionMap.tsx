import { useMemo, useRef } from "react";
import type { PlanOverview } from "./scene3d";
import type { SectionLine } from "./geometry";

const W = 260;
const H = 220;
const PAD = 14;

/** Corners of the section window in plan: along the line, from `away` behind to `towards` in front. */
export function windowCorners(s: SectionLine, reach: number): [number, number][] {
  const a = (s.az * Math.PI) / 180;
  const along = [Math.sin(a), Math.cos(a)];
  const right = [Math.cos(a), -Math.sin(a)];
  const u0 = s.length === null ? -reach : 0;
  const u1 = s.length ?? reach;
  const at = (u: number, d: number): [number, number] => [s.x0 + along[0] * u + right[0] * d, s.y0 + along[1] * u + right[1] * d];
  return [at(u0, -s.away), at(u1, -s.away), at(u1, s.towards), at(u0, s.towards)];
}

/** Bounds of the overview and the section, as [minX, minY, maxX, maxY]. */
export function overviewBounds(o: PlanOverview, extra: [number, number][]): [number, number, number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = (x: number, y: number) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  for (const h of o.holes) for (const [x, y] of h) add(x, y);
  for (const [x1, y1, x2, y2] of o.segments) { add(x1, y1); add(x2, y2); }
  for (const [x1, y1, x2, y2] of o.boxes) { add(x1, y1); add(x2, y2); }
  for (const [x, y] of extra) add(x, y);
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

/**
 * Small plan of the project beside a section: the data, the section line with its window, and the
 * side the section is viewed from. While a line is being drawn, two clicks on the map draw it too.
 */
export function SectionMap({ overview, section, draft, drawing, onPoint, onClose }: {
  overview: PlanOverview;
  section: SectionLine | null;
  /** first point of a line being drawn */
  draft: { x: number; y: number } | null;
  drawing: boolean;
  onPoint: (x: number, y: number) => void;
  onClose: () => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const data = useMemo(() => overviewBounds(overview, []), [overview]);
  const reach = data ? Math.max(data[2] - data[0], data[3] - data[1]) * 0.75 + 50 : 500;
  const corners = section ? windowCorners(section, reach) : [];
  // The map shows the data; a limited line is always inside it too.
  const b = overviewBounds(overview, section?.length != null ? corners : section ? [[section.x0, section.y0]] : []) ?? [0, 0, 100, 100];
  const span = Math.max(b[2] - b[0], b[3] - b[1], 1);
  const k = Math.min((W - 2 * PAD) / Math.max(b[2] - b[0], span * 0.05), (H - 2 * PAD) / Math.max(b[3] - b[1], span * 0.05));
  const cx = (b[0] + b[2]) / 2;
  const cy = (b[1] + b[3]) / 2;
  const sx = (x: number) => W / 2 + (x - cx) * k;
  const sy = (y: number) => H / 2 - (y - cy) * k;
  const pts = (list: [number, number][]) => list.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");

  let line: [number, number][] = [];
  let eye: [number, number] | null = null;
  if (section) {
    const a = (section.az * Math.PI) / 180;
    const u0 = section.length === null ? -reach : 0;
    const u1 = section.length ?? reach;
    line = [u0, u1].map((u) => [section.x0 + Math.sin(a) * u, section.y0 + Math.cos(a) * u]);
    const mid = (u0 + u1) / 2;
    // The viewer stands on the "towards" side (to the right of the line).
    const off = section.towards + 18 / k;
    eye = [section.x0 + Math.sin(a) * mid + Math.cos(a) * off, section.y0 + Math.cos(a) * mid - Math.sin(a) * off];
  }

  const click = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!drawing || !svgRef.current) return;
    const r = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const y = ((e.clientY - r.top) / r.height) * H;
    onPoint(cx + (x - W / 2) / k, cy - (y - H / 2) / k);
  };

  return (
    <div className="section-map" aria-label="План с линией разреза">
      <div className="section-map-head">
        <span>План{drawing ? " · кликните начало и конец" : ""}</span>
        <button className="icon" onClick={onClose} title="Скрыть план" aria-label="Скрыть план">×</button>
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className={drawing ? "drawing" : ""} onClick={click} role="img">
        <defs>
          <clipPath id="section-map-clip"><rect x={0} y={0} width={W} height={H} /></clipPath>
        </defs>
        <g clipPath="url(#section-map-clip)">
          {overview.boxes.map((r, i) => (
            <rect key={`b${i}`} className="sm-box" x={sx(r[0])} y={sy(r[3])} width={(r[2] - r[0]) * k} height={(r[3] - r[1]) * k} />
          ))}
          {overview.segments.length > 0 && (
            <path className="sm-lines" d={overview.segments.map(([x1, y1, x2, y2]) => `M${sx(x1).toFixed(1)} ${sy(y1).toFixed(1)}L${sx(x2).toFixed(1)} ${sy(y2).toFixed(1)}`).join("")} />
          )}
          {overview.holes.map((h, i) => h.length > 1 && <polyline key={`h${i}`} className="sm-hole" points={pts(h)} />)}
          {overview.collars.map(([x, y], i) => <circle key={`c${i}`} className="sm-collar" cx={sx(x)} cy={sy(y)} r={1.8} />)}
          {section && (
            <>
              <polygon className="sm-window" points={pts(corners)} />
              <polyline className="sm-section" points={pts(line)} />
              {section.length !== null && line.map(([x, y], i) => <circle key={`e${i}`} className="sm-end" cx={sx(x)} cy={sy(y)} r={3} />)}
              {eye && (
                <g transform={`translate(${sx(eye[0])} ${sy(eye[1])}) rotate(${section.az - 90})`}>
                  <title>Направление взгляда на разрез</title>
                  <path className="sm-eye" d="M0 -6 L-5 4 L5 4 Z" />
                </g>
              )}
            </>
          )}
          {draft && <circle className="sm-draft" cx={sx(draft.x)} cy={sy(draft.y)} r={4} />}
        </g>
        <g className="sm-north" transform={`translate(${W - 12} 16)`}>
          <path d="M0 -8 L4 4 L0 1 L-4 4 Z" />
          <text y={14} textAnchor="middle">С</text>
        </g>
      </svg>
    </div>
  );
}
