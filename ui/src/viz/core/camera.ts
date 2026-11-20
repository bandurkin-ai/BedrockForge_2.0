/**
 * The camera of the 3D window (BF `scene3d.ts` 366–370, 1952–1960, 2234–2242, 2740–2753): an orthographic
 * camera by default (plans and sections true to scale) or a perspective one, Z up, turned by OrbitControls
 * (left — rotate, wheel — zoom, right — pan). The pure view math works on `CameraState` (float64); this
 * module reads the state from three.js and applies a state back. Rendered space: relative to the scene
 * origin, Z stretched by the vertical exaggeration. «Зафиксировать вид» (`setLock`, ADR 0016 §4): orthographic,
 * turning off, the camera held on the locked direction and up after every move (OrbitControls rounds its
 * position through spherical coordinates), panning and zooming as before.
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { holdLock, type HeldView } from "./lockView";
import { DEFAULT_FOV, type CameraState, type ViewOptions } from "./views";
import type { Vec3 } from "./vec3";

export class CameraRig {
  readonly ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
  readonly persp = new THREE.PerspectiveCamera(DEFAULT_FOV, 1, 1, 1000);
  camera: THREE.OrthographicCamera | THREE.PerspectiveCamera = this.ortho;
  controls: OrbitControls;
  aspect = 1;
  vexag = 1;
  /** the camera moved (controls or a new state) */
  onChange: () => void = () => {};
  /** the user took the view with the mouse: a smooth turn under way stops */
  onUserStart: () => void = () => {};
  private held: HeldView | null = null;
  /** the projection the view asks for (orthographic while locked) */
  private wantPerspective = false;

  constructor(private readonly dom: HTMLElement) {
    this.ortho.up.set(0, 0, 1);
    this.persp.up.set(0, 0, 1);
    this.ortho.position.set(0.9, -1.2, 0.8);
    this.controls = this.makeControls();
  }

  get perspective(): boolean {
    return this.camera === this.persp;
  }

  get locked(): boolean {
    return this.held !== null;
  }

  /**
   * Locks the view on `held` (null — frees it): orthographic while locked, the asked projection after; turning
   * off. True — the projection changed (the caller fits the view again).
   */
  setLock(held: HeldView | null): boolean {
    this.held = held;
    const switched = this.switchTo(held ? false : this.wantPerspective);
    this.controls.enableRotate = !held;
    this.hold();
    return switched;
  }

  options(): ViewOptions {
    return { aspect: this.aspect, perspective: this.perspective, fov: this.persp.fov, vexag: this.vexag };
  }

  state(): CameraState {
    const c = this.camera;
    const t = this.controls.target;
    const position: Vec3 = [c.position.x, c.position.y, c.position.z];
    const target: Vec3 = [t.x, t.y, t.z];
    let half: number;
    if (c === this.ortho) half = this.ortho.top / (this.ortho.zoom || 1);
    else half = Math.hypot(position[0] - target[0], position[1] - target[1], position[2] - target[2]) * Math.tan(((this.persp.fov / 2) * Math.PI) / 180);
    return { position, target, up: [c.up.x, c.up.y, c.up.z], half, near: c.near, far: c.far };
  }

  /** Puts the camera to a state (the orthographic scale goes into its frustum, zoom 1). */
  apply(s: CameraState): void {
    const c = this.camera;
    c.up.set(s.up[0], s.up[1], s.up[2]);
    c.position.set(s.position[0], s.position[1], s.position[2]);
    this.controls.target.set(s.target[0], s.target[1], s.target[2]);
    if (c === this.ortho) {
      const half = Math.max(s.half, 1e-6);
      this.ortho.top = half;
      this.ortho.bottom = -half;
      this.ortho.left = -half * this.aspect;
      this.ortho.right = half * this.aspect;
      this.ortho.zoom = 1;
    }
    c.near = s.near;
    c.far = s.far;
    c.updateProjectionMatrix();
    c.lookAt(this.controls.target);
    this.controls.update();
    this.hold();
    this.onChange();
  }

  /** Near and far (and the step back of the orthographic camera) of this frame, without a change event. */
  setDepth(s: CameraState): void {
    const c = this.camera;
    if (c.position.x !== s.position[0] || c.position.y !== s.position[1] || c.position.z !== s.position[2]) {
      c.position.set(s.position[0], s.position[1], s.position[2]);
      c.updateMatrixWorld();
    }
    if (c.near !== s.near || c.far !== s.far) {
      c.near = s.near;
      c.far = s.far;
      c.updateProjectionMatrix();
    }
  }

  /** Orthographic or perspective (kept for later while locked); the caller fits the view again (BF setPerspective). */
  setPerspective(on: boolean): boolean {
    this.wantPerspective = on;
    return this.switchTo(on && !this.held);
  }

  private switchTo(on: boolean): boolean {
    const next = on ? this.persp : this.ortho;
    if (next === this.camera) return false;
    const target = this.controls.target.clone();
    next.position.copy(this.camera.position);
    this.camera = next;
    this.controls.dispose();
    this.controls = this.makeControls();
    this.controls.target.copy(target);
    this.controls.update();
    return true;
  }

  resize(w: number, h: number): void {
    this.aspect = Math.max(w, 1) / Math.max(h, 1);
    this.persp.aspect = this.aspect;
    this.persp.updateProjectionMatrix();
    const half = (this.ortho.top - this.ortho.bottom) / 2;
    this.ortho.left = -half * this.aspect;
    this.ortho.right = half * this.aspect;
    this.ortho.updateProjectionMatrix();
  }

  dispose(): void {
    this.controls.dispose();
  }

  /** Back on the locked direction and up, the target and the distance kept. */
  private hold(): void {
    if (!this.held) return;
    const s = holdLock(this.state(), this.held);
    const c = this.camera;
    c.position.set(s.position[0], s.position[1], s.position[2]);
    c.up.set(s.up[0], s.up[1], s.up[2]);
    c.lookAt(this.controls.target);
  }

  private makeControls(): OrbitControls {
    const controls = new OrbitControls(this.camera, this.dom);
    controls.screenSpacePanning = true;
    controls.enableDamping = false;
    controls.enableRotate = !this.held;
    controls.addEventListener("change", () => {
      this.hold();
      this.onChange();
    });
    controls.addEventListener("start", () => this.onUserStart());
    return controls;
  }
}
