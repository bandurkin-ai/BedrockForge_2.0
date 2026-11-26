/**
 * What the chart of the curve «тоннаж — содержание» draws from the curve of the core (ТЗ 8.10; ADR 0027 решение 11): T (tonnes above the cut-off, the left axis),
 * ḡ and M (the grade and the metal of an element above it, the right axis) against the cut-off, from the 100 cut-offs of the core (2…500). The drawing is the
 * chart of the project (`charts/gradetonnage`): a point with no grade is a gap, never 0. Pure.
 */
import type { CurveData } from "../../charts/gradetonnage/model";
import type { Curve, ReportResult } from "./answers";

/** The elements the curve has (the order of the core). */
export const curveElements = (curve: Curve): string[] => Object.keys(curve.elements);

/** The element of the chart: the chosen one if the curve has it, else the cut-off field if it is an element, else the first. */
export function curveElement(result: ReportResult, curve: Curve, chosen: string | null): string | null {
  const names = curveElements(curve);
  if (chosen && names.includes(chosen)) return chosen;
  if (names.includes(result.cutoff_field)) return result.cutoff_field;
  return names[0] ?? null;
}

/** The unit of the cut-off axis: the unit of the element that is the cut-off field («г/т»), empty when the field is not an element. */
export const cutoffUnit = (result: ReportResult): string | null => result.elements.find((e) => e.field === result.cutoff_field)?.unit_label || null;

/** The curve for the chart of one element: the arrays are as long as the cut-offs; an element that is not there has gaps only. */
export function curveData(result: ReportResult, curve: Curve, element: string | null): CurveData {
  const n = curve.cutoffs.length;
  const gaps = (): (number | null)[] => Array.from({ length: n }, () => null);
  const e = element ? curve.elements[element] : undefined;
  const info = element ? result.elements.find((x) => x.field === element) : undefined;
  return {
    field: curve.field || result.cutoff_field,
    unit: cutoffUnit(result),
    cutoffs: curve.cutoffs,
    tonnes: curve.tonnes.length === n ? curve.tonnes : gaps(),
    grade: e && e.grade.length === n ? e.grade : gaps(),
    metal: e && e.metal.length === n ? e.metal : gaps(),
    metal_unit: info?.metal_base ?? null,
  };
}

/** The cut-offs of the table (dashed marks on the chart): the distinct numbers of the rows. */
export const curveMarks = (result: ReportResult): number[] => [...new Set(result.rows.filter((r) => r.cutoff !== null).map((r) => r.cutoff as number))];
