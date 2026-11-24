/**
 * The slice of block models in the main 3D (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, decision 19; the pattern of
 * `scene.ts`): every model whose style says `slice.on` is one layer `bms:<id>` (`viz/blocks/sliceLayer.ts`) of the
 * answer `POST /viz/blocks/{id}/slice` — a textured rectangle on the plane, independent of the shell of the model.
 *
 * - The plane: an explicit `{origin, normal}` of the project, or «по разрезу» — the plane of the section shown now (a
 *   free line, or the current section of the active series: every step of the series is a new plane and a new
 *   request). That slice is the backdrop of the section: under the holes, the lines and the wireframes. No section —
 *   no plane: the state says so, nothing is asked.
 * - What asks the core again (`sliceKey`): the plane, the field of the colouring, the hidden categories, the filter,
 *   the restrictor, the version of the model or of the filter. The request carries `channel` and `seq`: a newer
 *   request of the model overtakes the older one — it answers 409 `superseded` and is dropped without a word, so is
 *   an answer that came after a newer request was sent. The old layer stays until the new answer is there.
 * - What does not: the range and the log of the scale, the breaks, the colours, the opacity (`SliceLayer.setColour`,
 *   `setLook`: the texels are baked again from the answer kept, no request).
 * - A lost context: the layers are made again from the answers kept, no request.
 *
 * A lazy chunk with three.js (`sliceCommands.ts` attaches it to every scene).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { decodeSlice, type SliceData } from "../../viz/blocks/sliceDecode";
import { resolveSliceColour, SliceLayer, type SliceColour, type SliceShown } from "../../viz/blocks/sliceLayer";
import type { Scene } from "../../viz/Scene";
import { viewStore } from "../scene/bind";
import { seriesStore } from "../section/seriesState";
import { blocksStore } from "./state";
import { filtersStore } from "./filters/state";
import { BLOCK_STYLES_KEY, blocksShowStore, colourIssue, legendStyleOf, quickIssue, readStyle, styleIn, type BlockStyle } from "./display";
import { isAborted, isSuperseded } from "./showApi";
import { dropSliceInfo, isBackdrop, planeOf, postSlice, setSliceInfo, sliceInfo, sliceKey, sliceRequest, type PlaneBody, type SliceBody } from "./sliceState";

export const sliceLayerKey = (id: number): string => `bms:${id}`;

/** One channel per model and per window of the application: a request overtakes only requests of its own. */
const SESSION = Math.random().toString(36).slice(2, 8);
export const sliceChannelOf = (id: number): string => `blocks:${id}:slice:${SESSION}`;

let counter = 0;

export type SliceHost = Pick<Scene, "addLayer" | "removeLayer" | "invalidate" | "clipPlanes" | "onRestored"> & Partial<Pick<Scene, "layerChanged">>;

interface Entry {
  id: number;
  layer: SliceLayer | null;
  /** the answer of the layer, for a lost context, the colours and the E2E */
  data: SliceData | null;
  shown: string | null;
  want: string | null;
  failed: string | null;
  /** the style failed the check before any request (the text is in the state) */
  local: boolean;
  seq: number;
  abort: AbortController | null;
  retries: number;
  /** the inputs of the resolved colouring and its result (the range reads the whole raster once per change of them) */
  colourIn: string;
  colour: SliceColour | null;
  /** the colours, the range and the look of the layer, to tell a restyle from nothing */
  look: string;
}

export interface SliceSceneStats {
  requests: number;
  superseded: number;
  restyles: number;
  layers: number;
}

/** The models whose style turns the slice on: id → style (the shell of the model may be hidden). */
export function slicedStyles(keys: Record<string, unknown>): Map<number, BlockStyle> {
  const map = keys[BLOCK_STYLES_KEY];
  const out = new Map<number, BlockStyle>();
  if (!map || typeof map !== "object" || Array.isArray(map)) return out;
  for (const [k, v] of Object.entries(map)) {
    if (!/^\d+$/.test(k)) continue;
    const style = readStyle(v);
    if (style.slice.on) out.set(Number(k), style);
  }
  return out;
}

/** The range the shell of the same model resolved for the same field, when the style leaves the range open. */
function shellRange(id: number, style: BlockStyle): { from: number; to: number } | null {
  const h = blocksShowStore.get().models[id]?.header;
  if (!h || h.field !== style.colour.field || h.colour.kind !== "scale") return null;
  const { from, to } = h.colour;
  return typeof from === "number" && typeof to === "number" ? { from, to } : null;
}

export class SliceScene {
  private readonly entries = new Map<number, Entry>();
  private readonly stops: (() => void)[] = [];
  private disposed = false;
  private requests = 0;
  private overtaken = 0;
  private restyles = 0;

  constructor(private readonly scene: SliceHost) {
    const sync = () => this.sync();
    this.stops.push(viewStore.subscribe(sync), blocksStore.subscribe(sync), filtersStore.subscribe(sync), seriesStore.subscribe(sync), blocksShowStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  stats(): SliceSceneStats {
    return { requests: this.requests, superseded: this.overtaken, restyles: this.restyles, layers: [...this.entries.values()].filter((e) => e.layer).length };
  }

  /** What the layers show now (for the E2E and the tests). */
  shown(): (SliceShown & { key: string })[] {
    return [...this.entries.values()].flatMap((e) => (e.layer ? [{ ...e.layer.shown(), key: sliceLayerKey(e.id) }] : []));
  }

  layerOf(id: number): SliceLayer | null {
    return this.entries.get(id)?.layer ?? null;
  }

  sync(): void {
    if (this.disposed) return;
    const keys = viewStore.get();
    const styles = slicedStyles(keys);
    const { list, loaded } = blocksStore.get();
    for (const id of [...this.entries.keys()]) if (!styles.has(id) || (loaded && !list.some((b) => b.id === id))) this.drop(id);
    for (const [id, style] of styles) {
      const model = list.find((b) => b.id === id);
      if (!model) continue; // the list is not read yet, or the model is gone: nothing to ask
      this.follow(id, style, model.version, model.fields, planeOf(style.slice.plane, keys));
    }
  }

  private entry(id: number): Entry {
    let e = this.entries.get(id);
    if (!e) {
      e = { id, layer: null, data: null, shown: null, want: null, failed: null, local: false, seq: 0, abort: null, retries: 0, colourIn: "", colour: null, look: "" };
      this.entries.set(id, e);
    }
    return e;
  }

  private follow(id: number, style: BlockStyle, version: number, fields: Parameters<typeof sliceRequest>[1], plane: PlaneBody | null): void {
    const e = this.entry(id);
    const model = blocksStore.get().list.find((b) => b.id === id);
    if (!plane) {
      // «по разрезу» and no section: no plane, the layer of the last one goes
      e.abort?.abort();
      e.want = null;
      e.shown = null;
      e.data = null;
      this.removeLayer(e);
      if (sliceInfo(id)?.status !== "noplane") setSliceInfo(id, { status: "noplane", header: null, plane: null, error: null, warnings: [] });
      return;
    }
    // a style the core would refuse is said here, in the same words, and not sent
    const issue = colourIssue(style.colour, model) ?? quickIssue(style.quick);
    if (issue) {
      if (!e.local || sliceInfo(id)?.error !== issue) setSliceInfo(id, { status: "error", error: issue, plane });
      e.local = true;
      return;
    }
    if (e.local) {
      e.local = false;
      e.want = null;
    }
    const body = sliceRequest(style, fields, plane);
    const fv = style.filter_id === null ? null : (filtersStore.get().rows.find((f) => f.id === style.filter_id)?.version ?? null);
    const key = sliceKey(id, version, fv, body);
    if (key !== e.want) this.request(e, body, key);
    else if (e.data && e.shown === key) this.restyle(e, style);
  }

  private request(e: Entry, body: SliceBody, key: string): void {
    e.want = key;
    e.failed = null;
    e.retries = 0;
    this.send(e, body, key);
  }

  private send(e: Entry, body: SliceBody, key: string): void {
    const mine = ++counter;
    e.seq = mine;
    e.abort?.abort();
    const ac = new AbortController();
    e.abort = ac;
    this.requests++;
    const t0 = performance.now();
    setSliceInfo(e.id, { status: "loading", error: null, plane: body.plane, requests: (sliceInfo(e.id)?.requests ?? 0) + 1 });
    postSlice(e.id, { ...body, channel: sliceChannelOf(e.id), seq: mine }, ac.signal).then(
      (buf) => this.arrived(e, mine, key, body, buf, t0),
      (err) => this.failedWith(e, mine, key, body, err),
    );
  }

  private current(e: Entry, mine: number): boolean {
    return !this.disposed && e.seq === mine && this.entries.get(e.id) === e;
  }

  private arrived(e: Entry, mine: number, key: string, body: SliceBody, buf: ArrayBuffer, t0: number): void {
    if (!this.current(e, mine)) return; // a newer request was sent meanwhile: this answer is stale
    let data: SliceData;
    try {
      data = decodeSlice(buf);
    } catch (err) {
      e.failed = key;
      setSliceInfo(e.id, { status: "error", error: errorText(err) });
      return;
    }
    e.data = data;
    e.shown = key;
    e.colourIn = "";
    if (data.empty) this.removeLayer(e);
    else this.install(e);
    setSliceInfo(e.id, { status: data.empty ? "empty" : "ready", header: data.header, plane: body.plane, warnings: data.header.warnings, error: null, ms: Math.round(performance.now() - t0) });
  }

  private failedWith(e: Entry, mine: number, key: string, body: SliceBody, err: unknown): void {
    if (!this.current(e, mine) || isAborted(err)) return;
    if (isSuperseded(err)) {
      // our own newer request cancelled it — then this one is not the newest and was dropped above; if it still is the
      // newest, someone else took our channel: ask again (twice at most) instead of staying «обновляется…» for good
      this.overtaken++;
      if (e.retries++ < 2) this.send(e, body, key);
      return;
    }
    e.failed = key;
    setSliceInfo(e.id, { status: "error", error: errorText(err) });
  }

  /** The colouring of the style for the raster kept: the range is read from the raster once per change of its inputs. */
  private colourOf(e: Entry, style: BlockStyle): SliceColour | null {
    const data = e.data;
    if (!data) return null;
    const fallback = shellRange(e.id, style);
    const c = style.colour;
    const input = JSON.stringify([c.kind, c.from, c.to, c.log, c.breaks, fallback]);
    if (e.colour && e.colourIn === input) return e.colour;
    e.colour = resolveSliceColour(data, { kind: c.kind, from: c.from, to: c.to, log: c.log, breaks: c.breaks }, fallback);
    e.colourIn = input;
    return e.colour;
  }

  /** The layer of the answer in the scene in place of the old one, with the colours and the look of the style now. */
  private install(e: Entry): void {
    const data = e.data;
    if (!data || data.empty) return;
    const style = styleIn(viewStore.get(), e.id);
    const colour = this.colourOf(e, style);
    if (!colour) return;
    const look = { opacity: style.opacity, backdrop: isBackdrop(style.slice.plane) };
    const layer = new SliceLayer(data, { clip: this.scene.clipPlanes }, { colour, style: legendStyleOf(style.colour), look });
    if (e.layer) this.scene.removeLayer(sliceLayerKey(e.id), false); // the scene disposes the layer it removes
    this.scene.addLayer(sliceLayerKey(e.id), layer);
    e.layer = layer;
    e.look = JSON.stringify([colour, legendStyleOf(style.colour), look]);
    this.scene.layerChanged?.();
    this.scene.invalidate();
  }

  /** New colours, range or look of the same raster: the texels baked again, no request. */
  private restyle(e: Entry, style: BlockStyle): void {
    const layer = e.layer;
    if (!layer) return;
    const colour = this.colourOf(e, style);
    if (!colour) return;
    const legend = legendStyleOf(style.colour);
    const look = { opacity: style.opacity, backdrop: isBackdrop(style.slice.plane) };
    const sig = JSON.stringify([colour, legend, look]);
    if (sig === e.look) return;
    layer.setColour(colour, legend);
    layer.setLook(look);
    e.look = sig;
    this.restyles++;
    this.scene.invalidate();
  }

  private removeLayer(e: Entry): void {
    if (!e.layer) return;
    e.layer = null;
    this.scene.removeLayer(sliceLayerKey(e.id));
    this.scene.invalidate();
  }

  private drop(id: number): void {
    const e = this.entries.get(id);
    if (!e) return;
    e.abort?.abort();
    this.entries.delete(id);
    if (e.layer) this.scene.removeLayer(sliceLayerKey(id));
    dropSliceInfo(id);
    this.scene.invalidate();
  }

  /** The context came back: the layers are made again from the answers kept (no request). */
  private restore(): void {
    for (const e of this.entries.values()) {
      if (!e.data || e.data.empty) continue;
      e.layer = null;
      e.colourIn = "";
      this.install(e);
    }
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    for (const id of [...this.entries.keys()]) this.drop(id);
    this.scene.invalidate();
  }
}

declare global {
  interface Window {
    __bfBlocksSlice?: unknown;
  }
}

/** Attaches the slices of the block models to a scene; gives what detaches them. */
export function attachSliceScene(scene: SliceHost): () => void {
  const s = new SliceScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfBlocksSlice = { shown: () => s.shown(), stats: () => s.stats(), styles: () => viewStore.get()[BLOCK_STYLES_KEY] };
  return () => {
    s.dispose();
    if (hook) delete window.__bfBlocksSlice;
  };
}
