/**
 * The table of the blocks of a cell of the report (ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решение 24, вопрос № 111): one page of the answer of `…/drill/rows` — the cell i, j, k of the model,
 * the centre and the sizes, the share inside the cell's rules, the volume and the tonnes of the block, the grades and the keys. A gap is «—», never 0. A click on a row marks it,
 * «Инспектор» sends the block to the inspector of Д-30 (`estimate.inspect`).
 */
import { useState } from "react";
import { cellText, columnsOf, type DrillRow } from "./drill";
import css from "./Drill.module.css";

const sizeText = (r: DrillRow): string => (r.dx === null && r.dy === null && r.dz === null ? "—" : [r.dx, r.dy, r.dz].map(cellText).join(" × "));
const centreText = (r: DrillRow): string => (r.x === null && r.y === null && r.z === null ? "—" : [r.x, r.y, r.z].map(cellText).join("; "));
const cls = (...c: (string | false | undefined)[]): string | undefined => c.filter(Boolean).join(" ") || undefined;

export function BlocksTable({ rows, inspectWhy, onInspect }: { rows: readonly DrillRow[]; inspectWhy: string | null; onInspect: (r: DrillRow) => void }) {
  const [picked, setPicked] = useState<number | null>(null);
  const { keys, elements } = columnsOf(rows);
  if (!rows.length) return <p className={css.empty}>В ячейке нет блоков.</p>;
  return (
    <div className={css.tableWrap} data-testid="drill-table">
      <table className={css.table}>
        <thead>
          <tr>
            <th className={css.left}>i</th>
            <th>j</th>
            <th>k</th>
            <th className={css.sep}>Центр X; Y; Z, м</th>
            <th>Размеры dx × dy × dz, м</th>
            <th>Доля</th>
            <th>Объём, м³</th>
            <th>Тоннаж, т</th>
            {elements.map((e, n) => (
              <th key={e} className={cls(n === 0 && css.sep)}>
                {e}
              </th>
            ))}
            {keys.map((k, n) => (
              <th key={k} className={cls(css.left, n === 0 && css.sep)}>
                {k}
              </th>
            ))}
            <th className={css.sep}>Блок</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, n) => (
            <tr key={`${r.i}-${r.j}-${r.k}-${n}`} aria-selected={picked === n} data-testid={`drill-row-${n}`} onClick={() => setPicked(n)}>
              <td className={css.left}>{r.i}</td>
              <td>{r.j}</td>
              <td>{r.k}</td>
              <td className={css.sep}>{centreText(r)}</td>
              <td>{sizeText(r)}</td>
              <td className={cls(r.f === null && css.gap)}>{cellText(r.f)}</td>
              <td className={cls(r.volume === null && css.gap)}>{cellText(r.volume)}</td>
              <td className={cls(r.tonnes === null && css.gap)}>{cellText(r.tonnes)}</td>
              {elements.map((e, ei) => {
                const v = r.elements[e] ?? null;
                return (
                  <td key={e} className={cls(ei === 0 && css.sep, v === null && css.gap)} title={v === null ? "В блоке нет значения: это пропуск, не 0" : undefined}>
                    {cellText(v)}
                  </td>
                );
              })}
              {keys.map((k, ki) => (
                <td key={k} className={cls(css.left, ki === 0 && css.sep)}>
                  {r.keys[k] ?? "(пусто)"}
                </td>
              ))}
              <td className={css.sep}>
                <button type="button" disabled={inspectWhy !== null} title={inspectWhy ?? "Какие пробы вошли в оценку блока и с какими весами; из пробы — интервал скважины (7.17)"} data-testid={`drill-inspect-${n}`} onClick={() => onInspect(r)}>
                  Инспектор
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
