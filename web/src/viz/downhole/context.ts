import type { DownholeData, IntervalColumn, IntervalTableInfo } from "../../api";
import type { Track, TrackLabel } from "../../charts/stripLog";
import type { FieldValues } from "./histogram";
import type { DataKind } from "./styles";

/** What a section of the form knows about the layer it edits. */
export interface LayerCtx {
  /** the layer as it is drawn (a linked layer: with the settings of its style) */
  track: Track;
  table?: IntervalTableInfo;
  column?: IntervalColumn;
  tables: IntervalTableInfo[];
  numeric: boolean;
  kind: DataKind;
  /** the values of a numeric field over the set, sorted */
  values: FieldValues | null;
  /** the range the scale takes from the data */
  range: [number, number] | null;
  data: DownholeData | null;
  /** is the layer drawn as a tube along the hole */
  tube: boolean;
  /** width of the default column, m */
  panelWidth: number;
  /** changes the layer (and the other selected ones); `key` joins quick changes of one control into one undo step */
  patch: (p: Partial<Track>, key?: string) => void;
}

export type ViewKind = "bars" | "steps" | "line" | "points" | "tube" | "ribbon" | "text" | "event";

export function viewKind(t: Track, tube: boolean): ViewKind {
  if (t.type === "color" || t.type === "color_text") return tube ? "tube" : "ribbon";
  return t.type;
}

export const isGrade = (name: string, unit: string) => /^(AU|AG|PT|PD)/i.test(name) || /g\/t|ppm|ppb|г\/т/i.test(unit);

export const defaultLabel = (field: string): TrackLabel => ({ on: true, template: `{${field}}`, decimals: null, size: 12, color: "#f3f6fb", place: "outside" });

/** Are the values written beside the layer (by its label settings, or as older displays did)? */
export const labelsOn = (t: Track, displayLabels: boolean | undefined): boolean =>
  t.label ? t.label.on : (t.type === "text" || t.type === "color_text" || t.type === "event") && displayLabels !== false;

/** The width of the layer's column, m. */
export const widthOf = (t: Track, panelWidth: number): number => (t.widthM && t.widthM > 0 ? t.widthM : panelWidth * Math.max(0.3, t.width / 90));
