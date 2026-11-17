/**
 * What the 3D layer of the displays tells the window (ТЗ 7.16): the legend — every display drawn, its layers
 * with the number of intervals in each class — the share of parts shown simplified (LOD 4 / 32 m) and loads
 * under way. No three.js: the legend and the status bar read it from the main bundle.
 */
import { Store } from "../../shell/store";
import type { LegendEntry } from "../../viz/downhole/geometry";

export interface LegendDisplay {
  key: string;
  chartId: number | null;
  /** the window of the form drawing it, for a draft */
  draftKey: string | null;
  name: string;
  setId: number;
  holes: number;
  intervals: number;
  entries: LegendEntry[];
}

export interface DisplaysUi {
  legend: LegendDisplay[];
  /** share of the parts in view drawn at a coarser level, 0…1 («упрощено») */
  simplified: number;
  loading: number;
  /** the video memory budget refused a part */
  over: boolean;
}

export const displaysUi = new Store<DisplaysUi>({ legend: [], simplified: 0, loading: 0, over: false });
