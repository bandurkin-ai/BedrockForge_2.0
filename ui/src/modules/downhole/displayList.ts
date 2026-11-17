/**
 * Which displays along holes the 3D view draws (ТЗ 7.16, ADR 0007 §2–§3): the saved displays switched on in
 * the view key `displays`, in its order, with their linked styles resolved from the library; a form's draft
 * with `chartId` is drawn instead of that saved display, a draft without it — in addition. Nothing of a set
 * whose `set:<id>` or `set:<id>:intervals` is hidden. Also the fields to load per set, and where each display
 * of a set starts beside the holes (displays of one set stand side by side). Pure.
 */
import { withDefaults, type StripLogSettings } from "../../charts/striplog/model";
import { displayFields, stackOf, type StackStart } from "../../viz/downhole/geometry";
import { resolveTracks, type StyleRec } from "../../viz/downhole/styles";
import { displayEntries } from "./displays";

export interface ShownDisplay {
  /** `chart:<id>` or `draft:<window>` */
  key: string;
  setId: number;
  chartId: number | null;
  /** the window of the form drawing it (a draft) */
  draftKey: string | null;
  name: string;
  settings: StripLogSettings;
  /** where it starts beside the holes, after the displays of the set before it */
  start: StackStart;
}

export interface DisplayInputs {
  displays: unknown;
  charts: readonly { id: number; set_id: number; name: string; settings: StripLogSettings }[];
  drafts: readonly { key: string; setId: number; chartId: number | null; settings: StripLogSettings; name?: string }[];
  styles: StyleRec[];
  hidden: readonly string[];
}

const drawable = (s: StripLogSettings) => s.tracks.some((t) => t.visible !== false) && (s.allHoles === true || s.holes.length > 0);

export function shownDisplays(x: DisplayInputs): ShownDisplay[] {
  const hidden = new Set(x.hidden);
  const off = (setId: number) => hidden.has(`set:${setId}`) || hidden.has(`set:${setId}:intervals`);
  const draftOf = new Map(x.drafts.filter((d) => d.chartId !== null).map((d) => [d.chartId as number, d]));
  const out: Omit<ShownDisplay, "start">[] = [];
  for (const e of displayEntries(x.displays)) {
    if (!e.visible) continue;
    const c = x.charts.find((y) => y.id === e.chart);
    if (!c) continue;
    const d = draftOf.get(c.id);
    out.push({ key: `chart:${c.id}`, setId: c.set_id, chartId: c.id, draftKey: d?.key ?? null, name: c.name, settings: d ? d.settings : resolveTracks(withDefaults(c.settings), x.styles) });
  }
  for (const d of x.drafts) {
    if (d.chartId !== null && out.some((o) => o.chartId === d.chartId)) continue;
    const c = d.chartId !== null ? x.charts.find((y) => y.id === d.chartId) : undefined;
    out.push({ key: d.chartId !== null ? `chart:${d.chartId}` : `draft:${d.key}`, setId: d.setId, chartId: d.chartId, draftKey: d.key, name: c?.name ?? d.name ?? `Новое: ${d.settings.tracks.filter((t) => t.visible !== false).map((t) => t.field).join(", ")}`, settings: d.settings });
  }
  const taken = new Map<number, StackStart>();
  const shown: ShownDisplay[] = [];
  for (const o of out) {
    if (off(o.setId) || !drawable(o.settings)) continue;
    const start = taken.get(o.setId) ?? { units: 0, tubes: 0 };
    taken.set(o.setId, stackOf(o.settings, start));
    shown.push({ ...o, start });
  }
  return shown;
}

/** Fields to load per set and table: everything the shown displays of the set use (sorted, one request). */
export function fieldsOfSets(shown: readonly ShownDisplay[]): Map<number, Record<string, string[]>> {
  const out = new Map<number, Record<string, string[]>>();
  for (const d of shown) {
    const acc = out.get(d.setId) ?? {};
    for (const [table, fields] of Object.entries(displayFields(d.settings))) {
      const list = (acc[table] ??= []);
      for (const f of fields) if (!list.some((g) => g.toUpperCase() === f.toUpperCase())) list.push(f);
    }
    out.set(d.setId, acc);
  }
  for (const acc of out.values()) for (const k of Object.keys(acc)) acc[k].sort();
  return out;
}

/** Width of the default panel column from the lengths of all the holes of the set (not the loaded ones). */
export function panelWidthOf(lengths: ArrayLike<number>): number {
  const l = Array.from(lengths).filter((v) => v > 0).sort((a, b) => a - b);
  const median = l.length ? l[l.length >> 1] : 100;
  return Math.min(40, Math.max(2, Math.round(median / 20)));
}
