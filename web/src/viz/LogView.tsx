import { useMemo, useState } from "react";
import type { HoleLog } from "../api";
import { formatValue, NO_VALUE_COLOR, type ColorScale } from "./colorScale";
import { niceStep } from "./geometry";

const AXIS_X = 46;
const SURVEY_X = 54;
const SAMPLE_X = 178;
const SAMPLE_W = 46;
const BAR_X = 236;
const BAR_W = 380;
const PAD_TOP = 12;

/** Hole log: depth scale, survey stations, interval colours and grade bars for one component. */
export function LogView({
  holeId,
  log,
  loading,
  error,
  component,
  unit,
  scale,
}: {
  holeId: string | null;
  log: HoleLog | null;
  loading: boolean;
  error: string | null;
  component: string | null;
  unit: string | null;
  scale: ColorScale | null;
}) {
  const [ppm, setPpm] = useState(4); // pixels per metre

  const items = useMemo(
    () =>
      (log?.intervals ?? [])
        .filter((iv) => iv.from_m !== null && iv.to_m !== null && iv.to_m > iv.from_m)
        .map((iv) => ({ ...iv, from: iv.from_m as number, to: iv.to_m as number, value: component ? iv.values[component] ?? null : null })),
    [log, component],
  );

  const stats = useMemo(() => {
    let length = 0;
    let weighted = 0;
    let max: { value: number; at: number } | null = null;
    for (const iv of items) {
      if (iv.value === null) continue;
      const len = iv.to - iv.from;
      length += len;
      weighted += iv.value * len;
      if (!max || iv.value > max.value) max = { value: iv.value, at: iv.from };
    }
    return { mean: length > 0 ? weighted / length : null, max, sampled: items.reduce((s, iv) => s + iv.to - iv.from, 0), withValue: length };
  }, [items]);

  if (!holeId) {
    return <div className="viz"><div className="canvas-note static">Выберите скважину в списке слева, чтобы увидеть её колонку.</div></div>;
  }

  const depthEnd = Math.max(log?.td ?? 0, ...items.map((i) => i.to), ...(log?.survey.map((s) => s.depth ?? 0) ?? []), 1);
  const height = depthEnd * ppm + PAD_TOP + 24;
  const tickStep = niceStep(64 / ppm, 3);
  const ticks: number[] = [];
  for (let d = 0; d <= depthEnd + 1e-9; d += tickStep) ticks.push(Number(d.toPrecision(12)));
  const y = (depth: number) => PAD_TOP + depth * ppm;

  return (
    <div className="viz">
      <div className="viz-toolbar">
        <strong>{holeId}</strong>
        {log && <span className="muted">глубина {formatValue(log.td)} м · проб: {items.length.toLocaleString("ru-RU")} · опробовано {formatValue(stats.sampled)} м</span>}
        {component && stats.mean !== null && (
          <span>{component}: среднее {formatValue(stats.mean)} {unit}{stats.max && <>, максимум {formatValue(stats.max.value)} на {stats.max.at} м</>}</span>
        )}
        <span className="toolbar-sep" />
        <button onClick={() => setPpm(Math.max(1, ppm / 1.5))} aria-label="Уменьшить">−</button>
        <span className="muted">{ppm.toFixed(1)} px/м</span>
        <button onClick={() => setPpm(Math.min(24, ppm * 1.5))} aria-label="Увеличить">+</button>
      </div>

      <div className="log-scroll">
        {loading && <div className="canvas-note static">Загрузка…</div>}
        {error && <div className="canvas-note static error">{error}</div>}
        {log && (
          <>
            <div className="log-header" style={{ width: BAR_X + BAR_W + 12 }}>
              <span style={{ left: SURVEY_X }} title="Азимут / наклон">Замеры</span>
              <span style={{ left: SAMPLE_X }}>Пробы</span>
              <span style={{ left: BAR_X }}>{component ?? "Содержание"}{unit ? `, ${unit}` : ""}</span>
            </div>
            <svg width={BAR_X + BAR_W + 12} height={height} role="img" aria-label={`Колонка скважины ${holeId}`}>
              {ticks.map((d) => (
                <g key={d}>
                  <line x1={AXIS_X - 6} x2={BAR_X + BAR_W} y1={y(d)} y2={y(d)} className="grid" />
                  <text x={AXIS_X - 9} y={y(d) + 4} textAnchor="end" className="axis-label">{d}</text>
                </g>
              ))}
              <line x1={AXIS_X} x2={AXIS_X} y1={y(0)} y2={y(depthEnd)} className="axis-line" />
              {log.td !== null && <line x1={AXIS_X - 10} x2={BAR_X + BAR_W} y1={y(log.td)} y2={y(log.td)} className="td-line" />}

              {log.survey.filter((s) => s.depth !== null).map((s, i) => (
                <g key={i}>
                  <line x1={SURVEY_X} x2={SURVEY_X + 10} y1={y(s.depth!)} y2={y(s.depth!)} className="axis-line" />
                  <text x={SURVEY_X + 14} y={y(s.depth!) + 4} className="axis-label">
                    {s.azimuth === null ? "—" : s.azimuth.toFixed(1)}° / {s.dip === null ? "—" : s.dip.toFixed(1)}°
                  </text>
                </g>
              ))}

              {items.map((iv, i) => {
                const color = scale ? scale.css(iv.value) : NO_VALUE_COLOR;
                const h = Math.max((iv.to - iv.from) * ppm, 1);
                const title = [
                  `${iv.sample_id ?? "—"}: ${iv.from}–${iv.to} м`,
                  ...Object.entries(iv.values).map(([k, v]) => `${k}: ${formatValue(v)}`),
                ].join("\n");
                return (
                  <g key={i}>
                    <title>{title}</title>
                    <rect x={SAMPLE_X} y={y(iv.from)} width={SAMPLE_W} height={h} fill={color} className="log-rect" />
                    {iv.value !== null && scale && (
                      <rect x={BAR_X} y={y(iv.from) + 0.5} width={Math.max(scale.position(iv.value) * BAR_W, 2)} height={Math.max(h - 1, 1)} fill={color} />
                    )}
                    {iv.value !== null && h >= 13 && (
                      <text x={BAR_X + Math.max((scale?.position(iv.value) ?? 0) * BAR_W, 2) + 4} y={y(iv.from) + h / 2 + 4} className="axis-label">{formatValue(iv.value)}</text>
                    )}
                  </g>
                );
              })}
            </svg>
          </>
        )}
        {log && log.truncated && <p className="muted small">Показаны первые 5000 интервалов.</p>}
        {log && items.length === 0 && !loading && <p className="muted">Для этой скважины нет проб.</p>}
      </div>
    </div>
  );
}
