// The samples of the comparison of two reports for the tests (Э20, 20.16; ТЗ 7.35 Д-37): the REAL answer of the core (`compare_reports` of `contract_answers.json`: two reports of one model with another
// density, rows of the cut-offs 2 and 3 only in one of them) and what is made from it by hand — the report against itself (ТД37-1: A = B — all the differences are 0) and the curves of
// both reports (the real answer has the reports built without a curve; the curve is the real one of `report_result`, the second is the same with the tonnes cut by a tenth).
import { readCurve, type Curve } from "../../src/modules/resources/answers";
import { answer } from "./harness";

type Raw = Record<string, unknown>;
type RawRow = { only_in: string | null; a: Raw | null; b: Raw | null; delta: Raw | null } & Raw;

/** The real answer of the core: `POST /resources/compare` of two different reports. */
export const compareBody = (): Raw & { rows: RawRow[] } => answer("compare_reports").body as Raw & { rows: RawRow[] };

const zeroDelta = (row: Raw): Raw => ({ blocks: 0, volume: 0, tonnes: 0, elements: Object.fromEntries(Object.keys((row.elements as Raw) ?? {}).map((k) => [k, { grade: 0, metal: 0 }])) });

/** The report against itself, made of the real answer: the rows both reports have, B is A, the differences are 0, no warnings. */
export function selfCompare(): Raw {
  const body = compareBody();
  const rows = body.rows.filter((r) => r.only_in === null && r.a).map((r) => ({ ...r, b: r.a, delta: zeroDelta(r.a as Raw) }));
  return { ...body, b: body.a, grids_differ: false, warnings: [], rows };
}

/** The real curve of the report of the contract (`report_result.curve`). */
export const realCurve = (): Curve => readCurve(answer("report_result").body.curve)!;

/** The same curve with the tonnes cut by a tenth: «the report B», so the two lines on the chart differ. */
export function otherCurve(): Curve {
  const c = realCurve();
  return { ...c, tonnes: c.tonnes.map((t) => (t === null ? null : t * 0.9)) };
}
