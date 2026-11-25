/**
 * What the slice of a block model in the main 3D knows (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, decision 19;
 * docs/progress/e17-api.md §12, §14): the plane of the style (`block_styles["<id>"].slice` = `{on, plane}`, the plane
 * «по разрезу» or an explicit `{origin, normal}` of the project), the plane of a section as the window shows it now,
 * the planes «по осям» and the move of a plane along its normal, the request to the core made of the style
 * (`sliceRequest`) and the key that tells whether a change is a new request (the plane, the field, the hidden
 * categories, the filter, the restrictor, the version of the model or of the filter) or only new colours (the range,
 * the breaks, the palette, the opacity — `SliceLayer.setColour`, no request), and the state of every slice (loading,
 * the raster, empty, failed). Pure: no React, no three.js (`sliceScene.ts` is the lazy chunk that draws).
 */
import { CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { logRequest } from "../../shell/opLog";
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { count, counted } from "../../shell/treeActions";
import type { SliceHeader } from "../../viz/blocks/sliceDecode";
import { sectionPlaneOf } from "../section/seriesState";
import type { BlockModelInfo } from "./api";
import type { Rule } from "./filters/constructor";
import { colourBody, commonRequest, type BlockStyle, type SlicePlane } from "./display";
import type { Triple } from "./showApi";

/** The side of the biggest raster the core makes (the contract: 64–4096). */
export const SLICE_MAX_PX = 4096;

export interface PlaneBody {
  origin: Triple;
  normal: Triple;
}

/** The body of `POST /viz/blocks/{id}/slice` without the overtaking keys, e17-api §12. */
export interface SliceBody {
  plane: PlaneBody;
  field?: string;
  hidden?: number[];
  filter_id?: number;
  restrict?: Rule;
  max_px: number;
}

// ------------------------------------------------------------------------------------------------------- the plane

/** The plane of the section shown now (a free line or the active series, its current section), null — no section. */
export function sectionPlaneBody(keys: Record<string, unknown>): PlaneBody | null {
  const p = sectionPlaneOf(keys).plane;
  return p ? { origin: [p.origin[0], p.origin[1], p.origin[2]], normal: [p.v[0], p.v[1], p.v[2]] } : null;
}

/** The plane of the slice by the style: «по разрезу» — the section of the view now (null while there is none). */
export function planeOf(plane: SlicePlane, keys: Record<string, unknown>): PlaneBody | null {
  if (plane === "section") return sectionPlaneBody(keys);
  return { origin: [...plane.origin], normal: [...plane.normal] };
}

/** The slice is drawn as a backdrop under the holes: only the plane of a section is. */
export const isBackdrop = (plane: SlicePlane): boolean => plane === "section";

export type Axis = "x" | "y" | "z";
export const AXES: readonly Axis[] = ["x", "y", "z"];
const AXIS_NAME: Record<Axis, string> = { x: "X", y: "Y", z: "Z" };
const unit = (a: Axis): Triple => (a === "x" ? [1, 0, 0] : a === "y" ? [0, 1, 0] : [0, 0, 1]);

/** [xmin, ymin, zmin, xmax, ymax, zmax] of the grid of the model in the local axes (the extent of the model if known). */
export function modelExtent(model: Pick<BlockModelInfo, "extent" | "grid">): number[] | null {
  if (model.extent && model.extent.length === 6 && model.extent.every((v) => Number.isFinite(v))) return model.extent;
  const g = model.grid;
  if (!g.size) return null;
  if ((g as { rotation?: number }).rotation) return null; // the grid is turned: the box of the local axes is not the extent of the model
  const size = g.size;
  return [...g.corner, ...[0, 1, 2].map((i) => g.corner[i] + g.counts[i] * size[i])];
}

/** The plane ⟂ an axis of the project through `at` (the middle of the model if none; a refusal when its extent is not known). */
export function axisPlane(axis: Axis, at: number | null, model: Pick<BlockModelInfo, "extent" | "grid">): PlaneBody {
  const e = modelExtent(model);
  if (!e && at === null) throw refusal("slice_no_extent", "Охват модели неизвестен (модель повёрнута или без регулярной сетки): плоскость нельзя поставить в её середину.", "Задайте отметку, например plane=z и at=262,5.");
  const mid = (i: number): number => (e ? (e[i] + e[i + 3]) / 2 : 0);
  const origin: Triple = [mid(0), mid(1), mid(2)];
  origin[AXES.indexOf(axis)] = at ?? mid(AXES.indexOf(axis));
  return { origin, normal: unit(axis) };
}

/** The coordinate of the plane along its axis (an axis plane), else null. */
export function axisOf(p: PlaneBody): { axis: Axis; at: number } | null {
  for (const a of AXES) {
    const i = AXES.indexOf(a);
    if (Math.abs(p.normal[i]) === 1 && p.normal[(i + 1) % 3] === 0 && p.normal[(i + 2) % 3] === 0) return { axis: a, at: p.origin[i] };
  }
  return null;
}

/** The plane moved by `d` metres along its normal (the normal is a unit vector when it came from the core; here it is made one). */
export function movePlane(p: PlaneBody, d: number): PlaneBody {
  const len = Math.hypot(...p.normal) || 1;
  const n: Triple = [p.normal[0] / len, p.normal[1] / len, p.normal[2] / len];
  return { origin: [p.origin[0] + d * n[0], p.origin[1] + d * n[1], p.origin[2] + d * n[2]], normal: p.normal };
}

/** One cell along the normal: the size of the cell along the axis of an axis plane, else the smallest side. */
export function cellStep(model: Pick<BlockModelInfo, "grid">, p: PlaneBody): number {
  const size = model.grid.size;
  if (!size) return 1;
  const a = axisOf(p);
  return a ? size[AXES.indexOf(a.axis)] : Math.min(...size);
}

/** «ось Z, отметка 262,5 м», «наклонная плоскость, нормаль (0,58; 0,58; 0,58)» — for the command log and the status bar. */
export function planeText(p: PlaneBody): string {
  const r = (v: number): string => String(Math.round(v * 100) / 100).replace(".", ",");
  const a = axisOf(p);
  if (a) return `по оси ${AXIS_NAME[a.axis]}, отметка ${r(a.at)} м`;
  const len = Math.hypot(...p.normal) || 1;
  return `наклонная, точка (${p.origin.map(r).join("; ")}), нормаль (${p.normal.map((v) => r(v / len)).join("; ")})`;
}

// ---------------------------------------------------------------------------------------------------- the request

/** The request of the slice: the plane, the field to read (the one of the colouring), the hidden codes, the filter, the restrictor. */
export function sliceRequest(style: BlockStyle, fields: BlockModelInfo["fields"] | undefined, plane: PlaneBody): SliceBody {
  const colour = colourBody(style.colour, fields);
  return {
    plane,
    ...(colour.field !== null && { field: colour.field }),
    ...(colour.hidden && colour.hidden.length > 0 && { hidden: colour.hidden }),
    ...commonRequest(style),
    max_px: SLICE_MAX_PX,
  };
}

/** What decides the raster: the same key — the same answer (the colours of the classes and the range are not in it). */
export function sliceKey(model: number, version: number, filterVersion: number | null, body: SliceBody): string {
  return JSON.stringify([model, version, filterVersion, body]);
}

/** BFA1 `blocks_slice` as it came (decode with `decodeSlice`); 409 `superseded` — a newer request of the channel took its place. */
export async function postSlice(modelId: number, body: SliceBody & { channel?: string; seq?: number }, signal?: AbortSignal): Promise<ArrayBuffer> {
  const target = coreTarget();
  const offline = () => new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  if (!target) throw offline();
  const path = `/viz/blocks/${modelId}/slice`;
  const t0 = performance.now();
  let r: Response;
  try {
    r = await fetch(apiUrl(target, path), { method: "POST", signal, headers: authHeaders(target, { "Content-Type": "application/json" }), body: JSON.stringify(body) });
  } catch (e) {
    if (signal?.aborted) throw e;
    logRequest("POST", path, body, 0, performance.now() - t0, "");
    throw offline();
  }
  if (r.ok) {
    const buf = await r.arrayBuffer();
    logRequest("POST", path, body, r.status, performance.now() - t0, `BFA1, ${buf.byteLength} байт`);
    return buf;
  }
  const text = await r.text().catch(() => "");
  logRequest("POST", path, body, r.status, performance.now() - t0, text);
  let e: Record<string, unknown> = {};
  try {
    e = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    /* not JSON */
  }
  const err = new CoreError(r.status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${r.status}`), String(e.hint ?? ""));
  err.body = e;
  throw err;
}

// ------------------------------------------------------------------------------------------------------- the state

export interface SliceInfo {
  model: number;
  /** noplane: «по разрезу», and no section is drawn; empty: the plane misses the model */
  status: "loading" | "ready" | "empty" | "noplane" | "error";
  header: SliceHeader | null;
  plane: PlaneBody | null;
  warnings: string[];
  error: string | null;
  /** ms the last request took, from the send to the layer in the scene */
  ms: number | null;
  /** requests sent for this model (the E2E and the tests count them) */
  requests: number;
}

export interface SliceState {
  models: Record<number, SliceInfo>;
}

export const sliceStore = new Store<SliceState>({ models: {} });

export const sliceInfo = (id: number): SliceInfo | undefined => sliceStore.get().models[id];

export function setSliceInfo(id: number, patch: Partial<SliceInfo>): void {
  sliceStore.set((s) => {
    const cur = s.models[id] ?? { model: id, status: "loading" as const, header: null, plane: null, warnings: [], error: null, ms: null, requests: 0 };
    return { models: { ...s.models, [id]: { ...cur, ...patch } } };
  });
}

export function dropSliceInfo(id: number): void {
  sliceStore.set((s) => {
    if (!(id in s.models)) return {};
    const models = { ...s.models };
    delete models[id];
    return { models };
  });
}

export const resetSlice = (): void => sliceStore.set({ models: {} });

const RASTER_TEXT = { cells: "ячейки", subcells: "подъячейки", sampled: "отсчёты наклонной плоскости" } as const;

/** «Срез «Ресурсная»: растр 1 528 × 1 528 пикселей (подъячейки), 33 блока на плоскости; по оси Z, отметка 262,7 м». */
export function sliceText(info: SliceInfo, name: string): string {
  const where = info.plane ? `; ${planeText(info.plane)}` : "";
  switch (info.status) {
    case "error":
      return `Срез «${name}»: ${info.error ?? "не построен"}`;
    case "noplane":
      return `Срез «${name}»: нет разреза. Нарисуйте линию разреза (вкладка «Вид») или выберите плоскость по оси.`;
    case "empty":
      return `Срез «${name}»: плоскость не пересекает блоки модели${where}`;
    default:
  }
  const h = info.header;
  if (!h) return `Срез «${name}»: запрос…`;
  const parts = [`растр ${count(h.width)} × ${count(h.height)} пикселей (${RASTER_TEXT[h.raster]})`, counted(h.passed, "блок", "блока", "блоков") + " на плоскости"];
  if (info.status === "loading") parts.push("обновляется…");
  return `Срез «${name}»: ${parts.join(", ")}${where}`;
}

/** The lines of the status bar, one per slice; none — null. */
export function sliceStatus(models: readonly Pick<BlockModelInfo, "id" | "name">[], s: SliceState = sliceStore.get()): string | null {
  const lines = Object.values(s.models).map((m) => sliceText(m, models.find((b) => b.id === m.model)?.name ?? `№${m.model}`));
  return lines.length ? lines.join(" · ") : null;
}
