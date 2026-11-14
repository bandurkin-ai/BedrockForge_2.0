import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { IntervalsData, Trajectories } from "../api";
import { NO_VALUE_COLOR, type ColorScale } from "./colorScale";
import type { SectionLine } from "./geometry";
import { THEME_EVENT } from "./scene3d-theme";

const PATH_COLOR = 0x8a93a6;
const SELECT_COLOR = 0xff7a00;
const SECTION_COLOR = 0x2e6fbd;

export type SceneMode = "3d" | "plan" | "section";

export interface HoleRef {
  setId: number;
  holeId: string;
}

export interface Hover extends HoleRef {
  x: number;
  y: number;
}

export interface DrillholeStyle {
  visible: boolean;
  paths: boolean;
  intervals: boolean;
  collars: boolean;
}

interface DrillholeLayer {
  extent: THREE.Box3;
  group: THREE.Group;
  paths: LineSegments2 | null;
  intervals: LineSegments2 | null;
  points: THREE.Points | null;
  collars: { holeId: string; position: THREE.Vector3 }[];
  pathOf: Map<string, number[][]>;
}

interface WireframeLayer {
  extent: THREE.Box3;
  mesh: THREE.Mesh;
  /** The wireframe's intersection with the section plane (section mode only). */
  cut: LineSegments2 | null;
}

interface LineLayer3D {
  extent: THREE.Box3;
  lines: LineSegments2;
}

interface RasterLayer {
  /** Project coordinates of the corners: top-left, top-right, bottom-right, bottom-left. */
  corners: number[][];
  /** null: placed just under the lowest object of the scene. */
  elevation: number | null;
  mesh: THREE.Mesh;
  texture: THREE.Texture | null;
  visible: boolean;
}

/**
 * The one 3D view of the project: drillhole sets and wireframes in a shared space.
 * Plan and section are camera modes of the same scene: plan looks straight down,
 * section looks across a vertical line and clips everything outside its corridor.
 *
 * Z is up. Coordinates are shifted to a scene origin (the first loaded object), because
 * UTM-sized numbers (4 500 000) do not fit float32 without visible jitter.
 * Renders on demand, so an idle tab costs nothing.
 */
export class GeoScene {
  onPick: (hole: HoleRef | null) => void = () => {};
  onHover: (hover: Hover | null) => void = () => {};
  /** A click on the ground in plan mode (project coordinates), used to draw a section line. */
  onPlanClick: ((x: number, y: number) => boolean) | null = null;
  /** called after every render, e.g. to move HTML labels */
  onRender: () => void = () => {};

  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private perspective = new THREE.PerspectiveCamera(45, 1, 1, 1000);
  private ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
  private camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = this.perspective;
  private controls: OrbitControls;
  private root = new THREE.Group();
  private light = new THREE.DirectionalLight(0xffffff, 1.6);
  private materials = new Set<LineMaterial>();
  private drillholes = new Map<number, DrillholeLayer>();
  private wireframes = new Map<number, WireframeLayer>();
  private lineLayers = new Map<number, LineLayer3D>();
  private rasters = new Map<number, RasterLayer>();
  private selectedLine: LineSegments2 | null = null;
  private sectionLine: THREE.LineSegments | null = null;
  private grid: THREE.GridHelper | null = null;
  private pointTexture: THREE.CanvasTexture;
  private origin: THREE.Vector3 | null = null;
  private vexag = 1;
  private mode: SceneMode = "3d";
  private section: SectionLine | null = null;
  private selected: HoleRef | null = null;
  private gridVisible = true;
  private fitted = false;
  private frameRequested = false;
  private disposed = false;
  private resizeObserver: ResizeObserver;
  private cleanup: (() => void)[] = [];

  constructor(private container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);
    this.renderer.domElement.className = "scene3d";

    this.perspective.up.set(0, 0, 1);
    this.ortho.up.set(0, 0, 1);
    this.scene.add(this.root);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.4));
    this.scene.add(this.light);
    this.scene.add(this.light.target);
    this.controls = this.makeControls();
    this.pointTexture = makeCircleTexture();

    this.applyTheme();
    const onTheme = () => {
      this.applyTheme();
      this.requestRender();
    };
    // The shell announces a theme switch after it has updated the page colours.
    window.addEventListener(THEME_EVENT, onTheme);
    this.cleanup.push(() => window.removeEventListener(THEME_EVENT, onTheme));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.attachPointerEvents();
    this.resize();
  }

  get webglCanvas(): HTMLCanvasElement {
    return this.renderer.domElement;
  }

  // ---------------------------------------------------------------- data

  /** Sets the scene origin from the first object; everything later is placed relative to it. */
  private ensureOrigin(min: number[], max: number[]): THREE.Vector3 {
    if (!this.origin) this.origin = new THREE.Vector3((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2);
    return this.origin;
  }

  setDrillholes(setId: number, trajectories: Trajectories, intervals: IntervalsData | null, scale: ColorScale | null, collarColors: Map<string, string>): void {
    this.removeDrillholes(setId, false);
    if (!trajectories.extent || trajectories.holes.length === 0) return;
    const o = this.ensureOrigin(trajectories.extent.min, trajectories.extent.max);
    const layer: DrillholeLayer = {
      extent: new THREE.Box3(
        new THREE.Vector3(...trajectories.extent.min).sub(o),
        new THREE.Vector3(...trajectories.extent.max).sub(o),
      ),
      group: new THREE.Group(),
      paths: null,
      intervals: null,
      points: null,
      collars: [],
      pathOf: new Map(),
    };

    const pathPositions: number[] = [];
    const collarPositions: number[] = [];
    const collarColorsArr: number[] = [];
    for (const h of trajectories.holes) {
      layer.pathOf.set(h.hole_id, h.path);
      for (let i = 0; i + 1 < h.path.length; i++) {
        const a = h.path[i];
        const b = h.path[i + 1];
        pathPositions.push(a[0] - o.x, a[1] - o.y, a[2] - o.z, b[0] - o.x, b[1] - o.y, b[2] - o.z);
      }
      const c = h.path[0];
      const position = new THREE.Vector3(c[0] - o.x, c[1] - o.y, c[2] - o.z);
      layer.collars.push({ holeId: h.hole_id, position });
      collarPositions.push(position.x, position.y, position.z);
      const color = new THREE.Color(collarColors.get(h.hole_id) ?? "#9aa3b5");
      collarColorsArr.push(color.r, color.g, color.b);
    }
    if (pathPositions.length > 0) {
      layer.paths = this.makeLines(pathPositions, null, 1.5, PATH_COLOR);
      layer.group.add(layer.paths);
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
        layer.intervals = this.makeLines(positions, colors, 4.5, 0xffffff);
        layer.intervals.renderOrder = 2;
        layer.group.add(layer.intervals);
      }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(collarPositions, 3));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(collarColorsArr, 3));
    layer.points = new THREE.Points(
      geometry,
      new THREE.PointsMaterial({ size: 10, sizeAttenuation: false, vertexColors: true, map: this.pointTexture, transparent: true, alphaTest: 0.4 }),
    );
    layer.points.renderOrder = 3;
    layer.group.add(layer.points);

    this.drillholes.set(setId, layer);
    this.root.add(layer.group);
    this.contentChanged();
  }

  removeDrillholes(setId: number, update = true): void {
    const layer = this.drillholes.get(setId);
    if (!layer) return;
    this.root.remove(layer.group);
    disposeTree(layer.group, this.materials);
    this.drillholes.delete(setId);
    if (update) this.contentChanged();
  }

  setDrillholeStyle(setId: number, style: DrillholeStyle): void {
    const layer = this.drillholes.get(setId);
    if (!layer) return;
    layer.group.visible = style.visible;
    if (layer.paths) layer.paths.visible = style.paths;
    if (layer.intervals) layer.intervals.visible = style.intervals;
    if (layer.points) layer.points.visible = style.collars;
    this.requestRender();
  }

  /** `vertices` are float32 relative to `origin` (project coordinates), `triangles` are uint32 indices. */
  setWireframe(id: number, origin: number[], vertices: Float32Array, triangles: Uint32Array, color: string, opacity: number): void {
    this.removeWireframe(id, false);
    if (vertices.length === 0 || triangles.length === 0) return;
    let min = [Infinity, Infinity, Infinity];
    let max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < vertices.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const v = vertices[i + k] + origin[k];
        if (v < min[k]) min[k] = v;
        if (v > max[k]) max[k] = v;
      }
    }
    const o = this.ensureOrigin(min, max);
    const shift = [origin[0] - o.x, origin[1] - o.y, origin[2] - o.z];
    const positions = new Float32Array(vertices.length);
    for (let i = 0; i < vertices.length; i += 3) {
      positions[i] = vertices[i] + shift[0];
      positions[i + 1] = vertices[i + 1] + shift[1];
      positions[i + 2] = vertices[i + 2] + shift[2];
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(triangles, 1));
    geometry.computeVertexNormals();
    const material = new THREE.MeshLambertMaterial({ color, side: THREE.DoubleSide, flatShading: true });
    const mesh = new THREE.Mesh(geometry, material);
    this.applyOpacity(material, opacity);
    const layer: WireframeLayer = {
      extent: new THREE.Box3(new THREE.Vector3(...min).sub(o), new THREE.Vector3(...max).sub(o)),
      mesh,
      cut: null,
    };
    this.wireframes.set(id, layer);
    this.root.add(mesh);
    this.updateCut(layer);
    this.contentChanged();
  }

  removeWireframe(id: number, update = true): void {
    const layer = this.wireframes.get(id);
    if (!layer) return;
    this.root.remove(layer.mesh);
    layer.mesh.geometry.dispose();
    (layer.mesh.material as THREE.Material).dispose();
    this.removeCut(layer);
    this.wireframes.delete(id);
    if (update) this.contentChanged();
  }

  setWireframeStyle(id: number, color: string, visible: boolean, opacity: number): void {
    const layer = this.wireframes.get(id);
    if (!layer) return;
    const material = layer.mesh.material as THREE.MeshLambertMaterial;
    const recolor = !material.color.equals(new THREE.Color(color));
    material.color.set(color);
    this.applyOpacity(material, opacity);
    layer.mesh.visible = visible;
    if (recolor) this.updateCut(layer);
    if (layer.cut) layer.cut.visible = visible;
    this.requestRender();
  }

  /** `vertices` float32 relative to `origin`; `offsets` are line starts plus the end. */
  setLines(id: number, origin: number[], vertices: Float32Array, offsets: Uint32Array, color: string): void {
    this.removeLines(id, false);
    if (vertices.length < 6) return;
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < vertices.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const v = vertices[i + k] + origin[k];
        if (v < min[k]) min[k] = v;
        if (v > max[k]) max[k] = v;
      }
    }
    const o = this.ensureOrigin(min, max);
    const shift = [origin[0] - o.x, origin[1] - o.y, origin[2] - o.z];
    const positions: number[] = [];
    for (let l = 0; l + 1 < offsets.length; l++) {
      for (let i = offsets[l]; i + 1 < offsets[l + 1]; i++) {
        const a = i * 3;
        const b = (i + 1) * 3;
        positions.push(
          vertices[a] + shift[0], vertices[a + 1] + shift[1], vertices[a + 2] + shift[2],
          vertices[b] + shift[0], vertices[b + 1] + shift[1], vertices[b + 2] + shift[2],
        );
      }
    }
    if (positions.length === 0) return;
    const lines = this.makeLines(positions, null, 2, new THREE.Color(color).getHex());
    lines.renderOrder = 5;
    this.lineLayers.set(id, { extent: new THREE.Box3(new THREE.Vector3(...min).sub(o), new THREE.Vector3(...max).sub(o)), lines });
    this.root.add(lines);
    this.contentChanged();
  }

  removeLines(id: number, update = true): void {
    const layer = this.lineLayers.get(id);
    if (!layer) return;
    this.root.remove(layer.lines);
    disposeTree(layer.lines, this.materials);
    this.lineLayers.delete(id);
    if (update) this.contentChanged();
  }

  setLinesStyle(id: number, color: string, visible: boolean): void {
    const layer = this.lineLayers.get(id);
    if (!layer) return;
    (layer.lines.material as LineMaterial).color.set(color);
    layer.lines.visible = visible;
    this.requestRender();
  }

  /** A georeferenced image lying in plan; `corners` in project coordinates (TL, TR, BR, BL). */
  setRaster(id: number, url: string, corners: number[][], elevation: number | null, opacity: number, visible: boolean): void {
    this.removeRaster(id, false);
    const xs = corners.map((c) => c[0]);
    const ys = corners.map((c) => c[1]);
    const z = elevation ?? 0;
    this.ensureOrigin([Math.min(...xs), Math.min(...ys), z], [Math.max(...xs), Math.max(...ys), z]);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(12), 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 0], 2));
    geometry.setIndex([0, 3, 1, 1, 3, 2]);
    const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, color: 0xffffff });
    material.polygonOffset = true;
    material.polygonOffsetFactor = 1;
    material.polygonOffsetUnits = 1;
    const mesh = new THREE.Mesh(geometry, material);
    mesh.renderOrder = -1;
    const layer: RasterLayer = { corners, elevation, mesh, texture: null, visible };
    this.rasters.set(id, layer);
    this.applyOpacity(material, opacity);
    new THREE.TextureLoader().load(url, (texture) => {
      if (!this.rasters.has(id) || this.rasters.get(id) !== layer) {
        texture.dispose();
        return;
      }
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
      layer.texture = texture;
      material.map = texture;
      material.needsUpdate = true;
      this.requestRender();
    });
    this.placeRaster(layer);
    this.root.add(mesh);
    this.contentChanged();
  }

  removeRaster(id: number, update = true): void {
    const layer = this.rasters.get(id);
    if (!layer) return;
    this.root.remove(layer.mesh);
    layer.mesh.geometry.dispose();
    (layer.mesh.material as THREE.Material).dispose();
    layer.texture?.dispose();
    this.rasters.delete(id);
    if (update) this.contentChanged();
  }

  setRasterStyle(id: number, visible: boolean, opacity: number, elevation: number | null): void {
    const layer = this.rasters.get(id);
    if (!layer) return;
    layer.visible = visible;
    this.applyOpacity(layer.mesh.material as THREE.Material, opacity);
    if (layer.elevation !== elevation) {
      layer.elevation = elevation;
      this.placeRaster(layer);
    }
    layer.mesh.visible = visible && this.mode !== "section";
    this.requestRender();
  }

  hasLines(id: number): boolean {
    return this.lineLayers.has(id);
  }

  hasRaster(id: number): boolean {
    return this.rasters.has(id);
  }

  hasDrillholes(setId: number): boolean {
    return this.drillholes.has(setId);
  }

  hasWireframe(id: number): boolean {
    return this.wireframes.has(id);
  }

  // ---------------------------------------------------------------- view

  setVerticalExaggeration(value: number): void {
    this.vexag = value;
    this.root.scale.z = value;
    this.updateSectionLine();
    this.requestRender();
  }

  setGridVisible(visible: boolean): void {
    this.gridVisible = visible;
    if (this.grid) this.grid.visible = visible && this.mode !== "section";
    this.requestRender();
  }

  setSelected(hole: HoleRef | null): void {
    this.selected = hole;
    this.applySelected();
    this.requestRender();
  }

  /** The section line is drawn in 3D and plan; in section mode it clips the scene to its corridor. */
  setSection(section: SectionLine | null): void {
    this.section = section;
    this.updateSectionLine();
    this.applyClipping();
    if (this.mode === "section") this.fit();
    this.requestRender();
  }

  setMode(mode: SceneMode): void {
    if (mode === this.mode) return;
    this.mode = mode;
    this.camera = mode === "3d" ? this.perspective : this.ortho;
    // The controls take the camera's "up" when created: plan is north-up, section is Z-up.
    this.ortho.up.set(0, mode === "plan" ? 1 : 0, mode === "plan" ? 0 : 1);
    this.controls.dispose();
    this.controls = this.makeControls();
    if (this.grid) this.grid.visible = this.gridVisible && mode !== "section";
    // Rasters lie in plan; across a vertical section they would be a line.
    for (const layer of this.rasters.values()) layer.mesh.visible = layer.visible && mode !== "section";
    this.updateSectionLine();
    this.applyClipping();
    this.resize();
    this.fit();
  }

  get currentMode(): SceneMode {
    return this.mode;
  }

  /** Project coordinates of the scene centre (for a default section line). */
  centre(): { x: number; y: number } | null {
    const box = this.contentBox();
    if (!box || !this.origin) return null;
    const c = box.getCenter(new THREE.Vector3());
    return { x: c.x + this.origin.x, y: c.y + this.origin.y };
  }

  /** Screen position (CSS px inside the container) of a collar, or null if off screen. */
  screenPosition(hole: HoleRef): { x: number; y: number } | null {
    const collar = this.drillholes.get(hole.setId)?.collars.find((c) => c.holeId === hole.holeId);
    return collar ? this.project(collar.position) : null;
  }

  /** Fits everything visible into the view. `preset` applies to the 3D mode. */
  fit(preset: "iso" | "top" | "side" = "iso"): void {
    const box = this.contentBox() ?? new THREE.Box3(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50));
    const scaled = box.clone();
    scaled.min.z *= this.vexag;
    scaled.max.z *= this.vexag;
    const centre = scaled.getCenter(new THREE.Vector3());
    const size = scaled.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 10);
    const aspect = Math.max(this.container.clientWidth, 1) / Math.max(this.container.clientHeight, 1);

    if (this.mode === "3d") {
      const distance = (radius / Math.sin(THREE.MathUtils.degToRad(this.perspective.fov / 2))) * 1.05;
      const direction =
        preset === "top" ? new THREE.Vector3(0, -0.0001, 1)
        : preset === "side" ? new THREE.Vector3(0, -1, 0.02)
        : new THREE.Vector3(0.9, -1.2, 0.8);
      this.perspective.position.copy(centre).add(direction.normalize().multiplyScalar(distance));
      this.perspective.near = distance / 200;
      this.perspective.far = distance * 50;
      this.perspective.updateProjectionMatrix();
    } else {
      let target = centre;
      let half: number;
      let direction: THREE.Vector3;
      if (this.mode === "plan") {
        half = Math.max(size.y / 2, size.x / 2 / aspect) * 1.08;
        direction = new THREE.Vector3(0, 0, 1);
      } else {
        const s = this.section ?? { x0: centre.x, y0: centre.y, az: 90, halfWidth: 25, length: null };
        const n = sectionNormal(s.az);
        const along = sectionDirection(s.az);
        const o = this.origin ?? new THREE.Vector3();
        // Centre on the line, at the middle height of the content.
        const lineCentre = new THREE.Vector3(s.x0 - o.x, s.y0 - o.y, centre.z);
        const offset = along.dot(centre.clone().sub(lineCentre).setZ(0));
        target = lineCentre.add(along.clone().multiplyScalar(offset));
        const width = Math.abs(size.x * along.x) + Math.abs(size.y * along.y);
        half = Math.max(size.z / 2, width / 2 / aspect) * 1.08;
        // Look along -n, so the line direction points to the right of the screen.
        direction = n;
      }
      half = Math.max(half, 5);
      this.ortho.left = -half * aspect;
      this.ortho.right = half * aspect;
      this.ortho.top = half;
      this.ortho.bottom = -half;
      this.ortho.zoom = 1;
      const distance = radius * 4 + 100;
      this.ortho.position.copy(target).add(direction.multiplyScalar(distance));
      this.ortho.near = 0.1;
      this.ortho.far = distance * 2 + radius * 4;
      this.ortho.updateProjectionMatrix();
      centre.copy(target);
    }
    this.controls.target.copy(centre);
    this.controls.update();
    this.fitted = true;
    this.requestRender();
  }

  dispose(): void {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.cleanup.forEach((fn) => fn());
    for (const id of [...this.drillholes.keys()]) this.removeDrillholes(id, false);
    for (const id of [...this.wireframes.keys()]) this.removeWireframe(id, false);
    for (const id of [...this.lineLayers.keys()]) this.removeLines(id, false);
    for (const id of [...this.rasters.keys()]) this.removeRaster(id, false);
    this.pointTexture.dispose();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }

  // ---------------------------------------------------------------- internals

  private makeControls(): OrbitControls {
    const controls = new OrbitControls(this.camera, this.renderer.domElement);
    controls.screenSpacePanning = true;
    controls.enableRotate = this.mode === "3d";
    // Plan and section: left button pans, like a map.
    if (this.mode !== "3d") controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    controls.addEventListener("change", () => this.requestRender());
    return controls;
  }

  /** Everything visible (or everything, when nothing is visible). Rasters count only with `withRasters`. */
  private contentBox(withRasters = true): THREE.Box3 | null {
    const collect = (visibleOnly: boolean) => {
      const box = new THREE.Box3();
      for (const layer of this.drillholes.values()) if (!visibleOnly || layer.group.visible) box.union(layer.extent);
      for (const layer of this.wireframes.values()) if (!visibleOnly || layer.mesh.visible) box.union(layer.extent);
      for (const layer of this.lineLayers.values()) if (!visibleOnly || layer.lines.visible) box.union(layer.extent);
      if (withRasters) {
        for (const layer of this.rasters.values()) {
          if (visibleOnly && !layer.visible) continue;
          const p = layer.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
          for (let i = 0; i < 4; i++) box.expandByPoint(new THREE.Vector3(p.getX(i), p.getY(i), p.getZ(i)));
        }
      }
      return box;
    };
    let box = collect(true);
    if (box.isEmpty()) box = collect(false);
    return box.isEmpty() ? null : box;
  }

  /** Corners into scene coordinates; without an elevation the image goes just under the lowest object. */
  private placeRaster(layer: RasterLayer): void {
    const o = this.origin;
    if (!o) return;
    let z = layer.elevation !== null ? layer.elevation - o.z : 0;
    if (layer.elevation === null) {
      const box = this.contentBox(false);
      z = box ? box.min.z - 1 : 0;
    }
    const position = layer.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    layer.corners.forEach((c, i) => position.setXYZ(i, c[0] - o.x, c[1] - o.y, z));
    position.needsUpdate = true;
    layer.mesh.geometry.computeBoundingSphere();
  }

  private contentChanged(): void {
    for (const layer of this.rasters.values()) if (layer.elevation === null) this.placeRaster(layer);
    this.rebuildGrid();
    this.applySelected();
    this.updateSectionLine();
    if (!this.fitted) this.fit();
    this.requestRender();
  }

  private rebuildGrid(): void {
    if (this.grid) {
      this.root.remove(this.grid);
      this.grid.geometry.dispose();
      (this.grid.material as THREE.Material).dispose();
      this.grid = null;
    }
    const box = this.contentBox();
    if (!box) return;
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.y, 100) * 1.3;
    this.grid = new THREE.GridHelper(span, 20, 0x8a93a6, 0xbac0cc);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.set(centre.x, centre.y, box.min.z - 2);
    const material = this.grid.material as THREE.Material;
    material.transparent = true;
    material.opacity = 0.35;
    this.grid.visible = this.gridVisible && this.mode !== "section";
    this.root.add(this.grid);
  }

  private applyOpacity(material: THREE.Material, opacity: number): void {
    material.opacity = opacity;
    material.transparent = opacity < 1;
    material.depthWrite = opacity >= 1;
    material.needsUpdate = true;
  }

  private makeLines(positions: number[], colors: number[] | null, width: number, color: number): LineSegments2 {
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(positions);
    if (colors) geometry.setColors(colors);
    const material = new LineMaterial({ color, linewidth: width, vertexColors: colors !== null, worldUnits: false });
    material.resolution.set(this.container.clientWidth || 1, this.container.clientHeight || 1);
    this.materials.add(material);
    const lines = new LineSegments2(geometry, material);
    lines.frustumCulled = false;
    return lines;
  }

  private applySelected(): void {
    if (this.selectedLine) {
      this.root.remove(this.selectedLine);
      disposeTree(this.selectedLine, this.materials);
      this.selectedLine = null;
    }
    const layer = this.selected ? this.drillholes.get(this.selected.setId) : undefined;
    const path = layer && this.selected ? layer.pathOf.get(this.selected.holeId) : undefined;
    const o = this.origin;
    if (!path || path.length < 2 || !o) return;
    const positions: number[] = [];
    for (let i = 0; i + 1 < path.length; i++) {
      positions.push(path[i][0] - o.x, path[i][1] - o.y, path[i][2] - o.z, path[i + 1][0] - o.x, path[i + 1][1] - o.y, path[i + 1][2] - o.z);
    }
    this.selectedLine = this.makeLines(positions, null, 9, SELECT_COLOR);
    this.selectedLine.renderOrder = 1;
    this.root.add(this.selectedLine);
  }

  /** The section trace and its corridor, drawn on top of the content in 3D and plan. */
  private updateSectionLine(): void {
    if (this.sectionLine) {
      this.scene.remove(this.sectionLine);
      this.sectionLine.geometry.dispose();
      (this.sectionLine.material as THREE.Material).dispose();
      this.sectionLine = null;
    }
    const s = this.section;
    const box = this.contentBox();
    if (!s || !this.origin || !box || this.mode === "section") return;
    const along = sectionDirection(s.az);
    const n = sectionNormal(s.az);
    const size = box.getSize(new THREE.Vector3());
    const reach = s.length ?? Math.max(size.x, size.y) * 0.75 + 50;
    const start = s.length === null ? -reach : 0;
    const p0 = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
    const z = (this.mode === "plan" ? box.max.z + 5 : box.max.z) * this.vexag;
    const point = (u: number, d: number) => p0.clone().add(along.clone().multiplyScalar(u)).add(n.clone().multiplyScalar(d)).setZ(z);
    const pts: THREE.Vector3[] = [];
    const edge = (d: number) => pts.push(point(start, d), point(reach, d));
    edge(0);
    edge(-s.halfWidth);
    edge(s.halfWidth);
    const geometry = new THREE.BufferGeometry().setFromPoints(pts);
    this.sectionLine = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: SECTION_COLOR, depthTest: false, transparent: true, opacity: 0.9 }));
    this.sectionLine.renderOrder = 10;
    this.scene.add(this.sectionLine);
  }

  /** Section mode keeps only the corridor ±halfWidth around the line (and its length, if set). */
  private applyClipping(): void {
    for (const layer of this.wireframes.values()) this.updateCut(layer);
    const s = this.section;
    if (this.mode !== "section" || !s || !this.origin) {
      this.renderer.clippingPlanes = [];
      return;
    }
    const n = sectionNormal(s.az);
    const along = sectionDirection(s.az);
    const c = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
    // A fragment is kept where every plane's signed distance is >= 0.
    const planes = [
      new THREE.Plane(n.clone(), -n.dot(c) + s.halfWidth),
      new THREE.Plane(n.clone().negate(), n.dot(c) + s.halfWidth),
    ];
    if (s.length !== null) {
      planes.push(new THREE.Plane(along.clone(), -along.dot(c)));
      planes.push(new THREE.Plane(along.clone().negate(), along.dot(c) + s.length));
    }
    this.renderer.clippingPlanes = planes;
  }

  private removeCut(layer: WireframeLayer): void {
    if (!layer.cut) return;
    this.root.remove(layer.cut);
    disposeTree(layer.cut, this.materials);
    layer.cut = null;
  }

  /**
   * In section mode a surface seen edge-on would vanish, so every wireframe also gets its
   * intersection with the section plane drawn as a thick line in its own colour.
   */
  private updateCut(layer: WireframeLayer): void {
    this.removeCut(layer);
    const s = this.section;
    if (this.mode !== "section" || !s || !this.origin) return;
    const n = sectionNormal(s.az);
    const c = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
    const offset = n.dot(c);
    const position = layer.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    const index = layer.mesh.geometry.getIndex();
    if (!index) return;
    const p = position.array as Float32Array;
    const idx = index.array as Uint32Array;
    const dist = new Float32Array(position.count);
    for (let i = 0; i < position.count; i++) dist[i] = n.x * p[i * 3] + n.y * p[i * 3 + 1] - offset;
    const out: number[] = [];
    const corner = [0, 0, 0];
    for (let t = 0; t < idx.length; t += 3) {
      corner[0] = idx[t];
      corner[1] = idx[t + 1];
      corner[2] = idx[t + 2];
      const d0 = dist[corner[0]];
      const d1 = dist[corner[1]];
      const d2 = dist[corner[2]];
      if ((d0 >= 0 && d1 >= 0 && d2 >= 0) || (d0 < 0 && d1 < 0 && d2 < 0)) continue;
      let found = 0;
      for (let e = 0; e < 3 && found < 2; e++) {
        const a = corner[e];
        const b = corner[(e + 1) % 3];
        const da = dist[a];
        const db = dist[b];
        if (da >= 0 === db >= 0) continue;
        const k = da / (da - db);
        out.push(
          p[a * 3] + (p[b * 3] - p[a * 3]) * k,
          p[a * 3 + 1] + (p[b * 3 + 1] - p[a * 3 + 1]) * k,
          p[a * 3 + 2] + (p[b * 3 + 2] - p[a * 3 + 2]) * k,
        );
        found++;
      }
      if (found === 1) out.length -= 3;
    }
    if (out.length === 0) return;
    const color = (layer.mesh.material as THREE.MeshLambertMaterial).color.getHex();
    layer.cut = this.makeLines(out, null, 3, color);
    layer.cut.renderOrder = 4;
    layer.cut.visible = layer.mesh.visible;
    this.root.add(layer.cut);
  }

  private project(position: THREE.Vector3): { x: number; y: number } | null {
    const v = new THREE.Vector3(position.x, position.y, position.z * this.vexag).project(this.camera);
    if (v.z < -1 || v.z > 1) return null;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    return { x: (v.x * 0.5 + 0.5) * w, y: (-v.y * 0.5 + 0.5) * h };
  }

  private visibleInSection(position: THREE.Vector3): boolean {
    const s = this.section;
    if (this.mode !== "section" || !s || !this.origin) return true;
    const d = sectionNormal(s.az).dot(new THREE.Vector3(position.x - (s.x0 - this.origin.x), position.y - (s.y0 - this.origin.y), 0));
    return Math.abs(d) <= s.halfWidth;
  }

  private nearestCollar(clientX: number, clientY: number, maxDistance: number): Hover | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    let best: Hover | null = null;
    let bestDistance = maxDistance;
    for (const [setId, layer] of this.drillholes) {
      if (!layer.group.visible || !layer.points?.visible) continue;
      for (const c of layer.collars) {
        if (!this.visibleInSection(c.position)) continue;
        const s = this.project(c.position);
        if (!s) continue;
        const d = Math.hypot(s.x - px, s.y - py);
        if (d < bestDistance) {
          bestDistance = d;
          best = { setId, holeId: c.holeId, x: s.x, y: s.y };
        }
      }
    }
    return best;
  }

  /** Project coordinates of a screen point on the horizontal plane through the content (plan mode). */
  private groundPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    if (!this.origin) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, this.camera);
    const hit = raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3());
    return hit ? { x: hit.x + this.origin.x, y: hit.y + this.origin.y } : null;
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
      if (moved >= 4 || !quick) return;
      if (this.mode === "plan" && this.onPlanClick) {
        const p = this.groundPoint(e.clientX, e.clientY);
        if (p && this.onPlanClick(p.x, p.y)) return;
      }
      const hit = this.nearestCollar(e.clientX, e.clientY, 14);
      this.onPick(hit ? { setId: hit.setId, holeId: hit.holeId } : null);
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
    this.perspective.aspect = w / h;
    this.perspective.updateProjectionMatrix();
    const half = (this.ortho.top - this.ortho.bottom) / 2;
    this.ortho.left = -half * (w / h);
    this.ortho.right = half * (w / h);
    this.ortho.updateProjectionMatrix();
    for (const m of this.materials) m.resolution.set(w, h);
    this.requestRender();
  }

  private requestRender(): void {
    if (this.frameRequested || this.disposed) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      if (this.disposed) return;
      // A headlight: the directional light follows the camera, so faces toward the viewer are lit.
      this.light.position.copy(this.camera.position);
      this.light.target.position.copy(this.controls.target);
      this.renderer.render(this.scene, this.camera);
      this.onRender();
    });
  }
}

/** Unit vector along a section line (azimuth clockwise from north). */
export function sectionDirection(az: number): THREE.Vector3 {
  const a = THREE.MathUtils.degToRad(az);
  return new THREE.Vector3(Math.sin(a), Math.cos(a), 0);
}

/** Unit vector to the right of a section line. */
export function sectionNormal(az: number): THREE.Vector3 {
  const a = THREE.MathUtils.degToRad(az);
  return new THREE.Vector3(Math.cos(a), -Math.sin(a), 0);
}

function disposeTree(object: THREE.Object3D, materials: Set<LineMaterial>): void {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    mesh.geometry?.dispose();
    const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
    for (const m of Array.isArray(material) ? material : material ? [material] : []) {
      if (m instanceof LineMaterial) materials.delete(m);
      m.dispose();
    }
  });
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
