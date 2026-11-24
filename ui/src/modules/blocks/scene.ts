/**
 * The block models in the main 3D (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19; the pattern of `trends/scene.ts` and
 * `variography/scene.ts`): every model the view of the project shows (`block_styles["<id>"].visible`) is one layer
 * `bm:<id>` (`viz/blocks/layer.ts`) of the answer `POST /viz/blocks/{id}/shell`.
 *
 * - What asks the core again: the version of the model or of its filter, the field, the range / log of the scale, the
 *   breaks, the hidden categories, the filter, the restrictor, «Оболочка / Все блоки», the limit of faces
 *   (`requestKey`). The request carries `channel` and `seq`: the core stops an older request of the model when a newer
 *   one comes, and the older one answers 409 `superseded` — it is dropped without a word (so is an answer that came
 *   after a newer request was sent; a request whose key is the one shown is not sent at all). The old layer stays on
 *   the screen until the new answer is there.
 * - What does not: the colours of the classes, the edges and the opacity (`BlocksLayer.setStyle` / `setLook`: the
 *   texels of the legend and two uniforms, no request, no new geometry, nothing sent to the GPU but 256·H texels).
 * - The click: a handler of the chain of the handles (`viz/core/handleChain.ts`, put in only while a layer is shown)
 *   asks the layers for the face under the cursor and the core for the block there (`pickBlock.ts`); the handler never
 *   takes the click, so holes and the rest are picked as before. `viz/Scene.ts` is not changed: the ray is the one of
 *   `Scene.cursorRay` (project coordinates, float64) and the layer is asked in them, with the scene origin taken as 0.
 * - A lost context: the layers are made again from the answers kept, no request.
 *
 * A lazy chunk with three.js (`bind.ts` attaches it to every scene).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { addHandles, type ChainHandler } from "../../viz/core/handleChain";
import type { ToolPointer } from "../../viz/core/toolInput";
import type { Vec3 } from "../../viz/core/vec3";
import { decodeBlocks, type BlocksData } from "../../viz/blocks/decode";
import { BlocksLayer, type BlocksPick, type BlocksShown } from "../../viz/blocks/layer";
import { defaultLook } from "../../viz/blocks/material";
import { decodeBfa1 } from "../../viz/format/bfa1";
import type { Scene } from "../../viz/Scene";
import type { PickQuery } from "../../viz/sceneTypes";
import { viewStore } from "../scene/bind";
import { blocksStore } from "./state";
import { filtersStore } from "./filters/state";
import { BLOCK_STYLES_KEY, colourIssue, dropShowInfo, legendStyleOf, lookOf, quickIssue, requestKey, revealOnShow, setShowInfo, shellRequest, showInfo, shownStyles, styleIn, type BlockStyle, type ShowInfo } from "./display";
import { lookupBlock } from "./pickBlock";
import { isAborted, isSuperseded, postShell, type ShellBody } from "./showApi";

export const blockLayerKey = (id: number): string => `bm:${id}`;

/** One channel per model and per window of the application: a request overtakes only requests of its own. */
const SESSION = Math.random().toString(36).slice(2, 8);
export const channelOf = (id: number): string => `blocks:${id}:${SESSION}`;

let counter = 0;

export type BlocksHost = Pick<Scene, "addLayer" | "removeLayer" | "invalidate" | "clipPlanes" | "onRestored" | "cursorRay" | "projectView" | "setHandles"> & Partial<Pick<Scene, "layerChanged" | "reveal">>;

interface Entry {
  id: number;
  layer: BlocksLayer | null;
  /** the answer of the layer, for a lost context and the E2E */
  data: BlocksData | null;
  /** the key of the request whose answer is in the layer */
  shown: string | null;
  /** the key of the newest request sent */
  want: string | null;
  /** the key of the request that failed: it is not sent again until something changes */
  failed: string | null;
  /** the style failed the check before any request (the text is in the state) */
  local: boolean;
  seq: number;
  abort: AbortController | null;
  legend: string;
  look: string;
  retries: number;
}

export interface BlocksSceneStats {
  /** requests sent / answers dropped (overtaken) / restyles without a request */
  requests: number;
  superseded: number;
  restyles: number;
  layers: number;
}

export class BlocksScene {
  private readonly entries = new Map<number, Entry>();
  private readonly stops: (() => void)[] = [];
  private stopHandler: (() => void) | null = null;
  private disposed = false;
  private requests = 0;
  private overtaken = 0;
  private restyles = 0;

  constructor(
    private readonly scene: BlocksHost,
    private readonly onPicked?: (model: number) => void,
  ) {
    const sync = () => this.sync();
    this.stops.push(viewStore.subscribe(sync), blocksStore.subscribe(sync), filtersStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  stats(): BlocksSceneStats {
    return { requests: this.requests, superseded: this.overtaken, restyles: this.restyles, layers: [...this.entries.values()].filter((e) => e.layer).length };
  }

  /** What the layers show now (for the E2E and the tests). */
  shown(): (BlocksShown & { key: string })[] {
    return [...this.entries.values()].flatMap((e) => (e.layer ? [{ ...e.layer.shown(), key: blockLayerKey(e.id) }] : []));
  }

  layerOf(id: number): BlocksLayer | null {
    return this.entries.get(id)?.layer ?? null;
  }

  // ------------------------------------------------------------------------------------------------ the layers

  sync(): void {
    if (this.disposed) return;
    const keys = viewStore.get();
    const styles = shownStyles(keys);
    const { list, loaded } = blocksStore.get();
    for (const id of [...this.entries.keys()]) if (!styles.has(id) || (loaded && !list.some((b) => b.id === id))) this.drop(id);
    for (const [id, style] of styles) {
      const model = list.find((b) => b.id === id);
      if (!model) continue; // the list is not read yet, or the model is gone: nothing to ask
      this.follow(id, style, model.version, model.fields);
    }
    this.handler();
  }

  private entry(id: number): Entry {
    let e = this.entries.get(id);
    if (!e) {
      e = { id, layer: null, data: null, shown: null, want: null, failed: null, local: false, seq: 0, abort: null, legend: "", look: "", retries: 0 };
      this.entries.set(id, e);
    }
    return e;
  }

  private follow(id: number, style: BlockStyle, version: number, fields: Parameters<typeof shellRequest>[1]): void {
    const e = this.entry(id);
    const model = blocksStore.get().list.find((b) => b.id === id);
    // a style the core would refuse is said here, in the same words, and not sent
    const issue = colourIssue(style.colour, model) ?? quickIssue(style.quick);
    if (issue) {
      if (!e.local || showInfo(id)?.error !== issue) setShowInfo(id, { status: "error", error: issue });
      e.local = true;
      return;
    }
    if (e.local) {
      e.local = false;
      e.want = null;
      if (e.layer && e.shown) setShowInfo(id, { status: "ready", error: null });
    }
    const body = shellRequest(style, fields);
    const fv = style.filter_id === null ? null : (filtersStore.get().rows.find((f) => f.id === style.filter_id)?.version ?? null);
    const key = requestKey(id, version, fv, body);
    if (key !== e.want) this.request(e, body, key);
    else if (e.layer && e.shown === key) this.restyle(e, style);
  }

  private request(e: Entry, body: ShellBody, key: string): void {
    e.want = key;
    e.failed = null;
    e.retries = 0;
    this.send(e, body, key);
  }

  private send(e: Entry, body: ShellBody, key: string): void {
    const mine = ++counter;
    e.seq = mine;
    e.abort?.abort();
    const ac = new AbortController();
    e.abort = ac;
    this.requests++;
    const t0 = performance.now();
    setShowInfo(e.id, { status: "loading", error: null, requests: (showInfo(e.id)?.requests ?? 0) + 1 });
    postShell(e.id, { ...body, channel: channelOf(e.id), seq: mine }, ac.signal).then(
      (buf) => this.arrived(e, mine, key, buf, t0),
      (err) => this.failedWith(e, mine, key, body, err),
    );
  }

  private current(e: Entry, mine: number): boolean {
    return !this.disposed && e.seq === mine && this.entries.get(e.id) === e;
  }

  private arrived(e: Entry, mine: number, key: string, buf: ArrayBuffer, t0: number): void {
    if (!this.current(e, mine)) return; // a newer request was sent meanwhile: this answer is stale
    let data: BlocksData;
    let warnings: string[] = [];
    try {
      data = decodeBlocks(buf);
      const w = decodeBfa1<{ warnings?: unknown }>(buf).header.warnings;
      if (Array.isArray(w)) warnings = w.filter((x): x is string => typeof x === "string");
    } catch (err) {
      e.failed = key;
      setShowInfo(e.id, { status: "error", error: errorText(err) });
      return;
    }
    e.data = data;
    e.shown = key;
    this.install(e, true);
    const info: Partial<ShowInfo> = { status: "ready", header: data.header, maxClass: data.maxClass, warnings, error: null, ms: Math.round(performance.now() - t0) };
    setShowInfo(e.id, info);
    if (revealOnShow.delete(e.id) && e.layer?.bbox) this.scene.reveal?.([e.layer.bbox]);
  }

  private failedWith(e: Entry, mine: number, key: string, body: ShellBody, err: unknown): void {
    if (!this.current(e, mine) || isAborted(err)) return;
    if (isSuperseded(err)) {
      // our own newer request cancelled it — then this one is not the newest and was dropped above; if it still is the
      // newest, someone else took our channel: ask again (twice at most) instead of staying «обновляется…» for good
      this.overtaken++;
      if (e.retries++ < 2) this.send(e, body, key);
      return;
    }
    e.failed = key;
    const text = errorText(err);
    setShowInfo(e.id, { status: "error", error: text });
  }

  /** The layer of the answer in the scene in place of the old one, with the colours and the look of the style now. */
  private install(e: Entry, fresh: boolean): void {
    const data = e.data;
    if (!data) return;
    const style = styleIn(viewStore.get(), e.id);
    const old = e.layer;
    const layer = new BlocksLayer(data, { clip: this.scene.clipPlanes }, { style: legendStyleOf(style.colour), look: { ...defaultLook(data.header.mode), ...lookOf(style) } });
    if (old) this.scene.removeLayer(blockLayerKey(e.id), false); // the scene disposes the layer it removes
    this.scene.addLayer(blockLayerKey(e.id), layer);
    e.layer = layer;
    e.legend = JSON.stringify(legendStyleOf(style.colour));
    e.look = JSON.stringify(lookOf(style));
    if (fresh) this.scene.layerChanged?.();
    this.scene.invalidate();
    this.handler();
  }

  /** New colours or look of the same faces: the texels of the legend and the uniforms, no request. */
  private restyle(e: Entry, style: BlockStyle): void {
    const layer = e.layer;
    if (!layer) return;
    const legend = JSON.stringify(legendStyleOf(style.colour));
    const look = JSON.stringify(lookOf(style));
    if (legend === e.legend && look === e.look) return;
    if (legend !== e.legend) layer.setStyle(legendStyleOf(style.colour));
    if (look !== e.look) layer.setLook({ ...defaultLook(layer.header.mode), ...lookOf(style) });
    e.legend = legend;
    e.look = look;
    this.restyles++;
    this.scene.invalidate();
  }

  private drop(id: number): void {
    const e = this.entries.get(id);
    if (!e) return;
    e.abort?.abort();
    this.entries.delete(id);
    if (e.layer) this.scene.removeLayer(blockLayerKey(id));
    dropShowInfo(id);
    revealOnShow.delete(id);
    this.scene.invalidate();
  }

  /** The context came back: the layers are made again from the answers kept (no request). */
  private restore(): void {
    for (const e of this.entries.values()) {
      e.layer = null;
      this.install(e, true);
    }
  }

  // ------------------------------------------------------------------------------------------------- the click

  /** The handler is in the chain while a layer is shown, and not otherwise (the scene is as it was without blocks). */
  private handler(): void {
    const any = [...this.entries.values()].some((e) => e.layer);
    if (any && !this.stopHandler) {
      const h: ChainHandler = {
        pointer: (p: ToolPointer) => {
          if (p.type === "click" && p.button === 0) void this.click(p.x, p.y);
          return false; // never takes it: holes and the rest are picked as before
        },
      };
      this.stopHandler = addHandles(this.scene, h);
    } else if (!any && this.stopHandler) {
      this.stopHandler();
      this.stopHandler = null;
    }
  }

  /** The face under the cursor of the nearest layer: the model and the answer of the layer. */
  pickAt(x: number, y: number): { model: number; pick: BlocksPick } | null {
    const ray = this.scene.cursorRay(x, y);
    const view = this.scene.projectView();
    if (!ray || !view) return null;
    let best: { model: number; score: number; pick: BlocksPick } | null = null;
    for (const e of this.entries.values()) {
      const layer = e.layer;
      if (!layer) continue;
      // the scene origin is taken as 0: the ray and the window are in the project, the offset of the layer is its origin
      const q: PickQuery = {
        vp: new Float64Array(16),
        vexag: 1,
        px: x,
        py: y,
        width: view.width,
        height: view.height,
        inWindow: (px, py, pz) => view.visible?.(px, py, pz) ?? true,
        ray,
        offset: [layer.origin.x, layer.origin.y, layer.origin.z] as Vec3,
      };
      const r = layer.pick(q);
      if (r && (!best || r.score < best.score)) best = { model: e.id, score: r.score, pick: r.hit as BlocksPick };
    }
    return best;
  }

  /** A click: the face, then the block of the core. Resolves when the block is read. */
  async click(x: number, y: number): Promise<void> {
    const hit = this.pickAt(x, y);
    if (!hit) return;
    this.onPicked?.(hit.model);
    await lookupBlock(hit.model, hit.pick.inside, { normal: hit.pick.normal, click: hit.pick.xyz });
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    this.stopHandler?.();
    this.stopHandler = null;
    for (const id of [...this.entries.keys()]) this.drop(id);
    this.scene.invalidate();
  }
}

declare global {
  interface Window {
    __bfBlocks?: unknown;
  }
}

/** Attaches the block models to a scene; gives what detaches them. */
export function attachBlocksScene(scene: BlocksHost, onPicked?: (model: number) => void): () => void {
  const s = new BlocksScene(scene, onPicked);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfBlocks = { shown: () => s.shown(), stats: () => s.stats(), pick: (x: number, y: number) => s.pickAt(x, y), click: (x: number, y: number) => s.click(x, y), styles: () => viewStore.get()[BLOCK_STYLES_KEY] };
  return () => {
    s.dispose();
    if (hook) delete window.__bfBlocks;
  };
}
