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
import type { SectionLine } from "./section/math";

export interface SceneEvents {
  /** a click picked a hole (or nothing) */
  onPick?(hit: HoleHit | null): void;
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
  segments: number;
  pickMs: number;
}
