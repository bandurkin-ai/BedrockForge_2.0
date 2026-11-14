import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { IntervalsData, Trajectories } from "../api";
import { NO_VALUE_COLOR, type ColorScale } from "./colorScale";

const PATH_COLOR = 0x8a93a6;
const SELECT_COLOR = 0xff7a00;

export interface Layers {
  paths: boolean;
  intervals: boolean;
  collars: boolean;
  grid: boolean;
}

export interface Hover {
  id: string;
  x: number;
  y: number;
}

/**
 * The 3D scene: trajectories, grade-coloured intervals, collars and a grid.
 * Z is up. All coordinates are shifted to the centre of the project, because
 * UTM-sized numbers (4 500 000) do not fit float32 without visible jitter.
 * Renders on demand (on camera change or data update), so an idle tab costs nothing.
 */
export class DrillScene {
  onPick: (holeId: string | null) => void = () => {};
  onHover: (hover: Hover | null) => void = () => {};
  /** called after every render, e.g. to move HTML labels */
  onRender: () => void = () => {};

  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(45, 1, 1, 1000);
  private controls: OrbitControls;
  private root = new THREE.Group();
  private materials: LineMaterial[] = [];
  private pathLines: LineSegments2 | null = null;
  private intervalLines: LineSegments2 | null = null;
  private selectedLine: LineSegments2 | null = null;
  private points: THREE.Points | null = null;
  private grid: THREE.GridHelper | null = null;
  private pointTexture: THREE.CanvasTexture;
  private collars: { id: string; position: THREE.Vector3 }[] = [];
  private paths = new Map<string, number[][]>();
  private origin = new THREE.Vector3();
  private radius = 100;
  private vexag = 1;
  private selected: string | null = null;
  private frameRequested = false;
  private disposed = false;
  private resizeObserver: ResizeObserver;
  private cleanup: (() => void)[] = [];
  private colorScheme = window.matchMedia("(prefers-color-scheme: dark)");

  constructor(private container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);
    this.renderer.domElement.className = "scene3d";

    this.camera.up.set(0, 0, 1);
    this.scene.add(this.root);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener("change", () => this.requestRender());
    this.pointTexture = makeCircleTexture();

    this.applyTheme();
    const onTheme = () => {
      this.applyTheme();
      this.requestRender();
    };
    this.colorScheme.addEventListener("change", onTheme);
    this.cleanup.push(() => this.colorScheme.removeEventListener("change", onTheme));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.attachPointerEvents();
    this.resize();
  }

  get webglCanvas(): HTMLCanvasElement {
    return this.renderer.domElement;
  }

  setData(
    trajectories: Trajectories | null,
    intervals: IntervalsData | null,
    scale: ColorScale | null,
    collarColors: Map<string, string>,
  ): void {
    this.clearObjects();
    if (!trajectories?.extent || trajectories.holes.length === 0) {
      this.requestRender();
      return;
    }
    const { min, max } = trajectories.extent;
    this.origin.set((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2);
    const o = this.origin;
    this.radius = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 50) / 2;

    const pathPositions: number[] = [];
    const collarPositions: number[] = [];
    const collarColorsArr: number[] = [];
    for (const h of trajectories.holes) {
      this.paths.set(h.hole_id, h.path);
      for (let i = 0; i + 1 < h.path.length; i++) {
        const a = h.path[i];
        const b = h.path[i + 1];
        pathPositions.push(a[0] - o.x, a[1] - o.y, a[2] - o.z, b[0] - o.x, b[1] - o.y, b[2] - o.z);
      }
      const c = h.path[0];
      const position = new THREE.Vector3(c[0] - o.x, c[1] - o.y, c[2] - o.z);
      this.collars.push({ id: h.hole_id, position });
      collarPositions.push(position.x, position.y, position.z);
      const color = new THREE.Color(collarColors.get(h.hole_id) ?? "#9aa3b5");
      collarColorsArr.push(color.r, color.g, color.b);
    }

    if (pathPositions.length > 0) {
      this.pathLines = this.makeLines(pathPositions, null, 1.5, PATH_COLOR);
      this.root.add(this.pathLines);
    }

    if (intervals) {
      const positions: number[] = [];
      const colors: number[] = [];
      const fallback = new THREE.Color(NO_VALUE_COLOR);
      for (const rows of Object.values(intervals.holes)) {
        for (const r of rows) {
          positions.push(r[3] - o.x, r[4] - o.y, r[5] - o.z, r[6] - o.x, r[7] - o.y, r[8] - o.z);
          const c = scale ? scale.rgb(r[2]).map((v) => v / 255) : [fallback.r, fallback.g, fallback.b];
          colors.push(c[0], c[1], c[2], c[0], c[1], c[2]);
        }
      }
      if (positions.length > 0) {
        this.intervalLines = this.makeLines(positions, colors, 4.5, 0xffffff);
        this.intervalLines.renderOrder = 2;
        this.root.add(this.intervalLines);
      }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(collarPositions, 3));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(collarColorsArr, 3));
    this.points = new THREE.Points(
      geometry,
      new THREE.PointsMaterial({ size: 10, sizeAttenuation: false, vertexColors: true, map: this.pointTexture, transparent: true, alphaTest: 0.4 }),
    );
    this.points.renderOrder = 3;
    this.root.add(this.points);

    const span = Math.max(max[0] - min[0], max[1] - min[1]) * 1.3;
    this.grid = new THREE.GridHelper(Math.max(span, 100), 20, 0x8a93a6, 0xbac0cc);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.z = min[2] - o.z - 2;
    (this.grid.material as THREE.Material).transparent = true;
    (this.grid.material as THREE.Material).opacity = 0.35;
    this.root.add(this.grid);

    this.applySelected();
    this.fit("iso");
  }

  setLayers(layers: Layers): void {
    if (this.pathLines) this.pathLines.visible = layers.paths;
    if (this.intervalLines) this.intervalLines.visible = layers.intervals;
    if (this.points) this.points.visible = layers.collars;
    if (this.grid) this.grid.visible = layers.grid;
    this.requestRender();
  }

  setVerticalExaggeration(value: number): void {
    this.vexag = value;
    this.root.scale.z = value;
    this.requestRender();
  }

  setSelected(holeId: string | null): void {
    this.selected = holeId;
    this.applySelected();
    this.requestRender();
  }

  /** Screen position (CSS px inside the container) of a collar, or null if it is behind the camera. */
  screenPosition(holeId: string): { x: number; y: number } | null {
    const collar = this.collars.find((c) => c.id === holeId);
    if (!collar) return null;
    return this.project(collar.position);
  }

  fit(preset: "iso" | "top" | "side" = "iso"): void {
    const target = new THREE.Vector3(0, 0, 0);
    const radius = Math.max(this.radius * Math.max(this.vexag * 0.6, 1), 1);
    const distance = (radius / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2))) * 1.05;
    const direction =
      preset === "top" ? new THREE.Vector3(0, -0.0001, 1)
      : preset === "side" ? new THREE.Vector3(0, -1, 0.02)
      : new THREE.Vector3(0.9, -1.2, 0.8);
    this.camera.position.copy(direction.normalize().multiplyScalar(distance));
    this.camera.near = distance / 200;
    this.camera.far = distance * 50;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(target);
    this.controls.update();
    this.requestRender();
  }

  dispose(): void {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.cleanup.forEach((fn) => fn());
    this.clearObjects();
    this.pointTexture.dispose();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }

  // ---- internals ----

  private makeLines(positions: number[], colors: number[] | null, width: number, color: number): LineSegments2 {
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(positions);
    if (colors) geometry.setColors(colors);
    const material = new LineMaterial({ color, linewidth: width, vertexColors: colors !== null, worldUnits: false });
    material.resolution.set(this.container.clientWidth || 1, this.container.clientHeight || 1);
    this.materials.push(material);
    const lines = new LineSegments2(geometry, material);
    lines.frustumCulled = false;
    return lines;
  }

  private applySelected(): void {
    if (this.selectedLine) {
      this.root.remove(this.selectedLine);
      this.selectedLine.geometry.dispose();
      this.materials = this.materials.filter((m) => m !== this.selectedLine!.material);
      (this.selectedLine.material as LineMaterial).dispose();
      this.selectedLine = null;
    }
    const path = this.selected ? this.paths.get(this.selected) : undefined;
    if (!path || path.length < 2) return;
    const o = this.origin;
    const positions: number[] = [];
    for (let i = 0; i + 1 < path.length; i++) {
      positions.push(path[i][0] - o.x, path[i][1] - o.y, path[i][2] - o.z, path[i + 1][0] - o.x, path[i + 1][1] - o.y, path[i + 1][2] - o.z);
    }
    this.selectedLine = this.makeLines(positions, null, 9, SELECT_COLOR);
    this.selectedLine.renderOrder = 1;
    this.root.add(this.selectedLine);
  }

  private clearObjects(): void {
    for (const obj of [this.pathLines, this.intervalLines, this.selectedLine, this.points, this.grid]) {
      if (!obj) continue;
      this.root.remove(obj);
      (obj as THREE.Mesh).geometry?.dispose();
      const material = (obj as THREE.Mesh).material as THREE.Material | undefined;
      material?.dispose();
    }
    this.pathLines = this.intervalLines = this.selectedLine = null;
    this.points = null;
    this.grid = null;
    this.materials = [];
    this.collars = [];
    this.paths.clear();
  }

  private project(position: THREE.Vector3): { x: number; y: number } | null {
    const v = new THREE.Vector3(position.x, position.y, position.z * this.vexag).project(this.camera);
    if (v.z < -1 || v.z > 1) return null;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    return { x: (v.x * 0.5 + 0.5) * w, y: (-v.y * 0.5 + 0.5) * h };
  }

  private nearestCollar(clientX: number, clientY: number, maxDistance: number): Hover | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    let best: Hover | null = null;
    let bestDistance = maxDistance;
    for (const c of this.collars) {
      const s = this.project(c.position);
      if (!s) continue;
      const d = Math.hypot(s.x - px, s.y - py);
      if (d < bestDistance) {
        bestDistance = d;
        best = { id: c.id, x: s.x, y: s.y };
      }
    }
    return best;
  }

  private attachPointerEvents(): void {
    const el = this.renderer.domElement;
    let down: { x: number; y: number; t: number } | null = null;
    let hoverFrame = 0;
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; };
    const onUp = (e: PointerEvent) => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      const quick = performance.now() - down.t < 600;
      down = null;
      if (moved < 4 && quick) this.onPick(this.nearestCollar(e.clientX, e.clientY, 14)?.id ?? null);
    };
    const onMove = (e: PointerEvent) => {
      if (down || hoverFrame) return;
      hoverFrame = requestAnimationFrame(() => {
        hoverFrame = 0;
        this.onHover(this.nearestCollar(e.clientX, e.clientY, 12));
      });
    };
    const onLeave = () => this.onHover(null);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    this.cleanup.push(() => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      if (hoverFrame) cancelAnimationFrame(hoverFrame);
    });
  }

  /** The scene background follows the page background (light or dark theme). */
  private applyTheme(): void {
    this.renderer.setClearColor(new THREE.Color(getComputedStyle(this.container).backgroundColor));
  }

  private resize(): void {
    const w = Math.max(this.container.clientWidth, 1);
    const h = Math.max(this.container.clientHeight, 1);
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const m of this.materials) m.resolution.set(w, h);
    this.requestRender();
  }

  private requestRender(): void {
    if (this.frameRequested || this.disposed) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      if (this.disposed) return;
      this.renderer.render(this.scene, this.camera);
      this.onRender();
    });
  }
}

function makeCircleTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
  ctx.lineWidth = 5;
  ctx.strokeStyle = "rgba(0,0,0,0.55)";
  ctx.stroke();
  return new THREE.CanvasTexture(canvas);
}
