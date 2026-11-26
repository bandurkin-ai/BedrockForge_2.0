/**
 * The client of the stack of relations, the faults and the systems of veins (Э19б; ТЗ 7.35 Д-24, Д-31, Д-39; ADR 0026 решения 15, 19–21, 22;
 * docs/progress/e19-api.md §10): the four routes of `/api/v1/implicit` and the readers of the numbers the build writes into `meta.stats`. Every
 * answer is read by a reader that keeps a gap as `null` — never 0 (8.0.1). The core names the relations `conformable`, `erode`, `onlap`,
 * `intrusion` (the words of the ТЗ: «Согласно», «Эрозия», «Налегание», «Интрузия»). No React.
 */
import { api } from "../../shell/api";
import { isNum, numOrNull, obj, str } from "../estimation/api";
import { IMPLICIT_API, readGroup, readSaved, type GroupSaved, type Saved } from "./api";

// ---- the relations of the stack ----

export type Relation = "conformable" | "erode" | "onlap" | "intrusion";
export const RELATIONS: readonly Relation[] = ["conformable", "erode", "onlap", "intrusion"];
export const RELATION_TITLES: Record<Relation, string> = { conformable: "Согласно", erode: "Эрозия", onlap: "Налегание", intrusion: "Интрузия" };
/** What a relation does — the group that carries it is the YOUNGER one, the others are those below it in the list (older). */
export const RELATION_HELP: Record<Relation, string> = {
  conformable: "Слои лежат друг на друге без срезов: при перекрытии побеждает более молодая группа. Данные старых групп не отбрасываются.",
  erode: "Молодая поверхность срезает более старые группы: их данные внутри тела молодой группы и на её поверхности отбрасываются перед решением, кровля старых обрезается.",
  onlap: "Молодая группа ложится на более старую поверхность: её данные ниже старой поверхности отбрасываются, старое тело остаётся целым и побеждает в перекрытии.",
  intrusion: "Молодая группа — замкнутое тело, которое замещает объёмы всех более старых групп: их данные строго внутри тела отбрасываются; само тело срезается более молодыми группами.",
};

export const relationOf = (v: unknown): Relation => (v === "erode" || v === "onlap" || v === "intrusion" ? v : "conformable");

/** A relation as a person or the assistant writes it: the word of the core, the Russian word (any case), the English «erosion». */
export function parseRelation(text: unknown): Relation | null {
  const t = String(text ?? "").trim().toLowerCase();
  if (!t) return null;
  const hit = RELATIONS.find((r) => r === t || RELATION_TITLES[r].toLowerCase() === t);
  if (hit) return hit;
  return t === "erosion" ? "erode" : null;
}

/** `PUT /models/{id}/relations`: `order` — every group of the stack (all but the faults), the youngest first; `relations` — «group id → relation». */
export const putRelations = async (id: number, version: number, order: number[], relations: Record<string, Relation>): Promise<Saved> =>
  readSaved(await api.put(`${IMPLICIT_API}/models/${id}/relations`, { expected_version: version, order, relations }));

/** `PUT /models/{id}/groups/{gid}/faults`: the faults that cut a domain (only the domains are cut, only a group of type `fault` cuts). */
export const putGroupFaults = async (id: number, groupId: number, version: number, faultIds: number[]): Promise<Saved> =>
  readSaved(await api.put(`${IMPLICIT_API}/models/${id}/groups/${groupId}/faults`, { expected_version: version, fault_ids: faultIds }));

// ---- the faults, the systems of veins ----

export interface ManualLayer {
  layer_id: number;
  role: "contact" | "inside" | "outside";
}

/** `POST /models/{id}/faults`: a fault, added last; without codes the hand layers (`manual`) are the data — else 422. */
export const postFault = async (id: number, version: number, group: Record<string, unknown>, manual: ManualLayer[]): Promise<GroupSaved> => {
  const r = obj(await api.post(`${IMPLICIT_API}/models/${id}/faults`, { expected_version: version, group, ...(manual.length ? { manual } : {}) }));
  return { ...readSaved(r), group: readGroup(r.group) };
};

/** `POST /models/{id}/vein_system`: 2–20 veins, the order is the age (the first is the youngest). */
export const postVeinSystem = async (id: number, version: number, group: Record<string, unknown>): Promise<GroupSaved> => {
  const r = obj(await api.post(`${IMPLICIT_API}/models/${id}/vein_system`, { expected_version: version, group }));
  return { ...readSaved(r), group: readGroup(r.group) };
};

// ---- what the build wrote into `meta.stats` (Э19б) ----

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const stats = (meta: Record<string, unknown>): Record<string, unknown> => (meta.stats === null || meta.stats === undefined ? {} : obj(meta.stats));

export interface DroppedPoints {
  group: number | null;
  by: number | null;
  relation: Relation;
  points: number | null;
  of: number | null;
}

export interface StackStats {
  order: number[];
  relations: Record<string, Relation>;
  dropped: DroppedPoints[];
  droppedTotal: number | null;
}

/** `stats.stack` of the last build; null — the model was built without a stack (or not built). */
export function readStackStats(meta: Record<string, unknown>): StackStats | null {
  const raw = stats(meta).stack;
  if (raw === null || raw === undefined) return null;
  const s = obj(raw);
  const relations: Record<string, Relation> = {};
  for (const [k, v] of Object.entries(obj(s.relations))) relations[k] = relationOf(v);
  return {
    order: arr(s.order).filter(isNum),
    relations,
    dropped: arr(s.dropped).map((d) => {
      const x = obj(d);
      return { group: numOrNull(x.group), by: numOrNull(x.by), relation: relationOf(x.relation), points: numOrNull(x.points), of: numOrNull(x.of) };
    }),
    droppedTotal: numOrNull(s.dropped_total),
  };
}

export interface FaultStat {
  id: number;
  /** null — an infinite fault */
  radius: number | null;
  points: number | null;
  /** the volumes of the two regions (the wings 0 and 1): the space on each side, not rocks */
  wingVolumes: (number | null)[];
  balance: number | null;
  maxResidual: number | null;
}

/** `stats.faults {fid: {radius, points, wing_volumes, balance, max_residual}}`. */
export function readFaultStats(meta: Record<string, unknown>): FaultStat[] {
  return Object.entries(obj(stats(meta).faults))
    .filter(([k]) => /^\d+$/.test(k))
    .map(([k, v]) => {
      const f = obj(v);
      return { id: Number(k), radius: numOrNull(f.radius), points: numOrNull(f.points), wingVolumes: arr(f.wing_volumes).map(numOrNull), balance: numOrNull(f.balance), maxResidual: numOrNull(f.max_residual) };
    })
    .sort((a, b) => a.id - b.id);
}

export interface Offset {
  mean: number | null;
  max: number | null;
}

export interface GroupOffset {
  group: number;
  /** the wing the numbers are of; null — the group is not cut */
  wing: number | null;
  faults: number[];
  offset: Offset | null;
  byFault: Record<string, Offset>;
}

const offsetOf = (v: unknown): Offset | null => (v === null || v === undefined ? null : { mean: numOrNull(obj(v).mean), max: numOrNull(obj(v).max) });

/** The offset of the cut groups, `stats.by_group[].offset` / `offset_by_fault` (the result of the difference of the fields, read only). */
export function readOffsets(meta: Record<string, unknown>): GroupOffset[] {
  const out: GroupOffset[] = [];
  for (const raw of arr(stats(meta).by_group)) {
    const g = obj(raw);
    const group = numOrNull(g.group);
    const offset = offsetOf(g.offset);
    if (group === null || !offset) continue;
    const byFault: Record<string, Offset> = {};
    for (const [k, v] of Object.entries(obj(g.offset_by_fault))) {
      const o = offsetOf(v);
      if (o) byFault[k] = o;
    }
    out.push({ group, wing: numOrNull(g.wing), faults: arr(g.faults).filter(isNum), offset, byFault });
  }
  return out;
}

export interface VeinPair {
  /** the id of the system */
  group: number | null;
  a: string;
  b: string;
  /** the length of the line of intersection, m */
  lineLength: number | null;
  /** the volume of the common zone BEFORE the younger vein cut the older one, m³ */
  volumeCommon: number | null;
}

/** `stats.vein_pairs`: the table of the intersections of the veins (Д-39). */
export function readVeinPairs(meta: Record<string, unknown>): VeinPair[] {
  return arr(stats(meta).vein_pairs).map((p) => {
    const x = obj(p);
    return { group: numOrNull(x.group), a: str(x.a) ?? "", b: str(x.b) ?? "", lineLength: numOrNull(x.line_length), volumeCommon: numOrNull(x.volume_common) };
  });
}
