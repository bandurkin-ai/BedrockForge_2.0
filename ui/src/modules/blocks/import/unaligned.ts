/**
 * The refusal «Модель не ложится в сетку» and the list of blocks in the words of the window (ADR 0030 decisions 5–7, 14;
 * Д-45 А6): the grid the form is filled with, the choices of the core (`unalignedOf`: `blocks_unaligned` with a
 * diagnosis, `rotation_mismatch` with its choices), the table of the axes and of the candidates, the summary of a list
 * of blocks. Split from `model.ts` (the file is at most 600 lines); `model.ts` re-exports all of it. Pure: no React.
 */
import { n, small, triple, type BlocksDraft, BlocksError, BlocksInfo, Diag, DiagCandidate, DiagValue, Geo, UnalignedGrid, UnalignedOption } from "./model";

// ---- the refusal «blocks_unaligned»: the reason and the choices (ADR 0030 decisions 5–7) -----------------------------

type T3 = [string, string, string];

/** A number the way the user types it: a comma, at most nine decimals, no float noise. */
export const typed = (v: number): string => String(Number(v.toFixed(9))).replace(".", ",");

/** The fields of the grid filled from a grid the core offers (the counts are left to be found: they follow the corner). */
export function gridPatch(g: UnalignedGrid): Partial<BlocksDraft> {
  return {
    corner: g.corner.map(typed) as T3,
    size: g.size.map(typed) as T3,
    counts: ["", "", ""],
    div: g.div.map((d) => String(d)) as T3,
    rotation: g.rotation ? typed(g.rotation) : "",
    tol: g.tol > 1e-6 ? typed(g.tol) : "",
  };
}

/** A candidate of the diagnosis as the grid to fill the form with (its biggest tolerance of the three axes). */
export const candidateGrid = (c: DiagCandidate, rotation: number): UnalignedGrid => ({ corner: c.corner, size: c.size, div: c.div, tol: Math.max(...c.tol), rotation });

export interface Unaligned {
  error: BlocksError;
  /** the diagnosis of `blocks_unaligned`; empty for `rotation_mismatch` (the refusal names rows and a distance only) */
  diag: Diag;
  /** the refusal has a diagnosis (the tables are shown); false — `rotation_mismatch` */
  detailed: boolean;
  /** «Задать сетку вручную» — the grid it fills the form with (null: the core found none) */
  grid: UnalignedOption | null;
  /** «Импортировать списком блоков» */
  list: UnalignedOption | null;
  /** «Не импортировать этот файл» */
  cancel: UnalignedOption;
  /** the core's text of the cause first, else the message */
  lead: string;
}

/** The refusals of the core that offer a choice: `blocks_unaligned` with a diagnosis, `rotation_mismatch` with its
 * choices (rows and the distance, no table); anything else — null. */
export function unalignedOf(error: BlocksError | null | undefined): Unaligned | null {
  if (!error) return null;
  const turned = error.code === "rotation_mismatch" && (error.choices?.length ?? 0) > 0;
  if (!turned && !(error.code === "blocks_unaligned" && error.diag)) return null;
  const choices = error.choices ?? [];
  const diag = error.code === "blocks_unaligned" ? error.diag : undefined;
  return {
    error,
    diag: diag ?? { rows: 0, sample: 0, columns: {}, axes: [], candidates: [], causes: [], text: [] },
    detailed: diag !== undefined,
    grid: choices.find((c) => c.id === "grid") ?? null,
    list: choices.find((c) => c.id === "list") ?? null,
    cancel: choices.find((c) => c.id === "cancel") ?? { id: "cancel", label: "Не импортировать этот файл" },
    lead: diag?.text[0] ?? error.message,
  };
}

/** The reason of `rotation_mismatch` in the words of the window: the rows of the three centres and how far they are. */
export function mismatchText(error: BlocksError): string {
  const rows = (error.rows ?? []).map((r) => n(r + 1, 0)).join(", ");
  const dist = error.dist === undefined ? "" : ` Наибольшее расхождение: ${small(error.dist)} м.`;
  return rows ? `Центры блоков в строках ${rows} не совпали с вычисленными по сетке.${dist}` : dist.trim();
}

/** «Список блоков: 1 012 620; охват X 1 000…1 200, Y …, Z … м; размеры блоков от 2 × 2 × 0,5 до 10 × 10 × 10 м; …» */
export function listText(info: BlocksInfo): string | null {
  const l = info.list;
  if (info.layout !== "list" || !l) return null;
  const [x0, y0, z0, x1, y1, z1] = l.extent;
  const span = (a: number, b: number) => `${n(a, 2)}…${n(b, 2)}`;
  const head = `Список блоков: ${l.whole ? "" : "около "}${n(l.rows, 0)}`;
  return [
    head,
    `охват X ${span(x0, x1)}, Y ${span(y0, y1)}, Z ${span(z0, z1)} м`,
    `размеры блоков от ${triple(l.size_min)} до ${triple(l.size_max)} м`,
    `охватывающая сетка ${triple(l.cells)} ячеек по ${triple(l.cell)} м, занято ячеек ${n(l.parents, 0)}`,
  ].join("; ");
}

export const CAUSE_TITLES: Record<string, string> = {
  div_too_fine: "Общее деление родителя больше 128",
  off_lattice: "Размеры без общего деления (субблоки произвольного размера)",
  two_origins: "Целые родители лежат на двух сетках",
  rounded: "Числа записаны с округлением",
  crosses_parent: "Блоки через границу родителя",
  off_grid: "Блоки за началом сетки",
};

/** «62 %», «99,6 %», «100 %»: the share of blocks, never rounded up to a whole one (a refusal is not 100 %). */
export function percent(share: number): string {
  const p = share * 100;
  if (p >= 100) return "100 %";
  if (p >= 99.95) return "99,9 %";
  return `${n(p, p >= 10 ? 1 : 2)} %`;
}

const AXIS_COLUMNS: Record<string, [Geo, Geo]> = { X: ["x", "dx"], Y: ["y", "dy"], Z: ["z", "dz"] };

/** One row of the table of the axes: what the core counted along an axis, in the words of the window. */
export interface AxisRow {
  axis: string;
  parent: string;
  sizes: string;
  fractions: string;
  div: string;
  residues: string;
  columns: string;
  quantum: string;
}

const valuesText = (list: readonly DiagValue[], total?: number): string => {
  const text = list.map((s) => `${n(s.v, 4)} (${n(s.n, 0)})`).join("; ") || "—";
  return total !== undefined && total > list.length ? `${text}; … всего различных ${n(total, 0)}` : text;
};

export function axisRows(diag: Diag): AxisRow[] {
  return diag.axes.map((a) => {
    const [centre, size] = AXIS_COLUMNS[a.axis] ?? ["x", "dx"];
    const fr = a.fractions.filter((f) => f.q !== 1).map((f) => (f.q ? `${n(f.v, 4)} = 1/${f.q}` : `${n(f.v, 4)} — нет`));
    return {
      axis: a.axis,
      parent: n(a.parent, 4),
      sizes: a.overflow ? "слишком много различных положений блоков" : valuesText(a.sizes, a.distinct),
      fractions: fr.slice(0, 6).join("; ") || "—",
      div: a.div ? String(a.div) : "нет",
      residues: valuesText(a.residues),
      columns: [diag.columns[centre], diag.columns[size]].filter(Boolean).join(" / ") || "—",
      quantum: n(a.quantum, 6),
    };
  });
}

/** One row of the table of the candidates. */
export interface CandidateRow {
  size: string;
  corner: string;
  div: string;
  tol: string;
  aligned: string;
  current: boolean;
}

export function candidateRows(diag: Diag): CandidateRow[] {
  return diag.candidates.map((c) => ({
    size: triple(c.size),
    corner: triple(c.corner, "; "),
    div: triple(c.div),
    tol: n(Math.max(...c.tol), 6),
    aligned: percent(c.aligned),
    current: c.current,
  }));
}
