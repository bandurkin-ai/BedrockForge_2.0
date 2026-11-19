/**
 * The window «Построение ЦТМ» as data (ТЗ 7.20, 7.35 Д-07, Д-22; ADR 0011 §3 `model.dtm`, ADR 0014 §5): the
 * sources — layers of points and lines, collars of drillhole sets, point clouds (the classes taken, the step of
 * the thinning; `cloud.ts`) — the boundary (a closed line of a layer), the longest edge, the name and «Сразу в
 * раздел ЦТМ». Numbers are typed with a comma or a point; an empty field is «not set». Pure.
 */
import { counted } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import { cloudBodies, cloudsText, type CloudPick } from "./cloud";

export interface BuildForm {
  name: string;
  layerIds: number[];
  setIds: number[];
  /** point clouds: the classes taken and the step of the thinning */
  clouds: CloudPick[];
  /** lines are structural: their segments are edges of the surface */
  breaklines: boolean;
  /** the layer with the closed line of the boundary; null — no boundary */
  boundaryLayer: number | null;
  /** the number of the closed line in its layer, from 1; empty — the only closed line */
  boundaryLine: string;
  /** the heights of the boundary are kept (a digitised outline often lies at Z = 0) */
  boundaryZ: boolean;
  /** the longest edge, m; empty — no limit */
  maxEdge: string;
  /** «Сразу в раздел ЦТМ» */
  toDtm: boolean;
}

export const EMPTY_FORM: BuildForm = { name: "", layerIds: [], setIds: [], clouds: [], breaklines: true, boundaryLayer: null, boundaryLine: "", boundaryZ: false, maxEdge: "", toDtm: true };

/** Layers that can give the boundary: the ones with a closed line. */
export function boundaryLayers(files: readonly LineFileInfo[]): LineLayerInfo[] {
  return files.flatMap((f) => f.layers).filter((l) => !l.points_only && l.closed_count > 0);
}

/** The form when the window opens: the selected layer (or the cloud asked) is a source already; a cloud gives
 * its ground (class 2). */
export function initialForm(files: readonly LineFileInfo[], selectedLayer: number | null): BuildForm {
  const layer = selectedLayer === null ? undefined : files.flatMap((f) => f.layers).find((l) => l.id === selectedLayer);
  if (layer?.cloud) return { ...EMPTY_FORM, clouds: [{ layerId: layer.id, classes: [2], cell: "" }] };
  return { ...EMPTY_FORM, layerIds: layer ? [layer.id] : [] };
}

export interface BuildBody {
  name: string | null;
  layer_ids: number[];
  set_ids: number[];
  /** only when a cloud is a source */
  clouds?: { layer_id: number; classes: number[]; cell: number | null }[];
  breaklines: boolean;
  boundary: { layer_id: number; line: number | null; use_z: boolean } | null;
  max_edge: number | null;
  to_dtm: boolean;
}

/** The body of `POST /wireframes/build-dtm`, or why the build cannot start. */
export function buildBody(form: BuildForm, files: readonly LineFileInfo[]): { body: BuildBody | null; error: string | null } {
  const fail = (error: string) => ({ body: null, error });
  if (!form.layerIds.length && !form.setIds.length && !form.clouds.length) return fail("Не из чего строить поверхность: отметьте слои точек или линий, облака точек либо наборы скважин (их устья).");
  const clouds = cloudBodies(form.clouds, files);
  if (clouds.error) return fail(clouds.error);
  const edge = parseNumber(form.maxEdge);
  if (edge === undefined || (edge !== null && edge <= 0)) return fail("«Наибольшая длина ребра» — положительное число, например 250, или пусто — без ограничения.");
  let boundary: BuildBody["boundary"] = null;
  if (form.boundaryLayer !== null) {
    const layer = boundaryLayers(files).find((l) => l.id === form.boundaryLayer);
    if (!layer) return fail("В слое границы нет замкнутой линии: выберите слой с контуром поверхности.");
    const text = form.boundaryLine.trim();
    let line: number | null = null;
    if (text) {
      const n = parseNumber(text);
      if (typeof n !== "number" || !Number.isInteger(n) || n < 1 || n > layer.line_count) return fail(`«Номер линии границы» — целое от 1 до ${layer.line_count}, или пусто — единственная замкнутая линия слоя.`);
      line = n - 1;
    } else if (layer.closed_count > 1) return fail(`В слое «${layer.name}» замкнутых линий: ${layer.closed_count}. Укажите номер линии границы.`);
    boundary = { layer_id: layer.id, line, use_z: form.boundaryZ };
  }
  const name = form.name.trim().replace(/\s+/g, " ");
  return {
    body: {
      name: name || null,
      layer_ids: [...form.layerIds],
      set_ids: [...form.setIds],
      ...(clouds.list.length ? { clouds: clouds.list } : {}),
      breaklines: form.breaklines,
      boundary,
      max_edge: edge,
      to_dtm: form.toDtm,
    },
    error: null,
  };
}

/** «2 слоя (1 240 точек и вершин), устья 1 набора (48 скв.)» of what is checked. */
export function sourcesText(form: Pick<BuildForm, "layerIds" | "setIds"> & Partial<Pick<BuildForm, "clouds">>, files: readonly LineFileInfo[], sets: readonly { id: number; holes: number }[]): string {
  const chosen = new Set(form.layerIds);
  const layers = files.flatMap((f) => f.layers).filter((l) => chosen.has(l.id));
  const picked = sets.filter((s) => form.setIds.includes(s.id));
  const parts: string[] = [];
  if (layers.length) parts.push(`${counted(layers.length, "слой", "слоя", "слоёв")} (${counted(layers.reduce((n, l) => n + l.point_count, 0), "точка или вершина", "точки и вершины", "точек и вершин")})`);
  if (form.clouds?.length) parts.push(cloudsText(form.clouds, files));
  if (picked.length) parts.push(`устья ${counted(picked.length, "набора", "наборов", "наборов")} (${picked.reduce((n, s) => n + s.holes, 0).toLocaleString("ru-RU")} скв.)`);
  return parts.length ? parts.join(", ") : "Источники не отмечены";
}
