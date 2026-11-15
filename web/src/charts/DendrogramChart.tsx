import { forwardRef } from "react";
import type { DendrogramResult } from "../api";
import { formatNumber } from "../stats/chartMath";

const PALETTE = ["#4f9dde", "#e8873a", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a"];
const ROW = 26;
const LEFT = 120;
const TREE = 300;
const GAP = 24;
const TOP = 34;

/**
 * Clusters at a cut height: fields joined by merges at or below `cut` share a cluster
 * (scipy's fcluster with criterion "distance"). Returns a cluster number per field (0-based,
 * in the order of the leaves) and per merge (-1 for merges above the cut).
 */
export function clustersAt(linkage: number[][], n: number, order: number[], cut: number): { leaf: number[]; merge: number[] } {
  const parent = Array.from({ length: 2 * n - 1 }, (_, i) => i);
  const find = (x: number): number => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  linkage.forEach(([a, b, d], i) => {
    if (d <= cut) {
      parent[find(a)] = n + i;
      parent[find(b)] = n + i;
    }
  });
  const ids = new Map<number, number>();
  const leaf = new Array<number>(n);
  for (const f of order) {
    const root = find(f);
    if (!ids.has(root)) ids.set(root, ids.size);
    leaf[f] = ids.get(root)!;
  }
  const merge = linkage.map(([, , d], i) => (d <= cut ? leaf[firstLeaf(linkage, n, n + i)] : -1));
  return { leaf, merge };
}

function firstLeaf(linkage: number[][], n: number, node: number): number {
  let x = node;
  while (x >= n) x = linkage[x - n][0];
  return x;
}

/** Colour of a correlation in [-1, 1]: blue negative, white zero, red positive. */
export function corrColor(r: number | null): string {
  if (r === null || !Number.isFinite(r)) return "#808080";
  const t = Math.max(-1, Math.min(1, r));
  const mix = (a: number, b: number, k: number) => Math.round(a + (b - a) * k);
  return t >= 0
    ? `rgb(${mix(245, 200, t)},${mix(245, 60, t)},${mix(245, 50, t)})`
    : `rgb(${mix(245, 50, -t)},${mix(245, 110, -t)},${mix(245, 200, -t)})`;
}

/** The tree drawn horizontally (distance to the right), with the correlation matrix beside it. */
export const DendrogramChart = forwardRef<SVGSVGElement, {
  result: DendrogramResult;
  cut: number | null;
  matrix: boolean;
  values: boolean;
  distanceLabel: string;
}>(function DendrogramChart({ result, cut, matrix, values, distanceLabel }, ref) {
  const n = result.names.length;
  const order = result.order;
  const cell = Math.max(18, Math.min(34, 480 / n));
  const matrixW = matrix ? n * cell : 0;
  const W = LEFT + TREE + GAP + matrixW + 20;
  const rows = Math.max(ROW, matrix ? cell : ROW);
  const H = TOP + n * rows + 46;
  const maxD = Math.max(result.max_distance, cut ?? 0, 1e-9) * 1.05;
  // scipy's layout: leaves at 5, 15, 25, ... ; distance 0 at the leaves.
  const y = (i: number) => TOP + ((i - 5) / 10) * rows + rows / 2;
  const x = (d: number) => LEFT + TREE - (d / maxD) * TREE;
  const groups = cut !== null ? clustersAt(result.linkage, n, order, cut) : null;
  const color = (k: number) => (k < 0 ? "currentColor" : PALETTE[k % PALETTE.length]);
  /** A link below the cut takes the colour of its cluster: that of a leaf under its left end
   * (subtrees occupy contiguous leaf positions, so the position under the left end is inside it). */
  const linkCluster = (ic: number[], dc: number[]) => {
    if (!groups || dc[1] > (cut ?? -1)) return -1;
    const pos = Math.min(n - 1, Math.max(0, Math.floor((ic[0] - 5) / 10 + 1e-9)));
    return groups.leaf[order[pos]];
  };
  const ticks = Array.from({ length: 5 }, (_, k) => (maxD / 4) * k);
  const mx0 = LEFT + TREE + GAP;

  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W * 1.25} className="hist-chart dendro-chart" role="img" aria-label="Дендрограмма">
      <rect x={0} y={0} width={W} height={H} className="chart-bg" />
      {ticks.map((t) => (
        <g key={t}>
          <line className="chart-grid" x1={x(t)} x2={x(t)} y1={TOP - 6} y2={TOP + n * rows} />
          <text className="chart-tick" x={x(t)} y={TOP + n * rows + 15} textAnchor="middle">{formatNumber(t)}</text>
        </g>
      ))}
      <text className="chart-axis" x={LEFT + TREE / 2} y={H - 8} textAnchor="middle">{distanceLabel}</text>
      {result.icoord.map((ic, k) => {
        const dc = result.dcoord[k];
        const c = groups ? color(linkCluster(ic, dc)) : "currentColor";
        const pts = ic.map((v, j) => `${x(dc[j]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
        return <polyline key={k} points={pts} fill="none" stroke={c} strokeWidth={1.6} className={groups ? "" : "dendro-link"} />;
      })}
      {cut !== null && cut <= maxD && (
        <g>
          <line className="hist-mark" x1={x(cut)} x2={x(cut)} y1={TOP - 10} y2={TOP + n * rows} strokeDasharray="5 4" />
          <text className="chart-tick hist-mark-label" x={x(cut) + 4} y={TOP - 14}>порог {formatNumber(cut)}</text>
        </g>
      )}
      {order.map((f, i) => (
        <text key={f} className="chart-axis" x={LEFT - 8} y={y(5 + i * 10)} textAnchor="end" dominantBaseline="middle" fill={groups ? color(groups.leaf[f]) : undefined}>
          {result.names[f]}
        </text>
      ))}
      {matrix && (
        <g>
          {order.map((fi, i) =>
            order.map((fj, j) => {
              const r = result.correlation[fi][fj];
              return (
                <g key={`${i}-${j}`}>
                  <rect x={mx0 + j * cell} y={TOP + i * rows + (rows - cell) / 2} width={cell - 1} height={cell - 1} fill={corrColor(r)}>
                    <title>{`${result.names[fi]} — ${result.names[fj]}: r = ${formatNumber(r, 4)}, проб ${result.pairs[fi][fj]}`}</title>
                  </rect>
                  {values && cell >= 24 && (
                    <text x={mx0 + j * cell + cell / 2} y={TOP + i * rows + rows / 2} textAnchor="middle" dominantBaseline="middle" className="dendro-value">
                      {r === null ? "—" : r.toFixed(2).replace("0.", ".")}
                    </text>
                  )}
                </g>
              );
            }),
          )}
          {order.map((f, j) => (
            <text key={f} className="chart-tick" transform={`translate(${mx0 + j * cell + cell / 2} ${TOP - 6}) rotate(-45)`} textAnchor="start">{result.names[f]}</text>
          ))}
        </g>
      )}
    </svg>
  );
});
