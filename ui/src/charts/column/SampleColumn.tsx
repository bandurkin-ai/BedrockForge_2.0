/**
 * A column of intervals along a hole (ТЗ 8.2.3: «пробы и композиты рядом»; shared with Э13–Э15): one bar per
 * interval coloured by the value of a field, a missing value hatched (never the colour of zero), a mark at a
 * flagged interval («мало проб»), the value written where the bar is tall enough, a tooltip «от–до м: значение».
 * `DepthAxis` — the depth ticks for the columns beside it on the same `DepthScale`. SVG only; data — `scale.ts`.
 */
import { useId } from "react";
import { columnBars, depthText, niceTicks, valueText, type Columns, type DepthScale } from "./scale";

const HEAD = 18;
const LABEL_MIN_H = 13;

export interface SampleColumnProps {
  title: string;
  cols: Columns;
  field: string | null;
  scale: DepthScale;
  color: (v: number | string | null | undefined) => string | null;
  width?: number;
  /** A text field whose non-empty value marks the bar (FLAG of composites). */
  flagField?: string;
  testId?: string;
}

export function SampleColumn({ title, cols, field, scale, color, width = 64, flagField, testId }: SampleColumnProps) {
  const hatch = `hatch-${useId().replace(/:/g, "")}`;
  const bars = columnBars(cols, field, scale, color);
  const flags = flagField ? (cols.values[flagField] ?? []) : [];
  const h = scale.pad + scale.height + 2;
  return (
    <svg width={width} height={h + HEAD} role="img" aria-label={`${title}: ${bars.length} интервалов`} data-testid={testId} style={{ flex: "none", overflow: "visible" }}>
      <defs>
        <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" style={{ stroke: "var(--text-faint)" }} strokeWidth="1.5" />
        </pattern>
      </defs>
      <text x={width / 2} y={12} textAnchor="middle" fontSize="11" fontWeight={600} style={{ fill: "var(--text)", fontFamily: "var(--font-brand)" }}>
        {title}
      </text>
      <g transform={`translate(0, ${HEAD})`}>
        {bars.map((b) => {
          const flag = flags[b.index];
          const flagged = typeof flag === "string" ? flag.trim() !== "" : flag !== null && flag !== undefined;
          const tip = `${depthText(b.from)}–${depthText(b.to)} м: ${valueText(b.value)}${flagged ? ` · ${String(flag)}` : ""}`;
          return (
            <g key={b.index} data-missing={b.color === null ? "yes" : undefined} data-flag={flagged ? "yes" : undefined}>
              <rect x={1} y={b.y} width={width - 2} height={b.h} fill={b.color ?? `url(#${hatch})`} strokeWidth={0.5} style={{ stroke: "var(--border-strong)" }}>
                <title>{tip}</title>
              </rect>
              {b.h >= LABEL_MIN_H && (
                <text x={width / 2} y={b.y + b.h / 2 + 4} textAnchor="middle" fontSize="10" pointerEvents="none" style={{ fill: b.color ? "#ffffff" : "var(--text-dim)" }}>
                  {b.color === null ? "—" : valueText(b.value, 2)}
                </text>
              )}
              {flagged && <rect x={width - 5} y={b.y} width={4} height={b.h} pointerEvents="none" style={{ fill: "var(--warn)" }} />}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

export function DepthAxis({ scale, width = 40, count = 8 }: { scale: DepthScale; width?: number; count?: number }) {
  const ticks = niceTicks(scale.top, scale.bottom, count);
  const h = scale.pad + scale.height + 2;
  return (
    <svg width={width} height={h + HEAD} aria-hidden="true" style={{ flex: "none", overflow: "visible" }}>
      <text x={width - 4} y={12} textAnchor="end" fontSize="10" style={{ fill: "var(--text-dim)" }}>
        м
      </text>
      <g transform={`translate(0, ${HEAD})`}>
        <line x1={width - 1} y1={scale.pad} x2={width - 1} y2={scale.pad + scale.height} style={{ stroke: "var(--border-strong)" }} />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={width - 5} y1={scale.y(t)} x2={width - 1} y2={scale.y(t)} style={{ stroke: "var(--border-strong)" }} />
            <text x={width - 7} y={scale.y(t) + 3} textAnchor="end" fontSize="10" style={{ fill: "var(--text-dim)" }}>
              {depthText(t)}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
}
