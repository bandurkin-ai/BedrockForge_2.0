/**
 * The shadow of the active series (ТЗ 8.1.3; ADR 0016 §5): the contours of the neighbouring sections by the mode of
 * the series (`params.shadow`: none, prev, next, both) — `GET /section-series/{id}/contours?k=…` (one request for
 * k − 1 and k + 1) with `If-None-Match` → the scene layer `shadow` (`viz/section/shadow.ts`). The request is made
 * again when the section, the mode, the series or the version of its layer of contours change: after a save of a
 * contour the layer has a new version, the core a new ETag and the shadow of the neighbour follows; the same
 * contours are a 304 and the layer stays. No series, no contour layer or the mode «нет» — no layer (and no ETag:
 * a 304 would leave it without lines). No React.
 */
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { Scene } from "../../viz/Scene";
import { bfa1Array, decodeBfa1 } from "../../viz/format/bfa1";
import type { ShadowLayer, ShadowLines } from "../../viz/section/shadow";
import { viewStore } from "../scene/bind";
import { lineLayers, sceneObjects } from "../scene/objects";
import { onScene } from "../scene/sceneHost";
import type { ShadowMode } from "./seriesApi";
import { currentSection, seriesStore, type CurrentSection } from "./seriesState";

export const SHADOW_KEY = "shadow";

/** The sections the shadow shows for section k of K: k − 1 and (or) k + 1 inside the series. */
export function shadowSections(mode: ShadowMode | null | undefined, k: number, count: number): number[] {
  const out: number[] = [];
  if ((mode === "prev" || mode === "both") && k - 1 >= 0) out.push(k - 1);
  if ((mode === "next" || mode === "both") && k + 1 < count) out.push(k + 1);
  return out;
}

/** What the shadow asks for now: the path and its signature (with the version of the layer of contours); null — none. */
export function shadowRequest(cur: CurrentSection | null, layerVersion: (id: number) => number | null): { path: string; sig: string } | null {
  if (!cur || cur.seg !== null) return null;
  const layer = cur.row.params.contour_layer;
  const version = typeof layer === "number" ? layerVersion(layer) : null;
  if (version === null) return null;
  const ks = shadowSections(cur.row.params.shadow, cur.k, cur.steps);
  if (!ks.length) return null;
  const path = `/section-series/${cur.row.id}/contours?k=${ks.join(",")}`;
  return { path, sig: `${path}|${cur.row.version}|${layer}:${version}` };
}

/** The lines of BFA1 `series_contours`. */
export function readShadow(buf: ArrayBuffer): ShadowLines & { section: Uint32Array } {
  const doc = decodeBfa1<{ kind?: string; lines?: number }>(buf);
  if (doc.header.kind !== "series_contours") throw new Error("Контуры серии повреждены: обновите вид.");
  const section = bfa1Array(doc, "section", "<u4");
  return { xyz: bfa1Array(doc, "xyz", "<f8"), offsets: bfa1Array(doc, "offsets", "<u4", section.length + 1), section };
}

/** The grey of the theme (muted text). */
function shadowColor(): string {
  const css = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  return css?.getPropertyValue("--text-dim").trim() || "#8a93a3";
}

export interface ShadowTarget {
  addLayer(key: string, layer: ShadowLayer): void;
  removeLayer(key: string): void;
  layerChanged(): void;
  invalidate(): void;
}

export class ShadowSource {
  private layer: ShadowLayer | null = null;
  private sig: string | null = null;
  private etag: { path: string; tag: string } | null = null;
  private abort: AbortController | null = null;
  private disposed = false;
  /** every `show` takes a number: a layer made for an older one is not added */
  private shown = 0;
  /** the request in flight (tests wait for it) */
  pending: Promise<void> | null = null;

  constructor(
    private readonly scene: ShadowTarget,
    private readonly make: () => Promise<ShadowLayer> = async () => new (await import("../../viz/section/shadow")).ShadowLayer(shadowColor()),
  ) {}

  /** The section, the series or the layers changed: the shadow follows (nothing is asked when nothing changed). */
  sync(cur: CurrentSection | null = currentSection(), layerVersion: (id: number) => number | null = versionOf): void {
    if (this.disposed) return;
    const want = shadowRequest(cur, layerVersion);
    if (want?.sig === this.sig) return;
    this.sig = want?.sig ?? null;
    this.abort?.abort();
    if (!want) {
      this.show(null);
      return;
    }
    const abort = new AbortController();
    this.abort = abort;
    this.pending = this.load(want.path, abort.signal).finally(() => {
      if (this.abort === abort) this.abort = null;
    });
  }

  private async load(path: string, signal: AbortSignal): Promise<void> {
    const target = coreTarget();
    if (!target) return;
    const headers: Record<string, string> = authHeaders(target, {});
    // a 304 keeps the lines that are there: asked only while the layer shows them
    if (this.etag?.path === path && this.layer) headers["If-None-Match"] = this.etag.tag;
    let lines: ShadowLines | null;
    try {
      const r = await fetch(apiUrl(target, path), { headers, signal });
      if (signal.aborted || this.disposed) return;
      if (r.status === 304) return;
      if (!r.ok) {
        // no layer of contours yet, a deleted series: no shadow (asked again on the next change)
        this.sig = null;
        return this.show(null);
      }
      const tag = r.headers.get("ETag");
      this.etag = tag ? { path, tag } : null;
      lines = readShadow(await r.arrayBuffer());
    } catch {
      if (!signal.aborted) this.sig = null;
      return;
    }
    if (signal.aborted || this.disposed) return;
    await this.show(lines);
  }

  private async show(lines: ShadowLines | null): Promise<void> {
    const n = ++this.shown;
    if (!lines || !lines.xyz.length) {
      this.etag = null;
      if (this.layer) {
        this.scene.removeLayer(SHADOW_KEY);
        this.layer.dispose();
        this.layer = null;
      }
      return;
    }
    if (!this.layer) {
      const layer = await this.make();
      // disposed, or another `show` came while the layer was made (none — no layer; others — that one adds it)
      if (this.disposed || n !== this.shown || this.layer) return layer.dispose();
      layer.show(lines);
      this.layer = layer;
      this.scene.addLayer(SHADOW_KEY, layer);
      return;
    }
    if (this.layer.show(lines)) this.scene.layerChanged();
    this.scene.invalidate();
  }

  /** After a lost context the scene has no layer: everything is asked again. */
  reset(): void {
    this.abort?.abort();
    this.shown++;
    this.layer = null;
    this.sig = null;
    this.etag = null;
  }

  /** For the E2E hook and the tests: segments shown. */
  get segments(): number {
    return this.layer?.segments ?? 0;
  }

  dispose(): void {
    this.disposed = true;
    this.abort?.abort();
    if (this.layer) {
      this.scene.removeLayer(SHADOW_KEY);
      this.layer.dispose();
      this.layer = null;
    }
  }
}

const versionOf = (id: number): number | null => lineLayers().find((l) => l.id === id)?.version ?? null;

let current: ShadowSource | null = null;
export const shadowSource = (): ShadowSource | null => current;

/** The shadow follows the scene, the view keys, the series and the layers of the project (once, at start). */
export function bindShadow(): () => void {
  let off: (() => void)[] = [];
  const stopScene = onScene((scene: Scene | null) => {
    current?.dispose();
    off.forEach((fn) => fn());
    off = [];
    current = scene ? new ShadowSource(scene) : null;
    if (!scene || !current) return;
    const src = current;
    const sync = () => src.sync();
    const restored = () => {
      src.reset();
      sync();
    };
    off = [viewStore.subscribe(sync), seriesStore.subscribe(sync), sceneObjects.subscribe(sync), scene.onRestored(restored)];
    sync();
  });
  return () => {
    stopScene();
    off.forEach((fn) => fn());
    current?.dispose();
    current = null;
  };
}
