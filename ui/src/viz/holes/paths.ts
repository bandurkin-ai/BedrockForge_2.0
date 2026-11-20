/**
 * Hole traces of a part (ADR 0007 §7; BF `makeLines` 2372, `applyTraceStyle` 562–570): one line object per
 * kind of hole in the part — `LineSegments2` with a width in pixels, or plain `THREE.LineSegments` when the
 * width is at most 1 px or the scene holds more segments than its class of graphics draws fat (`budget.ts`
 * `THIN_LINE_SEGMENTS`: a million; software rendering — 200 thousand). Design holes are dashed (the dash
 * is kept in pixels: its scale follows the zoom), trenches have their own colour unless the set has one.
 * Buffers come from the Worker as they are (no copy); the materials take the shared clipping planes.
 */
import * as THREE from "three";
import { InstancedInterleavedBuffer, InterleavedBufferAttribute } from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { fatLines, THIN_LINE_SEGMENTS, type GpuClass } from "../core/budget";
import { DEFAULT_TRACE_STYLE, type TraceStyle } from "./traceStyle";
import { TRENCH_COLOR } from "./colors";
import { KIND_DESIGN, KIND_TRENCH, type BuiltPaths, type PathGroup } from "../worker/paths";

/** Above this many segments in the scene the traces are drawn 1 px wide (ADR 0007 §7); real graphics. */
export const THIN_SEGMENTS = THIN_LINE_SEGMENTS.integrated;
const DASH_PX = 8;
const GAP_PX = 5;

type LineMat = LineMaterial | THREE.LineBasicMaterial | THREE.LineDashedMaterial;

export interface TraceLine {
  object: LineSegments2 | THREE.LineSegments;
  material: LineMat;
  group: PathGroup;
}

export function traceColor(kind: number, style: TraceStyle): string {
  return kind === KIND_TRENCH && style.color === DEFAULT_TRACE_STYLE.color ? TRENCH_COLOR : style.color;
}

/** Fat lines only when they are wider than a pixel and the scene is not too heavy for the class of graphics. */
export function useFatLines(style: TraceStyle, sceneSegments: number, gpu?: GpuClass): boolean {
  return fatLines(style.width, sceneSegments, gpu);
}

function fatLine(g: PathGroup, style: TraceStyle, clip: THREE.Plane[], resolution: THREE.Vector2): TraceLine {
  const geometry = new LineSegmentsGeometry();
  geometry.setPositions(g.positions);
  if (g.distances) {
    const buf = new InstancedInterleavedBuffer(g.distances, 2, 1);
    geometry.setAttribute("instanceDistanceStart", new InterleavedBufferAttribute(buf, 1, 0));
    geometry.setAttribute("instanceDistanceEnd", new InterleavedBufferAttribute(buf, 1, 1));
  }
  const material = new LineMaterial({ color: traceColor(g.kind, style), linewidth: style.width, worldUnits: false, dashed: g.kind === KIND_DESIGN, dashSize: DASH_PX, gapSize: GAP_PX, dashScale: 1 });
  material.clippingPlanes = clip;
  material.uniforms.resolution.value = resolution; // shared: a resize updates every trace
  const object = new LineSegments2(geometry, material);
  return { object, material, group: g };
}

function thinLine(g: PathGroup, style: TraceStyle, clip: THREE.Plane[]): TraceLine {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(g.positions, 3));
  geometry.computeBoundingSphere();
  const color = traceColor(g.kind, style);
  let material: LineMat;
  if (g.kind === KIND_DESIGN && g.distances) {
    geometry.setAttribute("lineDistance", new THREE.BufferAttribute(g.distances, 1));
    material = new THREE.LineDashedMaterial({ color, dashSize: DASH_PX, gapSize: GAP_PX, scale: 1 });
  } else {
    material = new THREE.LineBasicMaterial({ color });
  }
  material.clippingPlanes = clip;
  return { object: new THREE.LineSegments(geometry, material), material, group: g };
}

/** The line objects of a part, in a group (positioned by the part). */
export class TraceLines {
  readonly group = new THREE.Group();
  lines: TraceLine[] = [];
  fat = false;

  constructor(
    readonly built: BuiltPaths,
    private readonly clip: THREE.Plane[],
    private readonly resolution: THREE.Vector2,
  ) {
    this.group.renderOrder = 2;
  }

  get segments(): number {
    return this.built.segments;
  }

  /** Width and colour; a change between fat and thin lines rebuilds the objects (the buffers stay). */
  apply(style: TraceStyle, fat: boolean): void {
    if (fat !== this.fat || this.lines.length === 0) {
      this.clear();
      this.fat = fat;
      this.lines = this.built.groups.map((g) => (fat ? fatLine(g, style, this.clip, this.resolution) : thinLine(g, style, this.clip)));
      for (const l of this.lines) this.group.add(l.object);
    }
    for (const l of this.lines) {
      l.material.color.set(traceColor(l.group.kind, style));
      if (l.material instanceof LineMaterial) l.material.linewidth = style.width;
    }
  }

  /** Dashes of design holes stay `DASH_PX` long on the screen: their scale is pixels per scene unit. */
  setDashScale(ppu: number): void {
    for (const l of this.lines) {
      if (l.material instanceof LineMaterial && l.material.dashed) l.material.dashScale = ppu;
      else if (l.material instanceof THREE.LineDashedMaterial) l.material.scale = ppu;
    }
  }

  private clear(): void {
    for (const l of this.lines) {
      this.group.remove(l.object);
      l.object.geometry.dispose();
      l.material.dispose();
    }
    this.lines = [];
  }

  dispose(): void {
    this.clear();
  }
}
