/**
 * What «Свойства» of an implicit model say of the stack, the faults, the offset and the veins of Э19б (ТЗ 7.35 Д-24, Д-31, Д-39): the rows of the tables made
 * from `meta` (the groups as stored, `stats` of the last build). A number the core did not give is «—», never 0 (8.0.1). The volumes of the regions of a
 * fault are the space on each side of its surface, not a rock: they are named so and never added up. Pure.
 */
import { readFaultStats, readOffsets, readStackStats, readVeinPairs, RELATION_TITLES, type VeinPair } from "./stackApi";
import { domainsOfMeta, faultsOfMeta, groupName, radiusOf, radiusText, stackLines } from "./stackModel";

const dash = "—";
const n = (v: number | null, digits = 1): string => (v === null ? dash : v.toLocaleString("ru-RU", { maximumFractionDigits: digits }));

export interface FaultRowInfo {
  id: number;
  name: string;
  /** «R_F = 200 м» / «бесконечный» as the model keeps it now */
  radius: string;
  /** the points of the data of the last build */
  points: string;
  /** «крыло 0: 17 640 000 м³ (область)» — a region of space, not a rock */
  regions: string[];
  /** the unbalance of the two sides (a share) */
  balance: string;
  residual: string;
  /** the domains this fault cuts */
  cuts: string[];
  built: boolean;
}

export function faultRows(meta: Record<string, unknown>): FaultRowInfo[] {
  const stat = new Map(readFaultStats(meta).map((f) => [f.id, f]));
  const domains = domainsOfMeta(meta);
  return faultsOfMeta(meta).map((g) => {
    const f = stat.get(g.id);
    return {
      id: g.id,
      name: g.name,
      radius: radiusText(radiusOf(g)),
      points: f ? n(f.points, 0) : dash,
      regions: f ? f.wingVolumes.map((v, i) => `крыло ${i}: ${n(v, 0)} м³ (область)`) : [],
      balance: f ? n(f.balance, 4) : dash,
      residual: f && f.maxResidual !== null ? f.maxResidual.toExponential(1).replace(".", ",") : dash,
      cuts: domains.filter((d) => d.faults.includes(g.id)).map((d) => d.name),
      built: !!f,
    };
  });
}

export interface OffsetRowInfo {
  group: string;
  wing: string;
  faults: string;
  mean: string;
  max: string;
}

/** The offset of the cut groups, one row per group and wing; a group cut by two faults has the rows of each fault below the common one. */
export function offsetRows(meta: Record<string, unknown>): OffsetRowInfo[] {
  const rows: OffsetRowInfo[] = [];
  for (const o of readOffsets(meta)) {
    const group = groupName(meta, o.group);
    const wing = o.wing === null ? dash : `крыло ${o.wing}`;
    rows.push({ group, wing, faults: o.faults.map((f) => groupName(meta, f)).join(", ") || dash, mean: o.offset ? n(o.offset.mean, 2) : dash, max: o.offset ? n(o.offset.max, 2) : dash });
    if (o.faults.length > 1) for (const f of o.faults) rows.push({ group: "", wing: "", faults: groupName(meta, f), mean: n(o.byFault[String(f)]?.mean ?? null, 2), max: n(o.byFault[String(f)]?.max ?? null, 2) });
  }
  return rows;
}

export interface PairRowInfo {
  pair: string;
  length: string;
  common: string;
}

/** The table of the intersections of the veins (`stats.vein_pairs`; Д-39): the pair, the length of the line, the volume of the common zone before the cut. */
export function pairRows(pairs: readonly VeinPair[], names?: ReadonlyMap<string, string>): PairRowInfo[] {
  const nm = (code: string) => names?.get(code) ?? code;
  return pairs.map((p) => ({ pair: `${nm(p.a)} × ${nm(p.b)}`, length: n(p.lineLength, 1), common: n(p.volumeCommon, 1) }));
}

export const pairsOf = (meta: Record<string, unknown>, group?: number | null): VeinPair[] => readVeinPairs(meta).filter((p) => group === undefined || group === null || p.group === group);

/** The words of the stack for the panel: the lines by age and what the last build dropped by the relations. */
export function stackSummary(meta: Record<string, unknown>): { lines: string[]; dropped: string | null } {
  const stats = readStackStats(meta);
  const dropped = stats && stats.droppedTotal !== null ? `По отношениям стека в последней сборке отброшено точек данных: ${n(stats.droppedTotal, 0)}.` : null;
  return { lines: stackLines(meta), dropped };
}

/** A relation as a title (a gap is «—»). */
export const relationTitle = (r: keyof typeof RELATION_TITLES | null): string => (r ? RELATION_TITLES[r] : dash);

/** The kind of an output in a word for the tables: «область крыла», «общее тело», «тело жилы», «линия» … */
export function outputKindText(o: { kind: string; region: boolean; common: boolean; code: string | null; wing: number | null }): string {
  if (o.kind === "line") return "линия пересечения";
  if (o.kind === "surface") return "поверхность";
  if (o.region) return `область крыла ${o.wing ?? ""}`.trim();
  if (o.common) return "общее тело";
  if (o.code) return `тело жилы ${o.code}`;
  return o.wing !== null ? `тело, крыло ${o.wing}` : "тело";
}
