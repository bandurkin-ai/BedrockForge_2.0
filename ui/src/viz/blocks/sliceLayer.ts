/**
 * The section of a block model by a plane in the 3D (ТЗ 8.7.6, 8.7.7 «Срез на разрезе»; ADR 0023 §11, decision 19;
 * the answer — `sliceDecode.ts`): one textured rectangle on the plane, one texel per pixel of the raster of the core
 * (a cell, a sub-cell or a sample of an oblique plane). The classes are the client's (the core sends values, never
 * colours): a scale of 256 buckets, the breaks or the codes of the categories as `legend.ts` has them, so a new
 * palette is a new bake of the texels — no request; a new field, range, hidden category, plane or filter is a new
 * request and a new layer (`sliceRequest` in `modules/blocks/sliceState.ts`).
 *
 * A pixel with `m = 0` (no block, filtered out, hidden category) is transparent; `m = 2` (a block without a value) and
 * a NaN are the grey of «нет значения» — never the colour of 0. The corners are float64 of the project, the layer's
 * own origin is a whole number of metres in the middle of the raster, so float32 of the vertices holds millimetres at
 * UTM coordinates (`viz/origin.ts`). Two ways to be seen: a plane in the 3D is an ordinary surface (depth-tested); the
 * plane of a section («Срез на разрезе») is a backdrop — drawn first, without the depth test, so the holes, the lines
 * and the wireframes of the section stay on top of it. Clipped by the section window like the rest.
 */
import * as THREE from "three";
import { integerOrigin } from "../origin";
import type { SceneLayer } from "../sceneTypes";
import { NO_CLASS, type ColourKind } from "./decode";
import { legendBytes, NONE_COLOR, NO_VALUE_COLOR, parseColor, type LegendBytes, type LegendStyle } from "./legend";
import { M_EMPTY, M_NONE, M_VALUE, sliceCorners, type SliceData, type Vec3 } from "./sliceDecode";

export const SLICE_BUCKETS = 256;

/** How the values of the raster become classes: the kind follows the field of the answer, the range is resolved. */
export interface SliceColour {
  kind: ColourKind;
  /** scale */
  from: number;
  to: number;
  log: boolean;
  /** classes */
  breaks: number[];
}

/** What the style says about the colours; nulls are resolved by `resolveSliceColour`. */
export interface SliceColourStyle {
  kind: ColourKind;
  from: number | null;
  to: number | null;
  log: boolean;
  breaks: number[];
}

/**
 * The classes of the raster. The kind comes from the field of the answer (a number — a scale or breaks, a category —
 * its codes, no field — one colour); the range of a scale is the style's, else `fallback` (the range the shell of the
 * same model resolved), else the minimum and maximum of the values of the raster (the core takes `stats` of the field).
 */
export function resolveSliceColour(data: SliceData, style: SliceColourStyle, fallback?: { from: number; to: number } | null): SliceColour {
  const base = { from: 0, to: 1, log: false, breaks: [] as number[] };
  const fk = data.header.field_kind;
  if (fk === null) return { ...base, kind: "none" };
  if (fk === "category") return { ...base, kind: "categories" };
  if (style.kind === "classes" && style.breaks.length > 0) return { ...base, kind: "classes", breaks: [...style.breaks] };
  let lo = style.from ?? fallback?.from ?? null;
  let hi = style.to ?? fallback?.to ?? null;
  const log = style.log;
  if (lo === null || hi === null || (log && lo <= 0)) {
    let min = Infinity;
    let max = -Infinity;
    let pos = Infinity;
    const { value, m } = data;
    for (let i = 0; i < value.length; i++) {
      if (m[i] !== M_VALUE) continue;
      const v = value[i];
      if (v !== v) continue; // NaN
      if (v < min) min = v;
      if (v > max) max = v;
      if (v > 0 && v < pos) pos = v;
    }
    if (lo === null) lo = Number.isFinite(min) ? min : 0;
    if (hi === null) hi = Number.isFinite(max) ? max : 1;
    if (log && lo <= 0) lo = Number.isFinite(pos) ? pos : 1;
  }
  if (!(hi > lo)) hi = lo + 1; // a constant field: one bucket (as the core)
  return { kind: "scale", from: lo, to: hi, log, breaks: [] };
}

/** The number of classes the legend of a colouring has: 256 buckets, the breaks + 1, the codes. */
export function sliceClassCount(data: SliceData, c: SliceColour): number {
  switch (c.kind) {
    case "scale":
      return SLICE_BUCKETS;
    case "classes":
      return c.breaks.length + 1;
    case "categories":
      return Math.max(data.header.labels?.length ?? 0, data.maxCode + 1, 1);
    default:
      return 0;
  }
}

/**
 * The class of every pixel (`shell_merge.scale_class`, `break_class`): a bucket 0…255 of the scale (log: v ≤ 0 → 0), the
 * number of breaks ≤ the value, the code of a category; NO_CLASS for a block without a value or a NaN. Pixels with
 * `m = 0` get 0 and are never drawn.
 */
export function sliceClasses(data: SliceData, c: SliceColour): Uint16Array {
  const { value, m } = data;
  const out = new Uint16Array(value.length);
  const lo = c.log ? Math.log(c.from) : c.from;
  const span = c.log ? Math.log(c.to) - lo : c.to - c.from;
  const last = SLICE_BUCKETS - 1;
  const b = c.breaks;
  for (let i = 0; i < value.length; i++) {
    const mi = m[i];
    if (mi === M_NONE) continue;
    const v = value[i];
    if (mi === M_EMPTY || v !== v) {
      out[i] = NO_CLASS;
      continue;
    }
    switch (c.kind) {
      case "scale": {
        const t = c.log ? (v > 0 ? (Math.log(v) - lo) / span : 0) : (v - lo) / span;
        const k = Math.floor(t * SLICE_BUCKETS);
        out[i] = k < 0 ? 0 : k > last ? last : k;
        break;
      }
      case "classes": {
        let a = 0;
        let z = b.length;
        while (a < z) {
          const mid = (a + z) >> 1;
          if (b[mid] <= v) a = mid + 1;
          else z = mid;
        }
        out[i] = a;
        break;
      }
      case "categories":
        out[i] = v < 0 ? NO_CLASS : Math.round(v);
        break;
      default:
        out[i] = 0;
    }
  }
  return out;
}

/** RGBA8 of the raster: the legend by the class, grey for no value, transparent for `m = 0`. */
export function sliceRgba(data: SliceData, classes: Uint16Array, kind: ColourKind, legend: LegendBytes, style: LegendStyle = {}, out?: Uint8Array): Uint8Array {
  const n = classes.length;
  const rgba = out && out.length === 4 * n ? out : new Uint8Array(4 * n);
  const miss = parseColor(style.noValue ?? NO_VALUE_COLOR);
  const none = parseColor(style.none ?? NONE_COLOR);
  const table = legend.data;
  const total = legend.width * legend.height;
  const m = data.m;
  for (let i = 0; i < n; i++) {
    const p = 4 * i;
    if (m[i] === M_NONE) {
      rgba[p] = rgba[p + 1] = rgba[p + 2] = rgba[p + 3] = 0;
      continue;
    }
    const k = classes[i];
    if (m[i] === M_EMPTY) {
      rgba[p] = miss[0];
      rgba[p + 1] = miss[1];
      rgba[p + 2] = miss[2];
    } else if (kind === "none") {
      rgba[p] = none[0];
      rgba[p + 1] = none[1];
      rgba[p + 2] = none[2];
    } else if (k === NO_CLASS || k >= total) {
      rgba[p] = miss[0];
      rgba[p + 1] = miss[1];
      rgba[p + 2] = miss[2];
    } else {
      const q = 4 * k;
      rgba[p] = table[q];
      rgba[p + 1] = table[q + 1];
      rgba[p + 2] = table[q + 2];
    }
    rgba[p + 3] = 255;
  }
  return rgba;
}

export interface SliceLook {
  /** 0…1; below 1 the plane blends (no depth write) */
  opacity: number;
  /** the plane of a section: drawn first, under everything, without the depth test */
  backdrop: boolean;
}

export interface SliceShown {
  model: number;
  version: number;
  raster: string;
  width: number;
  height: number;
  withValue: number;
  withoutValue: number;
  kind: ColourKind;
  look: SliceLook;
  visible: boolean;
}

export interface SliceLayerContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
}

export interface SliceLayerOptions {
  colour: SliceColour;
  style?: LegendStyle;
  look?: Partial<SliceLook>;
}

export class SliceLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[] | null;
  readonly mesh: THREE.Mesh | null = null;
  readonly texture: THREE.DataTexture | null = null;
  private readonly material: THREE.MeshBasicMaterial | null = null;
  private readonly geometry: THREE.BufferGeometry | null = null;
  private colour: SliceColour;
  private colourKey: string;
  private classes: Uint16Array | null = null;
  private style: LegendStyle;
  private ownLook: SliceLook;

  constructor(
    readonly data: SliceData,
    ctx: SliceLayerContext,
    opts: SliceLayerOptions,
  ) {
    this.colour = opts.colour;
    this.colourKey = JSON.stringify(opts.colour);
    this.style = { ...opts.style };
    this.ownLook = { opacity: 1, backdrop: false, ...opts.look };
    if (data.empty) {
      this.bbox = null;
      this.origin = { x: 0, y: 0, z: 0 };
      return;
    }
    // float64 corners in the project → float32 from the whole-metre origin in the middle of the raster
    const corners = sliceCorners(data.header);
    const flat = new Float64Array(12);
    corners.forEach((c, i) => flat.set(c, 3 * i));
    this.origin = integerOrigin(flat);
    const lo = [Infinity, Infinity, Infinity];
    const hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < 12; i++) {
      lo[i % 3] = Math.min(lo[i % 3], flat[i]);
      hi[i % 3] = Math.max(hi[i % 3], flat[i]);
    }
    this.bbox = [...lo, ...hi];
    const pos = new Float32Array(12);
    const o = this.origin;
    corners.forEach((c: Vec3, i) => pos.set([c[0] - o.x, c[1] - o.y, c[2] - o.z], 3 * i));
    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    this.geometry.setAttribute("uv", new THREE.BufferAttribute(Float32Array.of(0, 0, 1, 0, 1, 1, 0, 1), 2));
    this.geometry.setIndex([0, 1, 2, 0, 2, 3]);
    this.texture = new THREE.DataTexture(this.bake(), data.width, data.height, THREE.RGBAFormat, THREE.UnsignedByteType);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.magFilter = THREE.NearestFilter;
    this.texture.minFilter = THREE.NearestFilter;
    this.texture.generateMipmaps = false;
    this.texture.flipY = false;
    this.texture.needsUpdate = true;
    this.material = new THREE.MeshBasicMaterial({ map: this.texture, side: THREE.DoubleSide, alphaTest: 0.01 });
    this.material.clippingPlanes = ctx.clip;
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false; // one quad; the extent is the layer's `bbox`
    this.object.add(this.mesh);
    this.applyLook();
  }

  private legend(): LegendBytes {
    const classes = sliceClassCount(this.data, this.colour);
    return legendBytes(this.colour.kind, classes, this.data.maxCode, this.style);
  }

  private bake(into?: Uint8Array): Uint8Array {
    this.classes ??= sliceClasses(this.data, this.colour);
    return sliceRgba(this.data, this.classes, this.colour.kind, this.legend(), this.style, into);
  }

  /**
   * New colours or a new range of the same raster: the texels only, no request. A changed range, breaks or kind recount
   * the classes; a changed palette only looks them up again.
   */
  setColour(colour: SliceColour, style: LegendStyle = this.style): void {
    this.style = { ...style };
    const key = JSON.stringify(colour);
    if (key !== this.colourKey) {
      this.colour = colour;
      this.colourKey = key;
      this.classes = null;
    }
    if (!this.texture) return;
    this.bake(this.texture.image.data as Uint8Array);
    this.texture.needsUpdate = true;
  }

  setLook(look: Partial<SliceLook>): void {
    this.ownLook = { ...this.ownLook, ...look };
    this.applyLook();
  }

  private applyLook(): void {
    const m = this.material;
    if (!m || !this.mesh) return;
    const opacity = Math.min(1, Math.max(0, this.ownLook.opacity));
    const blend = opacity < 1;
    const back = this.ownLook.backdrop;
    m.opacity = opacity;
    if (m.transparent !== blend || m.depthTest === back) {
      m.transparent = blend;
      m.needsUpdate = true;
    }
    m.depthTest = !back;
    m.depthWrite = !blend && !back;
    this.mesh.renderOrder = back ? -10 : blend ? 4 : 0;
  }

  get look(): SliceLook {
    return this.ownLook;
  }

  get kind(): ColourKind {
    return this.colour.kind;
  }

  setVisible(on: boolean): void {
    this.object.visible = on;
  }

  shown(): SliceShown {
    const h = this.data.header;
    return {
      model: h.model,
      version: h.version,
      raster: h.raster,
      width: this.data.width,
      height: this.data.height,
      withValue: this.data.withValue,
      withoutValue: this.data.withoutValue,
      kind: this.colour.kind,
      look: { ...this.ownLook },
      visible: this.object.visible,
    };
  }

  dispose(): void {
    this.geometry?.dispose();
    this.material?.dispose();
    this.texture?.dispose();
    this.object.clear();
  }
}
