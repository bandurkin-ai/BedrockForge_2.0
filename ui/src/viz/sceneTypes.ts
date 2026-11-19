/**
 * Types of the facade `viz/Scene` (ADR 0007 §7), importable without three.js at run time: the events of
 * the scene, the context of a frame, a layer added from outside (displays along holes, Э7+ objects) and the
 * state read by the E2E hook.
 */
import type * as THREE from "three";
import type { FrameStats, GpuInfo } from "./core/renderer";
import type { CameraState, ViewOptions } from "./core/views";
import type { Vec3 } from "./core/vec3";
import type { HoleHit } from "./holes/select";
import type { LabelItem } from "./labels/sprites";
import type { Origin } from "./origin";
import type { Ray, SectionLine } from "./section/math";

export interface SceneEvents {
  /** a click picked a hole (or nothing) */
  onPick?(hit: HoleHit | null): void;
  /** the same click, when no hole was hit: the layer that answered it (a line, a point, a raster) or nothing */
  onPickLayer?(hit: LayerHit | null): void;
  /** a click while drawing a section: the point on the view plane (project coordinates) */
  onGroundClick?(p: { x: number; y: number }): void;
  onContextLost?(): void;
  onContextRestored?(): void;
  /** the camera moved, the view resized */
  onCamera?(): void;
}

/** What a layer sees before every frame. */
export interface FrameContext {
  camera: CameraState;
  options: ViewOptions;
  width: number;
  height: number;
  vexag: number;
  /** pixels per scene unit at a rendered-space point */
  pixelsPerUnit(at: Vec3): number;
}

/** A click for a layer to answer on the CPU (ADR 0011 §4). */
export interface PickQuery {
  /** clip ← rendered space (projection · view), float64 */
  vp: Float64Array;
  vexag: number;
  /** the cursor, CSS px from the top left of a width × height window */
  px: number;
  py: number;
  width: number;
  height: number;
  /** is a plan point (rendered space) inside the section window */
  inWindow(x: number, y: number): boolean;
  /** the ray of the cursor, rendered space */
  ray: Ray;
  /** float64: the origin of the layer − the scene origin (scene units, Z not stretched) */
  offset: Vec3;
}

export interface LayerPick {
  /** the smallest wins: lines and points 0…1 (their share of the pick tolerance), surfaces from 2 */
  score: number;
  hit: unknown;
}

export interface LayerHit extends LayerPick {
  /** the key the layer was added with */
  key: string;
}

export interface SceneLayer {
  /** three.js objects, coordinates relative to `origin` (materials: `clippingPlanes = scene.clipPlanes`) */
  object: THREE.Object3D;
  /** absolute integer origin of the object's coordinates */
  origin: Origin;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] for «Вписать», the grid and near / far; null — not counted */
  bbox?: ArrayLike<number> | null;
  /** labels to place with the others (by priority, hidden when they overlap) */
  labels?(): readonly LabelItem[];
  beforeRender?(ctx: FrameContext): void;
  /** the answer to a click, or null (only visible layers are asked) */
  pick?(q: PickQuery): LayerPick | null;
  dispose?(): void;
}

export interface SceneDebug {
  frames: number;
  idle: boolean;
  lost: boolean;
  stats: FrameStats;
  camera: CameraState & { dir: Vec3 };
  perspective: boolean;
  vexag: number;
  clipPlanes: number;
  section: SectionLine | null;
  gpu: GpuInfo;
  sets: number[];
  /** keys of the layers added with `addLayer` */
  layers: string[];
  segments: number;
  pickMs: number;
}
