/**
 * The result of the report in the window «Содержание–тоннаж» (ТЗ 7.31): the table by cut-offs (and groups) — blocks,
 * volume, tonnes, and for each grade the mean, the metal with its unit and what had no value («нет значения», never 0),
 * what was not counted, the warnings of the core, the curve «содержание–тоннаж» (tonnes above a cut-off against the
 * grade or the metal), «Копировать CSV» / «Сохранить CSV…».
 */
import { Fragment, useRef, useState } from "react";
import { GradeTonnageChart } from "../../../charts/gradetonnage/GradeTonnageChart";
import { curveCsv, hasValues, rightValues, type RightAxis } from "../../../charts/gradetonnage/model";
import { ChartToolbar } from "../../../charts/core/ChartToolbar";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import type { ReportResult } from "./api";
import { copyCsv, saveCsv } from "./csvOut";
import { csvName, densityText, excludedLines, fmt, fmtCount, fmtCutoff, gapCell, gradeCell, gradeNames, groupTitle, metalCell, metalUnitOf, unitOf, type Cell } from "./format";
import mod from "./Report.module.css";

function Td({ cell, id, sep }: { cell: Cell; id: string; sep?: boolean }) {
  return (
    <td className={`${cell.missing ? mod.gap : ""} ${sep ? mod.sep : ""}`} title={cell.title} data-testid={id} data-missing={cell.missing || undefined}>
      {cell.text}
    </td>
  );
}

export function ReportTable({ result }: { result: ReportResult }) {
  const names = gradeNames(result);
  const cutUnit = unitOf(result, result.cutoff_field);
  let prev: string | null | undefined;
  return (
    <div className={mod.tableWrap}>
      <table className={mod.table} data-testid="report-table">
        <thead>
          <tr>
            <th rowSpan={2} className={mod.left}>
              Группа
            </th>
            <th rowSpan={2} title={`Блок выше борта, если значение поля «${result.cutoff_field}» не меньше борта`}>
              Борт{cutUnit ? `, ${cutUnit}` : ""}
            </th>
            <th rowSpan={2}>Блоков</th>
            <th rowSpan={2}>Объём, {result.units.volume}</th>
            <th rowSpan={2}>Тоннаж, {result.units.tonnes}</th>
            {names.map((n) => (
              <th key={n} colSpan={3} className={mod.span}>
                {n}
              </th>
            ))}
          </tr>
          <tr>
            {names.map((n) => (
              <Fragment key={n}>
                <th className={mod.sep}>Содержание{unitOf(result, n) ? `, ${unitOf(result, n)}` : ""}</th>
                <th>Металл{metalUnitOf(result, n) ? `, ${metalUnitOf(result, n)}` : ""}</th>
                <th title="Блоки строки, в которых нет значения этого содержания">Без содержания</th>
              </Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => {
            const start = row.group !== prev && i > 0;
            prev = row.group;
            return (
              <tr key={i} className={`${row.group === null ? mod.total : ""} ${start ? mod.groupStart : ""}`} data-testid={`report-row-${i}`}>
                <td className={mod.left} data-testid={`report-group-${i}`}>
                  {groupTitle(row.group)}
                </td>
                <td data-testid={`report-cutoff-${i}`}>{fmtCutoff(row.cutoff)}</td>
                <td>{fmtCount(row.blocks)}</td>
                <td>{fmt(row.volume)}</td>
                <td data-testid={`report-tonnes-${i}`}>{fmt(row.tonnes)}</td>
                {names.map((n) => (
                  <Fragment key={n}>
                    <Td cell={gradeCell(row.grades[n])} id={`report-grade-${i}-${n}`} sep />
                    <Td cell={metalCell(row.grades[n])} id={`report-metal-${i}-${n}`} />
                    <Td cell={gapCell(row.grades[n])} id={`report-gap-${i}-${n}`} />
                  </Fragment>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CurveView({ result, modelName }: { result: ReportResult; modelName: string }) {
  const curve = result.curve;
  const [right, setRight] = useState<RightAxis>("grade");
  const svg = useRef<SVGSVGElement | null>(null);
  if (!curve) return <p className={css.note}>Кривая не построена: «Точек кривой» — 0 или в поле борта нет значений.</p>;
  const metalOn = hasValues(rightValues(curve, "metal"));
  const shown: RightAxis = right === "metal" && !metalOn ? "grade" : right;
  const marks = [...new Set(result.rows.map((r) => r.cutoff))];
  return (
    <div className={mod.chartBox} data-testid="report-curve">
      <div className={mod.bar}>
        <span className={css.note}>Справа:</span>
        <span className={mod.seg} role="group" aria-label="Что показывать справа">
          <button type="button" aria-pressed={shown === "grade"} data-testid="report-right-grade" onClick={() => setRight("grade")}>
            Содержание
          </button>
          <button type="button" aria-pressed={shown === "metal"} disabled={!metalOn} title={metalOn ? undefined : "Металл не считается: единица содержания не распознана или нет содержания"} data-testid="report-right-metal" onClick={() => setRight("metal")}>
            Металл
          </button>
        </span>
      </div>
      <GradeTonnageChart ref={svg} curve={curve} right={shown} marks={marks} />
      <ChartToolbar svgRef={svg} fileName={`${csvName(modelName)} кривая`} csv={() => curveCsv(curve)} csvLabel="CSV кривой" />
    </div>
  );
}

export function ResultView({ result, modelName }: { result: ReportResult; modelName: string }) {
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const out = excludedLines(result.excluded, result.cutoff_field);
  const tell = (fn: () => Promise<string | null>) =>
    void fn().then(
      (text) => setNote(text ? { text, error: false } : null),
      (e: unknown) => setNote({ text: errorText(e), error: true }),
    );
  const csv = result.csv !== null;
  return (
    <div className={mod.result} style={{ overflow: "visible" }} data-testid="report-result">
      <div className={mod.head}>
        <h4>Модель «{modelName}»</h4>
        <span className={css.note} data-testid="report-density">
          Плотность — {densityText(result)}; борт по полю «{result.cutoff_field}»
        </span>
      </div>
      {result.warnings.length > 0 && (
        <div data-testid="report-warnings">
          {result.warnings.map((w, i) => (
            <p key={i} className={css.warn}>
              {w}
            </p>
          ))}
        </div>
      )}
      <div className={mod.bar}>
        <button type="button" className={css.btn} disabled={!csv} title={csv ? "Таблица на буфер обмена: «;» между ячейками, десятичная запятая" : "В ответе нет CSV"} data-testid="report-csv-copy" onClick={() => tell(copyCsv)}>
          Копировать CSV
        </button>
        <button type="button" className={css.btn} disabled={!csv} title={csv ? "Сохранить таблицу в файл CSV для Excel" : "В ответе нет CSV"} data-testid="report-csv-save" onClick={() => tell(async () => ((await saveCsv()) ? "Таблица CSV сохранена" : null))}>
          Сохранить CSV…
        </button>
        {note && (
          <span className={note.error ? css.error : css.note} role={note.error ? "alert" : "status"} data-testid="report-csv-note">
            {note.text}
          </span>
        )}
      </div>
      <ReportTable result={result} />
      {out.length > 0 ? (
        <div data-testid="report-excluded">
          <p className={css.note}>Не вошли в расчёт:</p>
          <ul className={mod.excluded}>
            {out.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className={css.note} data-testid="report-excluded-none">
          В расчёт вошли все блоки модели.
        </p>
      )}
      <CurveView result={result} modelName={modelName} />
    </div>
  );
}
