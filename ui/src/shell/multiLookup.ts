/**
 * Where «Свойства» of several objects read the values of the fields from (Д-44 А5): the lists and the settings of the view
 * of every module that has a field the registry can set (`multiPropsModel.ts`). The values are read from the stores, not
 * from the page: the panel draws again when a store changes. No React: the panel passes the stores' states.
 */
import { styleIn } from "../modules/blocks/display";
import { pointsShown, type ViewState as ImplicitView } from "../modules/implicit/viewState";
import type { PlannedRow } from "../modules/drillholes/plannedApi";
import { isShown as plannedShown, type PlannedView } from "../modules/drillholes/plannedState";
import { setStyle } from "../modules/scene/SetStyleWindow";
import { lineLayers, type SceneObjects } from "../modules/scene/objects";
import { shownSwaths, SWATHS_KEY } from "../modules/scene/viewSync";
import type { SetInfo } from "../modules/tables/state";
import { ELLIPSOIDS_KEY, shownObjects } from "../modules/trends/state";
import type { Trend } from "../modules/trends/api";
import type { ViewRow } from "../modules/views/state";
import { itemExists } from "../modules/wireframes/named";
import type { FieldValue, Lookup, MultiField } from "./multiPropsModel";
import { isObjectMulti, unpair, type PickKind } from "./selection";

/** The lists of the sections without `object.select` that have a field (the stores of their modules). */
export interface Lists {
  sets: readonly SetInfo[];
  trends: readonly Trend[];
  planned: readonly PlannedRow[];
  swaths: readonly { id: number }[];
  views: readonly ViewRow[];
  /** the models of the section «Неявные модели» with the groups of their data */
  implicit: readonly { id: number; group_list: readonly { id: number | null }[] }[];
}

export const NO_LISTS: Lists = { sets: [], trends: [], planned: [], swaths: [], views: [], implicit: [] };

/** What the lookup reads: the states of the stores the panel is subscribed to. */
export interface LookupEnv {
  hidden: readonly string[];
  objects: SceneObjects;
  /** the keys of the project view (`viewStore`) */
  view: Record<string, unknown>;
  planned: PlannedView;
  /** the points of implicit models shown in 3D */
  implicit?: ImplicitView;
  lists: Lists;
}

/** Is the row in the list of its section (kinds without a field have no list here: they are drawn, so they are there). */
export function rowExists(lists: Lists, kind: PickKind, id: number): boolean {
  switch (kind) {
    case "set":
      return lists.sets.some((x) => x.id === id);
    case "trend":
      return lists.trends.some((x) => x.id === id);
    case "planned":
      return lists.planned.some((x) => x.id === id);
    case "swath":
      return lists.swaths.some((x) => x.id === id);
    case "view":
      return lists.views.some((x) => x.id === id);
    case "implicit_group": {
      const [model, group] = unpair(id);
      return lists.implicit.some((m) => m.id === model && m.group_list.some((g) => g.id === group));
    }
    default:
      return true;
  }
}

/** The value of a field of a row of a section without `object.select`; `undefined` — the row is gone or has no such field. */
function otherValue(env: LookupEnv, lists: Lists, kind: PickKind, id: number, key: MultiField["key"]): FieldValue | undefined {
  if (!rowExists(lists, kind, id)) return undefined;
  if (kind === "set") {
    if (key === "visible") return !env.hidden.includes(`set:${id}`);
    if (key === "traceColor") return setStyle("traces", id, env.view).color;
    if (key === "traceWidth") return setStyle("traces", id, env.view).width;
    if (key === "collarSize") return setStyle("collars", id, env.view).size;
  }
  if (key === "visible") {
    if (kind === "trend") return shownObjects(env.view[ELLIPSOIDS_KEY]).includes(id);
    if (kind === "planned") return plannedShown(env.planned, id);
    if (kind === "swath") return shownSwaths(env.view[SWATHS_KEY]).includes(id);
  }
  if (kind === "implicit_group" && key === "visible") {
    const [model, group] = unpair(id);
    return env.implicit ? pointsShown(env.implicit, model, group) : false;
  }
  if (kind === "view" && key === "folder") return lists.views.find((x) => x.id === id)?.folder ?? "";
  return undefined;
}

export function buildLookup(env: LookupEnv): Lookup {
  const lists = env.lists;
  const layers = new Map(lineLayers(env.objects).map((l) => [l.id, l]));
  return {
    exists: (kind, id) => (isObjectMulti(kind) ? itemExists(kind, id) : rowExists(lists, kind, id)),
    hidden: env.hidden,
    blockShown: (id) => styleIn(env.view, id).visible,
    blockOpacity: (id) => styleIn(env.view, id).opacity,
    layerColor: (id) => layers.get(id)?.color,
    wireframeColor: (id) => env.objects.wireframes.find((w) => w.id === id)?.color,
    rasterOpacity: (id) => env.objects.rasters.find((r) => r.id === id)?.opacity,
    other: (kind, id, key) => otherValue(env, lists, kind, id, key),
  };
}
