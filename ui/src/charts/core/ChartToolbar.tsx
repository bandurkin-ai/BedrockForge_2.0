/**
 * The export panel of a chart (ADR 0019 §11; ТЗ 7.25 «Экспорт»): PNG twice the chart's size, SVG with the
 * brand fonts embedded (`charts/striplog/export.ts`, Э6), CSV of the chart's data, «Сбросить вид».
 * The file goes through our own save dialog of the shell (bridge `saveFile`).
 */
import { useState, type ReactNode, type RefObject } from "react";
import { saveFile } from "../../bridge";
import { errorText } from "../../shell/props";
import { pngBytes, svgText } from "../striplog/export";
import css from "./Chart.module.css";

/** PNG is twice the chart (ТЗ 7.25, Т7.25-3). */
export const PNG_SCALE = 2;

export type ExportKind = "png" | "svg" | "csv";

/** Writes the chart in one of the formats; the path, or null when the dialog was cancelled. */
export async function exportChart(kind: ExportKind, fileName: string, svg: SVGSVGElement | null, csv: () => string | null): Promise<string | null> {
  let out: string | Uint8Array;
  if (kind === "csv") {
    const text = csv();
    if (text === null) return null;
    out = text;
  } else {
    if (!svg) return null;
    out = kind === "svg" ? await svgText(svg) : await pngBytes(svg, PNG_SCALE);
  }
  return saveFile(`${fileName}.${kind}`, kind, out);
}

/** `prepare` runs before a PNG or SVG export (the canvas layer inlines its picture: `PointsCanvas.inlineRasters`). */
export function ChartToolbar({ svgRef, fileName, csv, csvLabel = "CSV", disabled, onReset, prepare, children }: { svgRef: RefObject<SVGSVGElement | null>; fileName: string; csv: () => string | null; csvLabel?: string; disabled?: boolean; onReset?: () => void; prepare?: (svg: SVGSVGElement | null) => Promise<void>; children?: ReactNode }) {
  const [note, setNote] = useState<string | null>(null);
  const run = (kind: ExportKind) => {
    (kind !== "csv" && prepare ? prepare(svgRef.current) : Promise.resolve())
      .then(() => exportChart(kind, fileName, svgRef.current, csv))
      .then((path) => setNote(path ? `Сохранено: ${path}` : null))
      .catch((e: unknown) => setNote(errorText(e)));
  };
  return (
    <div className={css.toolbar} role="toolbar" aria-label="Экспорт графика">
      <button type="button" disabled={disabled} onClick={() => run("png")} title="Изображение PNG вдвое крупнее графика">
        PNG
      </button>
      <button type="button" disabled={disabled} onClick={() => run("svg")} title="Векторный рисунок SVG со шрифтами">
        SVG
      </button>
      <button type="button" disabled={disabled} onClick={() => run("csv")} title="Данные графика: разделитель «;», десятичная точка">
        {csvLabel}
      </button>
      {onReset && (
        <button type="button" onClick={onReset} title="Вернуть настройки вида по умолчанию">
          Сбросить вид
        </button>
      )}
      {children}
      {note && <span className={css.note}>{note}</span>}
    </div>
  );
}
