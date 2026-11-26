/**
 * The chart «тоннаж — содержание» of the result panel (ТЗ 8.10 «Вывод»; ADR 0027 решение 11): the 100 cut-offs of the core on the axis, T (tonnes above the cut-off) on the
 * left, ḡ or M of the chosen element on the right (the switch «Содержание» / «Металл»), the cut-offs of the table as dashed marks, the tooltip of the point under the cursor
 * (T, ḡ, M). The drawing is the chart of the project (`charts/gradetonnage`, the shared frame and the brand colours); PNG, SVG and the CSV of the data — the toolbar of the
 * charts. A point with no grade breaks the line: a gap is not 0; a metal of an unknown unit is told in words. Presentation only.
 */
import { useRef } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { GradeTonnageChart } from "../../charts/gradetonnage/GradeTonnageChart";
import { curveCsv, hasValues, rightValues, type RightAxis } from "../../charts/gradetonnage/model";
import type { Curve, ReportResult } from "./answers";
import { curveData, curveElement, curveElements, curveMarks } from "./curveModel";
import css from "./Panel.module.css";

export interface CurveChartProps {
  result: ReportResult;
  curve: Curve;
  element: string | null;
  right: RightAxis;
  onElement?: (name: string) => void;
  onRight?: (right: RightAxis) => void;
}

export function CurveChart({ result, curve, element, right, onElement, onRight }: CurveChartProps) {
  const svg = useRef<SVGSVGElement | null>(null);
  const names = curveElements(curve);
  const chosen = curveElement(result, curve, element);
  const data = curveData(result, curve, chosen);
  const metalOn = hasValues(rightValues(data, "metal"));
  const shown: RightAxis = right === "metal" && !metalOn ? "grade" : right;
  return (
    <div className={css.chartBox} data-testid="res-curve" data-points={curve.cutoffs.length} data-element={chosen ?? undefined}>
      <div className={css.bar}>
        {names.length > 1 && (
          <label>
            Элемент
            <select value={chosen ?? ""} aria-label="Элемент кривой" data-testid="res-curve-element" onChange={(e) => onElement?.(e.target.value)}>
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className={css.note}>Справа:</span>
        <span className={css.seg} role="group" aria-label="Что показывать справа">
          <button type="button" aria-pressed={shown === "grade"} data-testid="res-curve-grade" onClick={() => onRight?.("grade")}>
            Содержание ḡ
          </button>
          <button type="button" aria-pressed={shown === "metal"} disabled={!metalOn} title={metalOn ? undefined : "Металл не считается: единица содержания не распознана или нет содержания"} data-testid="res-curve-metal" onClick={() => onRight?.("metal")}>
            Металл M
          </button>
        </span>
        <span className={css.note}>{curve.cutoffs.length} бортов по полю {curve.field || result.cutoff_field}</span>
      </div>
      <GradeTonnageChart ref={svg} curve={data} right={shown} marks={curveMarks(result)} />
      <ChartToolbar svgRef={svg} fileName={`${result.name || "Отчёт по ресурсам"} — кривая`} csv={() => curveCsv(data)} csvLabel="CSV кривой" />
    </div>
  );
}
