/**
 * Measurements of the editor in words (ТЗ 7.22 `lines.measure`): of the selected lines and of the whole layer,
 * with the edits not saved yet; a vertex without a height — the 3D values that need it are «нет высот». Pure.
 */
import { layerMeasures, lineMeasures, type LineMeasures } from "../../edit/measure";
import { lineById } from "../../edit/model";
import type { EditSession } from "../../edit/session";
import { targetLines } from "../../edit/tools";

const N = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
export const metres = (v: number): string => `${N.format(v)} м`;
export const squareMetres = (v: number): string => `${N.format(v)} м²`;

/** «вершин 4 · длина 40 м (в плане 40 м) · периметр 40 м · площадь 100 м² (в плане 100 м²)» */
export function lineText(m: LineMeasures): string {
  const parts = [`вершин ${m.vertices}`, `длина ${metres(m.length)} (в плане ${metres(m.planLength)})`];
  if (m.missingZ) parts.push(`без высоты ${m.missingZ}`);
  if (m.closed && m.vertices >= 3) {
    parts.push(m.perimeter === null ? "периметр — нет высот" : `периметр ${metres(m.perimeter)}`);
    parts.push(`площадь ${m.area === null ? "— нет высот" : squareMetres(m.area)} (в плане ${squareMetres(m.planArea ?? 0)})`);
  }
  return parts.join(" · ");
}

/** The measurements of the selected lines: one line in full, several — their sums; null — nothing selected. */
export function selectionText(s: EditSession): string | null {
  const ids = targetLines(s);
  if (!ids.length) return null;
  const ms = ids.map((id) => lineMeasures(lineById(s.layer, id)!));
  if (ms.length === 1) return `Линия: ${lineText(ms[0])}`;
  const sum = (f: (m: LineMeasures) => number) => ms.reduce((a, m) => a + f(m), 0);
  const area = ms.filter((m) => m.planArea !== null);
  const parts = [`Выбрано линий ${ms.length}`, `вершин ${sum((m) => m.vertices)}`, `длина ${metres(sum((m) => m.length))} (в плане ${metres(sum((m) => m.planLength))})`];
  if (area.length) parts.push(`площадь в плане ${squareMetres(area.reduce((a, m) => a + (m.planArea ?? 0), 0))}`);
  return parts.join(" · ");
}

/** The sums of the layer with the unsaved edits. */
export function layerText(s: EditSession): string {
  const m = layerMeasures(s.layer);
  const parts = [`Слой «${s.layerName}»: линий ${m.lines}`, `вершин ${m.vertices}`, `длина ${metres(m.length)} (в плане ${metres(m.planLength)})`];
  if (m.closedLines) parts.push(`полигонов ${m.closedLines}, площадь ${squareMetres(m.area)}${m.areaUnknown ? ` (без ${m.areaUnknown} без высот)` : ""}, в плане ${squareMetres(m.planArea)}`);
  if (m.missingZ) parts.push(`вершин без высоты ${m.missingZ}`);
  if (s.dirty) parts.push("с несохранёнными правками");
  return parts.join(" · ");
}
