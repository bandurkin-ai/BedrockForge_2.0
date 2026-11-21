/**
 * The table of a swath plot (ТЗ 7.30 «таблица с отклонением от первого ряда, %»; перенос `SwathTable` BF): the
 * swaths shown — number, centre; for each series the mean, the number of samples or the weight of blocks and,
 * from the second series on, Δ from the first, %; the last row — all swaths. An empty swath shows «—», never 0.
 */
import { formatNumber } from "../../charts/core/axisScale";
import type { SwathResult } from "./api";
import { axisValue, deviation, deviations } from "./model";
import css from "./Swath.module.css";

const big = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("ru-RU") : formatNumber(v, 4));
const pct = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}${formatNumber(Math.abs(v), 3)}`);

export function SwathTable({ result, range, hover, onHover }: { result: SwathResult; range: [number, number]; hover: number | null; onHover: (k: number | null) => void }) {
  const rows: number[] = [];
  for (let k = range[0]; k <= range[1]; k++) rows.push(k);
  const base = result.series[0];
  const dev = result.series.map((_, i) => deviations(result, i));
  return (
    <div className={css.tableBox}>
      <table className={css.table} data-testid="swath-table" onPointerLeave={() => onHover(null)}>
        <thead>
          <tr>
            <th rowSpan={2}>№</th>
            <th rowSpan={2}>Центр, м</th>
            {result.series.map((s, i) => (
              <th key={i} colSpan={i === 0 ? 2 : 3}>
                {s.name}
              </th>
            ))}
          </tr>
          <tr>
            {result.series.map((s, i) => [
              <th key={`m${i}`}>среднее</th>,
              <th key={`n${i}`}>{s.kind === "samples" ? "проб" : s.weight_unit}</th>,
              i > 0 ? (
                <th key={`d${i}`} title={`Отклонение от ряда 1 (${base?.name ?? ""}), %`}>
                  Δ, %
                </th>
              ) : null,
            ])}
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => (
            <tr key={k} className={hover === k ? css.lit : undefined} onPointerEnter={() => onHover(k)}>
              <td>{k + 1}</td>
              <td>{formatNumber(axisValue(result, result.centres[k]), 8)}</td>
              {result.series.map((s, i) => [
                <td key={`m${i}`}>{formatNumber(s.mean[k], 4)}</td>,
                <td key={`n${i}`}>{s.kind === "samples" ? (s.count[k] ?? 0) : big(s.weights[k])}</td>,
                i > 0 ? <td key={`d${i}`}>{pct(dev[i][k])}</td> : null,
              ])}
            </tr>
          ))}
          <tr className={css.total}>
            <td colSpan={2}>Все полосы</td>
            {result.series.map((s, i) => [
              <td key={`m${i}`}>{formatNumber(s.total.mean, 4)}</td>,
              <td key={`n${i}`}>{s.kind === "samples" ? s.total.count.toLocaleString("ru-RU") : big(s.total.weight)}</td>,
              i > 0 ? <td key={`d${i}`}>{pct(s.total.deviation ?? deviation(s.total.mean, base?.total.mean))}</td> : null,
            ])}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
