/**
 * The frame of the prototype in the main 3D (ТЗ 8.7.2: «в 3D — рамка с сеткой, рядом nx·ny·nz и объём на диске»; ADR 0023
 * §6): while the window «Прототип модели» is open and the core has answered the preview, a scene layer `blocks:prototype`
 * draws the box of the grid — the 8 corners the core gave (`frame`, project axes, so that a rotation about Z is seen as
 * it is) — and the lines of the parents on its bottom and top planes (at most 48 per direction: a finer grid is drawn
 * with a step, the number of parents is told in the window, not drawn). The core is the only one who knows the grid: the
 * layer takes its corners, the counts and the sub-block divisions, nothing is computed here. Float32 from the integer
 * origin of the layer (CLAUDE.md). A lazy chunk with three.js (`bind.ts` attaches it to every scene); after a lost
 * context the layer comes again.
 */
import * as THREE from "three";
import { bridge } from "../../../bridge";
import { windowStore } from "../../../shell/windows";
import type { Scene } from "../../../viz/Scene";
import { BRAND } from "../../../viz/ellipsoid/build";
import { integerOrigin } from "../../../viz/origin";
import type { SceneLayer } from "../../../viz/sceneTypes";
import type { Preview, Triple } from "./api";
import { buildStore, PROTOTYPE_WINDOW } from "./state";

export const FRAME_KEY = "blocks:prototype";
/** At most so many lines of the parents per direction on a plane. */
export const MAX_GRID_LINES = 48;

/** The 12 edges of the box by the corners (bit 0 — x, 1 — y, 2 — z of the corner's index). */
export const EDGES: readonly (readonly [number, number])[] = [
  [0, 1], [2, 3], [4, 5], [6, 7],
  [0, 2], [1, 3], [4, 6], [5, 7],
  [0, 4], [1, 5], [2, 6], [3, 7],
];

const add = (a: Triple, b: Triple, k = 1): Triple => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
const sub = (a: Triple, b: Triple): Triple => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: Triple, k: number): Triple => [a[0] * k, a[1] * k, a[2] * k];

/** The lines to draw as pairs of points (float64, absolute): the box, and the parents on the bottom and the top plane. */
export function frameSegments(p: Preview): { edges: Float64Array; grid: Float64Array; lines: number } {
  const f = p.frame;
  const edges = new Float64Array(EDGES.length * 6);
  EDGES.forEach(([a, b], i) => edges.set([...f[a], ...f[b]], i * 6));
  const [nx, ny] = p.grid.counts;
  const ex = scale(sub(f[1], f[0]), 1 / nx);
  const ey = scale(sub(f[2], f[0]), 1 / ny);
  const top = sub(f[4], f[0]);
  const step = (n: number) => Math.max(1, Math.ceil(n / MAX_GRID_LINES));
  const out: number[] = [];
  for (const base of [f[0], add(f[0], top)]) {
    for (let i = step(nx); i < nx; i += step(nx)) {
      const a = add(base, ex, i);
      out.push(...a, ...add(a, ey, ny));
    }
    for (let j = step(ny); j < ny; j += step(ny)) {
      const a = add(base, ey, j);
      out.push(...a, ...add(a, ex, nx));
    }
  }
  return { edges, grid: Float64Array.from(out), lines: EDGES.length + out.length / 6 };
}

/** What the layer shows now, for the tests and the E2E hook. */
export interface FrameShown {
  counts: Triple;
  parents: number;
  edges: number;
  gridLines: number;
  corner: Triple;
}

export class PrototypeFrameLayer implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: { x: number; y: number; z: number };
  readonly bbox: number[];
  private readonly materials: THREE.Material[] = [];
  private readonly geometries: THREE.BufferGeometry[] = [];
  private readonly info: FrameShown;

  constructor(p: Preview, ctx: { clip: THREE.Plane[] }) {
    const seg = frameSegments(p);
    this.origin = integerOrigin(Float64Array.from(p.frame.flat()));
    const axis = (k: number) => p.frame.map((c) => c[k]);
    this.bbox = p.extent.length === 6 ? [...p.extent] : [Math.min(...axis(0)), Math.min(...axis(1)), Math.min(...axis(2)), Math.max(...axis(0)), Math.max(...axis(1)), Math.max(...axis(2))];
    this.draw(seg.edges, BRAND.cobalt, 1, ctx);
    if (seg.grid.length) this.draw(seg.grid, BRAND.slate, 0.55, ctx);
    this.info = { counts: p.grid.counts, parents: p.parents, edges: EDGES.length, gridLines: seg.grid.length / 6, corner: p.grid.corner };
  }

  private draw(points: Float64Array, color: string, opacity: number, ctx: { clip: THREE.Plane[] }): void {
    const rel = new Float32Array(points.length);
    for (let i = 0; i < points.length; i += 3) {
      rel[i] = points[i] - this.origin.x;
      rel[i + 1] = points[i + 1] - this.origin.y;
      rel[i + 2] = points[i + 2] - this.origin.z;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(rel, 3));
    const material = new THREE.LineBasicMaterial({ color: new THREE.Color(color), transparent: opacity < 1, opacity, clippingPlanes: ctx.clip, depthWrite: false });
    this.geometries.push(geometry);
    this.materials.push(material);
    this.object.add(new THREE.LineSegments(geometry, material));
  }

  shown(): FrameShown {
    return this.info;
  }

  dispose(): void {
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
  }
}

/** Attaches the frame to a scene: drawn while the window of the prototype is open and the preview is answered. */
export function attachFrameScene(scene: Scene): () => void {
  let layer: PrototypeFrameLayer | null = null;
  let drawn: Preview | null = null;
  const wanted = (): Preview | null => {
    const open = windowStore.get().windows.some((w) => w.kind === PROTOTYPE_WINDOW);
    const pv = buildStore.get().preview;
    return open && pv.phase === "ok" ? pv.data : null;
  };
  const drop = (): void => {
    if (!layer) return;
    scene.removeLayer(FRAME_KEY, false);
    layer = null;
    drawn = null;
  };
  const sync = (): void => {
    const want = wanted();
    if (want === drawn) return;
    drop();
    if (want) {
      layer = new PrototypeFrameLayer(want, { clip: scene.clipPlanes });
      drawn = want;
      scene.addLayer(FRAME_KEY, layer);
    }
    scene.invalidate();
  };
  const stops = [buildStore.subscribe(sync), windowStore.subscribe(sync), scene.onRestored(() => {
    layer = null;
    drawn = null;
    sync();
  })];
  sync();
  // BF_E2E=1: what the frame shows (app/e2e)
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfBuildFrame = { shown: () => layer?.shown() ?? null };
  return () => {
    stops.forEach((fn) => fn());
    drop();
    scene.invalidate();
    if (hook) delete window.__bfBuildFrame;
  };
}

declare global {
  interface Window {
    __bfBuildFrame?: unknown;
  }
}
