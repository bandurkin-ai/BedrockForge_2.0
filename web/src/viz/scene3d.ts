import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import type { IntervalsData, Trajectories, VerticalPlacement } from "../api";
import { NO_VALUE_COLOR, type ColorScale } from "./colorScale";
import type { DownholeBuild, Label } from "./downholeGeometry";
import type { MarkerShape } from "../charts/stripLog";
import { DEFAULT_COLLAR_STYLE, type CollarStyle } from "./collarStyle";
import { pointAt } from "./downholeGeometry";
import { DEFAULT_TRACE_STYLE, depthText, isLabelDepth, tickDepths, type TraceStyle } from "./traceStyle";
import type { SwathBuild } from "../stats/swath";
import { SwathLayer } from "./swathLayer";

export { DEFAULT_COLLAR_STYLE, type CollarStyle };
import type { SectionLine } from "./geometry";
import { bounds, type Bounds } from "./geometryLoader";
import { THEME_EVENT } from "./scene3d-theme";

const PATH_COLOR = 0x8a93a6;
const SELECT_COLOR = 0xff7a00;
const SECTION_COLOR = 0x2e6fbd;
const BLOCK_BOX = new THREE.BoxGeometry(1, 1, 1);
const DRAFT_COLOR = 0xff8c1a;

/** Plan overview for the section map (project coordinates). */
export interface PlanOverview {
  holes: [number, number][][];
  collars: [number, number][];
  /** x1, y1, x2, y2 */
  segments: [number, number, number, number][];
  /** min x, min y, max x, max y */
  boxes: [number, number, number, number][];
}

/** Camera presets: they only turn the view. "section" looks at the section plane with Z up. */
export type ViewPreset = "top" | "front" | "side" | "iso" | "section";

export interface HoleRef {
  setId: number;
  holeId: string;
}

export interface Hover extends HoleRef {
  x: number;
  y: number;
}

interface LabelFont {
  size: number;
  family: string;
  bold: boolean;
  color: string;
  /** the ring around the letters (default: dark, for light text over anything) */
  halo?: string;
}

const DEFAULT_FONT: LabelFont = { size: 12, family: "sans-serif", bold: false, color: "#f3f6fb" };
/** No more hole names than this are written (each is a sprite). */
const MAX_COLLAR_LABELS = 3000;

export interface DrillholeStyle {
  visible: boolean;
  paths: boolean;
  intervals: boolean;
  collars: boolean;
}

/** Data along the holes (tubes, panels, graph lines, text) of one saved display. */
interface DownholeLayer {
  group: THREE.Group;
  tubes: LineSegments2[];
  panels: THREE.Mesh | null;
  lines: THREE.LineSegments | null;
  labels: { sprite: THREE.Sprite; label: Label; wpx: number; hpx: number }[];
  labelGroup: THREE.Group;
  /** panels and lines each carry a side direction found by the shader from the camera */
  materials: THREE.Material[];
  /** width of the default panel column, m, and the factor that keeps it wide enough on screen */
  panelWidth: number;
  scale: { value: number };
}

/** A mark on a hole trace: a crossing with a wireframe, or where it enters or leaves a block model. */
export interface TraceMark {
  /** project coordinates */
  xyz: [number, number, number];
  color: string;
  shape: MarkerShape;
  label: string | null;
}

/** No more ticks and written depths than this per set (each written depth is a sprite). */
const MAX_TICKS = 200_000;
const MAX_TICK_LABELS = 5000;
const MAX_MARK_LABELS = 3000;

/** A label shows when its interval is at least this long on screen, px. */
const LABEL_MIN_PX = 18;
/** Panels are in metres, but the default column is never narrower than this on screen, px. */
const PANEL_MIN_PX = 26;

interface DrillholeLayer {
  extent: THREE.Box3;
  group: THREE.Group;
  paths: LineSegments2 | null;
  intervals: LineSegments2 | null;
  points: THREE.Points | null;
  collars: { holeId: string; position: THREE.Vector3 }[];
  pathOf: Map<string, number[][]>;
  /** r, g, b per collar by the audit */
  auditColors: number[];
  labelGroup: THREE.Group;
  labels: { sprite: THREE.Sprite; position: THREE.Vector3; wpx: number; hpx: number }[];
  /** shift of the labels to the right of the marker, px */
  labelShift: number;
  /** depth ticks and marks of crossings, with their written depths and names */
  marks: THREE.Group;
  markLabelGroup: THREE.Group;
  markLabels: { sprite: THREE.Sprite; position: THREE.Vector3; wpx: number; hpx: number; shift: number; priority: number }[];
  /** metres per pixel at the view's target: ticks keep their length on screen */
  tickScale: { value: number };
  hasTicks: boolean;
}

/** One level of detail of a wireframe: a mesh, or (full detail) a group of parts. */
interface WireframeLevel {
  object: THREE.Object3D;
  triangles: number;
  geometries: THREE.BufferGeometry[];
}

interface WireframeLayer {
  extent: THREE.Box3;
  /** Placed at the wireframe's origin; holds its levels and its section line. */
  group: THREE.Group;
  material: THREE.MeshLambertMaterial;
  /** 0: full detail; 1, 2, …: simplified copies, coarser and coarser. */
  levels: Map<number, WireframeLevel>;
  /** Bounding sphere in the group's coordinates (for choosing the level). */
  sphere: THREE.Sphere;
  /** The exact section (from the server): flat x, y, z per polyline, relative to the origin. */
  cutLines: { closed: boolean; points: number[][] }[] | null;
  cut: LineSegments2 | null;
  /** Project coordinates the vertices are relative to. */
  origin: number[];
  /** The raster draped on the surface (a terrain model): shader values and the loaded image. */
  drape: DrapeUniforms;
  drapeTexture: THREE.Texture | null;
  drapeUrl: string | null;
}

/** A raster laid on a terrain model: the image follows the relief. */
export interface Drape {
  rasterId: number;
  url: string;
  /** Corners of the image in project coordinates: top-left, top-right, bottom-right, bottom-left. */
  corners: number[][];
  /** The image is multiplied by this colour ("#ffffff": as is). */
  tint: string;
  /** How much of the image covers the surface's own colour, 0…1. */
  opacity: number;
  /** Light and shadow of the relief on the image. */
  shade: boolean;
  /** Hide the part of the raster outside the surface (otherwise it stays flat around it). */
  clip: boolean;
}

interface DrapeUniforms {
  uDrapeMap: { value: THREE.Texture | null };
  uDrapeU: { value: THREE.Vector3 };
  uDrapeV: { value: THREE.Vector3 };
  uDrapeTint: { value: THREE.Color };
  uDrapeMix: { value: number };
  uDrapeShade: { value: number };
}

/**
 * Image coordinates (u, v) of a point as two rows of numbers: u = a·x + b·y + c. `corners` are
 * the image's top-left, top-right, bottom-right, bottom-left; x, y are relative to `origin`.
 */
export function imageAxes(corners: number[][], origin: number[]): { u: [number, number, number]; v: [number, number, number] } | null {
  const [tl, , br, bl] = corners;
  const e1 = [br[0] - bl[0], br[1] - bl[1]];
  const e2 = [tl[0] - bl[0], tl[1] - bl[1]];
  const det = e1[0] * e2[1] - e2[0] * e1[1];
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
  const bx = bl[0] - origin[0];
  const by = bl[1] - origin[1];
  return {
    u: [e2[1] / det, -e2[0] / det, -(e2[1] * bx - e2[0] * by) / det],
    v: [-e1[1] / det, e1[0] / det, (e1[1] * bx - e1[0] * by) / det],
  };
}

/** The part of an image (u0, v0, u1, v1) covered by a box in plan, or null when they do not meet. */
export function imageWindow(corners: number[][], min: number[], max: number[]): [number, number, number, number] | null {
  const axes = imageAxes(corners, [0, 0]);
  if (!axes) return null;
  const at = (x: number, y: number) => [axes.u[0] * x + axes.u[1] * y + axes.u[2], axes.v[0] * x + axes.v[1] * y + axes.v[2]];
  const points = [at(min[0], min[1]), at(max[0], min[1]), at(max[0], max[1]), at(min[0], max[1])];
  const u = points.map((p) => p[0]);
  const v = points.map((p) => p[1]);
  const window: [number, number, number, number] = [Math.min(...u), Math.min(...v), Math.max(...u), Math.max(...v)];
  return window[0] >= 1 || window[1] >= 1 || window[2] <= 0 || window[3] <= 0 ? null : window;
}

/** Most surfaces one flat raster is cut out for (the rest of them only cover it). */
const MAX_RASTER_HOLES = 4;

/** A level is drawn when it has at most this many triangles per pixel of the object's disc on screen. */
const TRIANGLES_PER_PIXEL = 2;

interface LineLayer3D {
  extent: THREE.Box3;
  /** Placed at the layer's origin: the lines and the single points of the layer. */
  group: THREE.Group;
  lines: LineSegments2 | null;
  points: THREE.Points | null;
}

/** What the string editor draws over the scene; all in project coordinates (float64). */
export interface EditOverlay {
  color: string;
  /** x, y, z pairs: 6 numbers per segment of the working copy */
  segments: Float64Array;
  /** segments of the selected lines (drawn thicker) */
  selected: Float64Array;
  /** x, y, z of every vertex */
  vertices: Float64Array;
  selectedVertices: Float64Array;
  /** the rubber band to the cursor */
  draft: Float64Array;
  /** where the next point goes (the snap target) */
  marker: number[] | null;
}

/** A pointer event offered to the editor before the scene uses it. */
export interface ScenePointer {
  type: "down" | "move" | "up" | "click" | "dblclick";
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  button: number;
  shift: boolean;
  ctrl: boolean;
}

interface BlockLayer {
  extent: THREE.Box3;
  group: THREE.Group;
  /** centre (relative to the group) and size of every block */
  render: Float32Array;
  mesh: THREE.InstancedMesh | null;
  material: THREE.MeshLambertMaterial;
  /** block number of every instance */
  index: Int32Array;
  /** rgb per block; which blocks pass the restriction; which of them are drawn as boxes (the shell, thinned) */
  colors: Uint8Array | null;
  all: Uint8Array | null;
  drawn: Uint8Array | null;
  /** the boxes do not match `colors` and `drawn` any more */
  stale: boolean;
  shown: boolean;
  opacity: number;
  /** in a clipped section: the cut of the blocks by the section plane instead of the boxes */
  slice: boolean;
  sliceMesh: THREE.Mesh | null;
  sliceMaterial: THREE.MeshBasicMaterial;
  /** block number of every quad of the cut */
  sliceIndex: Int32Array;
  edges: BlockEdgeUniforms;
}

/** Outlines of blocks, drawn in the fragment shader of their faces (any number of blocks costs nothing more). */
interface BlockEdgeUniforms {
  /** half of the line width, px; 0: no outlines */
  width: { value: number };
  color: { value: THREE.Color };
  /** the cut drawn as an underlay: its colour mixed toward the background instead of blending */
  fade: { value: number };
  back: { value: THREE.Color };
}

export interface BlockDisplay {
  /** rgb 0–255 per block */
  colors: Uint8Array;
  /** 1 per block that passes the restriction (the cut of a section takes these) */
  all: Uint8Array;
  /** 1 per block drawn as a box (the outer shell, or all of them) */
  drawn: Uint8Array;
  opacity: number;
  shown: boolean;
  slice: boolean;
  /** outlines: colour and width in px, or null */
  edges: { color: string; width: number } | null;
}

/**
 * Adds outlines to a material whose faces carry uv 0..1 (a box face, a quad of a cut): a fragment
 * closer to the border of its face than the line width takes the line colour. The width is in
 * pixels (screen-space derivatives); faces only a few pixels across get no outline, so a model
 * seen from afar does not turn into the colour of its lines.
 */
function outlineFaces(material: THREE.Material, u: BlockEdgeUniforms): void {
  material.defines = { ...(material.defines ?? {}), USE_UV: "" };
  material.onBeforeCompile = (shader) => {
    shader.uniforms.bfEdgeWidth = u.width;
    shader.uniforms.bfEdgeColor = u.color;
    shader.uniforms.bfFade = u.fade;
    shader.uniforms.bfBack = u.back;
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float bfEdgeWidth;\nuniform vec3 bfEdgeColor;\nuniform float bfFade;\nuniform vec3 bfBack;")
      .replace("#include <opaque_fragment>", `if (bfEdgeWidth > 0.0) {
  vec2 bfPx = 1.0 / max(fwidth(vUv), vec2(1e-7));
  vec2 bfD = min(vUv, 1.0 - vUv) * bfPx;
  float bfLine = 1.0 - smoothstep(bfEdgeWidth - 0.5, bfEdgeWidth + 0.5, min(bfD.x, bfD.y));
  float bfFace = min(bfPx.x, bfPx.y);
  bfLine *= clamp((bfFace - 4.0 * bfEdgeWidth - 2.0) / (4.0 * bfEdgeWidth + 2.0), 0.0, 1.0);
  outgoingLight = mix(outgoingLight, bfEdgeColor, bfLine);
}
outgoingLight = mix(bfBack, outgoingLight, bfFade);
#include <opaque_fragment>`);
  };
  material.customProgramCacheKey = () => "bf-block-edges";
}

/** Above this many shown blocks the model is not drawn (stage 12.5: surfaces of filtered blocks). */
export const MAX_SHOWN_BLOCKS = 3_000_000;

interface RasterLayer {
  /** Project coordinates of the corners: top-left, top-right, bottom-right, bottom-left. */
  corners: number[][];
  /** null: placed just under the lowest object of the scene. */
  elevation: number | null;
  /** A raster standing in a vertical plane: corners with Z (then `elevation` is not used). */
  corners3d: number[][] | null;
  opacity: number;
  mesh: THREE.Mesh;
  texture: THREE.Texture | null;
  visible: boolean;
  /** Windows of the image (u0, v0, u1, v1) not drawn: there the raster lies on a terrain model. */
  holes: { uHoles: { value: THREE.Vector4[] }; uHoleCount: { value: number } };
}

/**
 * The one 3D view of the project: every object in a shared space, always free to rotate.
 * View buttons only turn the camera (plan of what is on screen, sides Z/X and Z/Y, the section plane);
 * a section line optionally clips the scene to its window (towards / away from the viewer).
 *
 * Z is up. Coordinates are shifted to a scene origin (the first loaded object), because
 * UTM-sized numbers (4 500 000) do not fit float32 without visible jitter.
 * Renders on demand, so an idle tab costs nothing.
 */
export class GeoScene {
  onPick: (hole: HoleRef | null) => void = () => {};
  onHover: (hover: Hover | null) => void = () => {};
  /** A click on the ground (project coordinates); returns true when it was used, e.g. to draw a section line. */
  onGroundClick: ((x: number, y: number) => boolean) | null = null;
  /** called after every render, e.g. to move HTML labels */
  onRender: () => void = () => {};

  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private perspective = new THREE.PerspectiveCamera(45, 1, 1, 1000);
  private ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
  private camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = this.ortho;
  private controls: OrbitControls;
  private root = new THREE.Group();
  private light = new THREE.DirectionalLight(0xffffff, 1.6);
  private materials = new Set<LineMaterial>();
  private drillholes = new Map<number, DrillholeLayer>();
  private downholes = new Map<string, DownholeLayer>();
  /** Camera directions for the shader of panels (world space), updated before each render. */
  private side = { view: { value: new THREE.Vector3(0, 0, -1) }, right: { value: new THREE.Vector3(1, 0, 0) }, vexag: { value: 1 } };
  private labelMaterials = new Map<string, THREE.SpriteMaterial>();
  private markerTextures = new Map<MarkerShape, THREE.CanvasTexture>();
  private collarStyles = new Map<number, CollarStyle>();
  private traceStyles = new Map<number, { style: TraceStyle; marks: TraceMark[] }>();
  private wireframes = new Map<number, WireframeLayer>();
  private lineLayers = new Map<number, LineLayer3D>();
  private rasters = new Map<number, RasterLayer>();
  /** Rasters draped on terrain models, by wireframe (kept while the wireframe is not loaded yet). */
  private drapes = new Map<number, Drape>();
  private blocks = new Map<number, BlockLayer>();
  /** Swath plots: the cuboid cut into swaths and the plot over it, by the id of the plot. */
  private swaths = new Map<number, SwathLayer>();
  /** A click on a block (when no collar is near the cursor). */
  onPickBlock: ((pick: { modelId: number; index: number } | null) => boolean) | null = null;
  /** A wireframe clicked in the view; true when the click is taken. */
  onPickWireframe: ((id: number, ray: { origin: [number, number, number]; dir: [number, number, number] } | null) => boolean) | null = null;
  /** The string editor: gets pointer events first; true = taken (no rotation, no picking). */
  onEditPointer: ((e: ScenePointer) => boolean) | null = null;
  /** The camera moved or the view was resized. */
  onCamera: () => void = () => {};
  private overlay: THREE.Group | null = null;
  private selectedWireframe: number | null = null;
  private selectedLine: LineSegments2 | null = null;
  private sectionLine: THREE.LineSegments | null = null;
  /** The section line being drawn: its first point (rendered space) and the rubber band to the cursor. */
  private draftStart: THREE.Vector3 | null = null;
  private draftLine: THREE.LineSegments | null = null;
  private grid: THREE.GridHelper | null = null;
  private pointTexture: THREE.CanvasTexture;
  private origin: THREE.Vector3 | null = null;
  private vexag = 1;
  private clip = false;
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
      auditColors: [],
      labelGroup: new THREE.Group(),
      labels: [],
      labelShift: 8,
      marks: new THREE.Group(),
      markLabelGroup: new THREE.Group(),
      markLabels: [],
      tickScale: { value: 1 },
      hasTicks: false,
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
    layer.auditColors = collarColorsArr;
    layer.group.add(layer.labelGroup);
    layer.group.add(layer.marks);

    this.drillholes.set(setId, layer);
    this.root.add(layer.group);
    this.applyCollarStyle(setId);
    this.applyTraceStyle(setId);
    this.contentChanged();
  }

  /** The marker of the collars (shape, colour, size) and the labels with hole names. */
  /**
   * Hole traces of a set: width and colour of the line, depth ticks with written depths, and marks
   * where the holes cross objects (computed on the server, filtered by the caller).
   */
  setTraceStyle(setId: number, style: TraceStyle, marks: TraceMark[] = []): void {
    this.traceStyles.set(setId, { style, marks });
    this.applyTraceStyle(setId);
    this.requestRender();
  }

  private applyTraceStyle(setId: number): void {
    const layer = this.drillholes.get(setId);
    if (!layer || !this.origin) return;
    const { style, marks } = this.traceStyles.get(setId) ?? { style: DEFAULT_TRACE_STYLE, marks: [] };
    if (layer.paths) {
      layer.paths.material.linewidth = style.width;
      layer.paths.material.color.set(style.color);
      layer.paths.material.needsUpdate = true;
    }
    // Marks are rebuilt: sprites first (their materials are shared and stay in the cache).
    layer.markLabelGroup.clear();
    layer.marks.remove(layer.markLabelGroup);
    disposeTree(layer.marks, this.materials);
    layer.marks.clear();
    layer.markLabels = [];
    layer.marks.add(layer.markLabelGroup);
    layer.markLabelGroup.renderOrder = 10;
    layer.hasTicks = false;
    const o = this.origin;
    /** priority: 2 — crossings, 1 — the depth at the end of a hole, 0 — tick depths (kept first when labels compete) */
    const addLabel = (text: string, at: THREE.Vector3, font: LabelFont, shift: number, priority = 0) => {
      const { material, wpx, hpx } = this.labelMaterial(text, font);
      const sprite = new THREE.Sprite(material);
      sprite.renderOrder = 10;
      sprite.frustumCulled = false;
      sprite.position.copy(at);
      sprite.center.set(-shift / wpx, 0.5);
      layer.markLabelGroup.add(sprite);
      layer.markLabels.push({ sprite, position: at, wpx, hpx, shift, priority });
    };

    if (style.ticks || style.endDepth) {
      const positions: number[] = [];
      const tangents: number[] = [];
      const amps: number[] = [];
      const font: LabelFont = { size: style.labelSize, family: "sans-serif", bold: false, color: style.labelColor };
      const endFont: LabelFont = { ...font, bold: true };
      // The end of a hole: a longer tick, so it reads as the bottom and not as one more step.
      const endSize = Math.max(6, style.tickSize * 1.75);
      let labels = 0;
      let endLabels = 0;
      const tick = (path: number[][], d: number, length: number, size: number) => {
        const p = pointAt(path, d);
        const a = pointAt(path, Math.max(0, d - 0.5));
        const b = pointAt(path, Math.min(length, d + 0.5));
        const t = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        const tl = Math.hypot(t[0], t[1], t[2]) || 1;
        for (const amp of [-size, size]) {
          positions.push(p[0] - o.x, p[1] - o.y, p[2] - o.z);
          tangents.push(t[0] / tl, t[1] / tl, t[2] / tl);
          amps.push(amp);
        }
        return new THREE.Vector3(p[0] - o.x, p[1] - o.y, p[2] - o.z);
      };
      for (const [, path] of layer.pathOf) {
        if (path.length < 2 || path[0].length < 4) continue;
        const length = path[path.length - 1][3];
        if (style.ticks) {
          for (const d of tickDepths(length, style.tickStep)) {
            if (positions.length / 6 >= MAX_TICKS) break;
            if (style.endDepth && d >= length - 1e-6) continue; // the end has its own tick and depth
            const at = tick(path, d, length, style.tickSize);
            if (style.tickLabels && labels < MAX_TICK_LABELS && isLabelDepth(d, style.labelStep)) {
              labels += 1;
              addLabel(depthText(d), at, font, style.tickSize + 3);
            }
          }
        }
        if (style.endDepth && length > 0) {
          const at = tick(path, length, length, endSize);
          if (endLabels < MAX_TICK_LABELS) {
            endLabels += 1;
            addLabel(depthText(length), at, endFont, endSize + 3, 1);
          }
        }
      }
      if (positions.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
        g.setAttribute("aTan", new THREE.Float32BufferAttribute(tangents, 3));
        g.setAttribute("aAmp", new THREE.Float32BufferAttribute(amps, 1));
        const material = this.sideMaterial(new THREE.LineBasicMaterial({ color: style.color }), layer.tickScale);
        const ticks = new THREE.LineSegments(g, material);
        ticks.frustumCulled = false;
        ticks.renderOrder = 2;
        layer.marks.add(ticks);
        layer.hasTicks = true;
      }
    }

    if (marks.length) {
      // One points object per colour and shape.
      const groups = new Map<string, { shape: MarkerShape; color: THREE.Color; positions: number[] }>();
      const font: LabelFont = { size: Math.max(9, style.labelSize), family: "sans-serif", bold: false, color: style.labelColor };
      let labels = 0;
      for (const m of marks) {
        const key = `${m.shape}|${m.color}`;
        let g = groups.get(key);
        if (!g) groups.set(key, (g = { shape: m.shape, color: new THREE.Color(m.color), positions: [] }));
        const at = new THREE.Vector3(m.xyz[0] - o.x, m.xyz[1] - o.y, m.xyz[2] - o.z);
        g.positions.push(at.x, at.y, at.z);
        if (m.label && labels < MAX_MARK_LABELS) {
          labels += 1;
          addLabel(m.label, at, font, style.markerSize / 2 + 4, 2);
        }
      }
      for (const g of groups.values()) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(g.positions, 3));
        const points = new THREE.Points(geometry, new THREE.PointsMaterial({
          size: style.markerSize, sizeAttenuation: false, color: g.color, map: this.markerTexture(g.shape), transparent: true, alphaTest: 0.4, depthTest: false,
        }));
        points.frustumCulled = false;
        points.renderOrder = 5;
        layer.marks.add(points);
      }
    }
    // Crossings are placed before depths when labels compete for room.
    layer.markLabels.sort((a, b) => b.priority - a.priority);
    layer.marks.visible = layer.paths?.visible ?? true;
  }

  setCollarStyle(setId: number, style: CollarStyle): void {
    this.collarStyles.set(setId, style);
    this.applyCollarStyle(setId);
    this.requestRender();
  }

  private applyCollarStyle(setId: number): void {
    const layer = this.drillholes.get(setId);
    const style = this.collarStyles.get(setId) ?? DEFAULT_COLLAR_STYLE;
    if (!layer?.points) return;
    const material = layer.points.material as THREE.PointsMaterial;
    material.size = style.size;
    material.map = this.markerTexture(style.shape);
    material.needsUpdate = true;
    const colors = layer.points.geometry.getAttribute("color") as THREE.BufferAttribute;
    const one = style.color === "audit" ? null : new THREE.Color(style.color);
    for (let i = 0; i < colors.count; i++) {
      if (one) colors.setXYZ(i, one.r, one.g, one.b);
      else colors.setXYZ(i, layer.auditColors[i * 3], layer.auditColors[i * 3 + 1], layer.auditColors[i * 3 + 2]);
    }
    colors.needsUpdate = true;
    // labels: rebuilt, their materials are shared and stay in the cache
    layer.labelGroup.clear();
    layer.labels = [];
    layer.labelShift = style.size / 2 + 3;
    if (style.label) {
      const font: LabelFont = { size: style.fontSize, family: style.fontFamily, bold: style.bold, color: style.labelColor };
      for (const c of layer.collars.slice(0, MAX_COLLAR_LABELS)) {
        const { material: m, wpx, hpx } = this.labelMaterial(c.holeId, font);
        const sprite = new THREE.Sprite(m);
        sprite.renderOrder = 10;
        sprite.frustumCulled = false;
        sprite.position.copy(c.position);
        layer.labelGroup.add(sprite);
        layer.labels.push({ sprite, position: c.position, wpx, hpx });
      }
    }
    layer.labelGroup.visible = layer.points.visible;
  }

  private markerTexture(shape: MarkerShape): THREE.CanvasTexture {
    let texture = this.markerTextures.get(shape);
    if (!texture) {
      texture = shape === "circle" ? this.pointTexture : makeMarkerTexture(shape);
      this.markerTextures.set(shape, texture);
    }
    return texture;
  }

  /** What the scene holds, by kind: the view compares it with the project, so nothing deleted stays drawn. */
  loadedIds(): { sets: number[]; wireframes: number[]; lines: number[]; rasters: number[]; blocks: number[]; downholes: string[]; swaths: number[] } {
    return {
      sets: [...this.drillholes.keys()],
      wireframes: [...this.wireframes.keys()],
      lines: [...this.lineLayers.keys()],
      rasters: [...this.rasters.keys()],
      blocks: [...this.blocks.keys()],
      downholes: [...this.downholes.keys()],
      swaths: [...this.swaths.keys()],
    };
  }

  /** The scene origin (project coordinates), null until the first object is loaded. */
  sceneOrigin(extent?: { min: number[]; max: number[] } | null): [number, number, number] | null {
    if (extent) this.ensureOrigin(extent.min, extent.max);
    return this.origin ? [this.origin.x, this.origin.y, this.origin.z] : null;
  }

  /**
   * Data along the holes of one display: colour tubes along the hole, panels beside it (bars, graph lines,
   * colour ribbons) and text. `build` is relative to the scene origin; null removes the display.
   */
  setDownhole(key: string, build: DownholeBuild | null, visible = true): void {
    this.removeDownhole(key, false);
    if (!build) {
      this.requestRender();
      return;
    }
    const layer: DownholeLayer = { group: new THREE.Group(), tubes: [], panels: null, lines: null, labels: [], labelGroup: new THREE.Group(), materials: [], panelWidth: build.panelWidth, scale: { value: 1 } };
    layer.group.visible = visible;
    build.tubes.forEach((t, i) => {
      if (t.positions.length === 0) return;
      const lines = this.makeLines(t.positions, t.colors, t.width, 0xffffff);
      if (t.opacity < 1) {
        lines.material.transparent = true;
        lines.material.opacity = t.opacity;
      }
      lines.renderOrder = 2 + i * 0.1;
      layer.tubes.push(lines);
      layer.group.add(lines);
    });
    const sideGeometry = (m: DownholeBuild["panels"]) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(m.positions, 3));
      g.setAttribute("aTan", new THREE.Float32BufferAttribute(m.tangents, 3));
      g.setAttribute("aAmp", new THREE.Float32BufferAttribute(m.amps, 1));
      g.setAttribute("color", new THREE.Float32BufferAttribute(m.colors, 4));
      if (m.index.length) g.setIndex(m.index);
      return g;
    };
    if (build.panels.amps.length > 0) {
      const material = this.sideMaterial(new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true, depthWrite: false }), layer.scale);
      layer.materials.push(material);
      layer.panels = new THREE.Mesh(sideGeometry(build.panels), material);
      layer.panels.frustumCulled = false;
      layer.panels.renderOrder = 1;
      layer.group.add(layer.panels);
    }
    if (build.lines.amps.length > 0) {
      const material = this.sideMaterial(new THREE.LineBasicMaterial({ vertexColors: true, transparent: true }), layer.scale);
      layer.materials.push(material);
      layer.lines = new THREE.LineSegments(sideGeometry(build.lines), material);
      layer.lines.frustumCulled = false;
      layer.lines.renderOrder = 2;
      layer.group.add(layer.lines);
    }
    for (const p of build.points) {
      if (p.amps.length === 0) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(p.positions, 3));
      g.setAttribute("aTan", new THREE.Float32BufferAttribute(p.tangents, 3));
      g.setAttribute("aAmp", new THREE.Float32BufferAttribute(p.amps, 1));
      g.setAttribute("color", new THREE.Float32BufferAttribute(p.colors, 3));
      const material = this.sideMaterial(new THREE.PointsMaterial({
        size: p.size, sizeAttenuation: false, vertexColors: true, map: this.markerTexture("circle"), transparent: true, alphaTest: 0.3, opacity: p.opacity,
      }), layer.scale);
      layer.materials.push(material);
      const points = new THREE.Points(g, material);
      points.frustumCulled = false;
      points.renderOrder = 3;
      layer.group.add(points);
    }
    for (const m of build.markers) {
      if (m.positions.length === 0) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(m.positions, 3));
      g.setAttribute("color", new THREE.Float32BufferAttribute(m.colors, 3));
      const points = new THREE.Points(g, new THREE.PointsMaterial({
        size: m.size, sizeAttenuation: false, vertexColors: true, map: this.markerTexture(m.shape), transparent: true, alphaTest: 0.4, depthTest: false, opacity: m.opacity,
      }));
      points.frustumCulled = false;
      points.renderOrder = 4;
      layer.group.add(points);
    }
    layer.labelGroup.renderOrder = 10;
    for (const label of build.labels) {
      const font = label.size || label.color ? { ...DEFAULT_FONT, size: label.size ?? DEFAULT_FONT.size, color: label.color ?? DEFAULT_FONT.color } : DEFAULT_FONT;
      const { material, wpx, hpx } = this.labelMaterial(label.text, font);
      const sprite = new THREE.Sprite(material);
      sprite.center.set(label.px ? -label.px / wpx : label.right ? 1 : 0, 0.5);
      sprite.renderOrder = 10;
      sprite.frustumCulled = false;
      layer.labelGroup.add(sprite);
      layer.labels.push({ sprite, label, wpx, hpx });
    }
    layer.group.add(layer.labelGroup);
    this.downholes.set(key, layer);
    this.root.add(layer.group);
    this.requestRender();
  }

  setDownholeVisible(key: string, visible: boolean): void {
    const layer = this.downholes.get(key);
    if (!layer) return;
    layer.group.visible = visible;
    this.requestRender();
  }

  hasDownhole(key: string): boolean {
    return this.downholes.has(key);
  }

  removeDownhole(key: string, update = true): void {
    const layer = this.downholes.get(key);
    if (!layer) return;
    this.root.remove(layer.group);
    layer.group.traverse((child) => {
      if (child instanceof THREE.Sprite) return; // labels share their materials
      const m = child as THREE.Mesh;
      m.geometry?.dispose();
      const material = m.material as THREE.Material | undefined;
      if (material instanceof LineMaterial) this.materials.delete(material);
      material?.dispose();
    });
    this.downholes.delete(key);
    if (update) this.requestRender();
  }

  /** A material whose vertices move `aAmp` metres to the side of the hole, across it and in the plane of the screen. */
  private sideMaterial<T extends THREE.Material>(material: T, scale: { value: number }): T {
    const side = this.side;
    material.onBeforeCompile = (shader) => {
      shader.uniforms.uView = side.view;
      shader.uniforms.uRight = side.right;
      shader.uniforms.uVexag = side.vexag;
      shader.uniforms.uScale = scale;
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nattribute vec3 aTan;\nattribute float aAmp;\nuniform vec3 uView;\nuniform vec3 uRight;\nuniform float uVexag;\nuniform float uScale;")
        .replace(
          "#include <begin_vertex>",
          `#include <begin_vertex>
          vec3 tw = normalize(vec3(aTan.xy, aTan.z * uVexag));
          vec3 sw = cross(tw, uView);
          float sl = length(sw);
          sw = sl < 0.05 ? uRight : sw / sl;
          if (dot(sw, uRight) < 0.0) sw = -sw;
          transformed += vec3(sw.xy, sw.z / uVexag) * aAmp * uScale;`,
        );
    };
    material.customProgramCacheKey = () => "downhole-side";
    return material;
  }

  private labelMaterial(text: string, f: LabelFont = DEFAULT_FONT): { material: THREE.SpriteMaterial; wpx: number; hpx: number } {
    const key = `${f.size}|${f.family}|${f.bold ? "b" : ""}|${f.color}|${f.halo ?? ""}|${text}`;
    const cached = this.labelMaterials.get(key);
    const size = f.size;
    if (cached) return { material: cached, wpx: cached.userData.wpx, hpx: cached.userData.hpx };
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const k = 2;
    const font = `${f.bold ? "bold " : ""}${size * k}px ${f.family}`;
    if (ctx) {
      ctx.font = font;
    }
    const w = Math.ceil((ctx?.measureText(text).width ?? text.length * size * 0.6 * k) + 6 * k);
    canvas.width = Math.max(2, w);
    canvas.height = Math.ceil(size * k * 1.4);
    if (ctx) {
      ctx.font = font;
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3 * k;
      ctx.strokeStyle = f.halo ?? "rgba(10, 14, 22, 0.85)";
      ctx.strokeText(text, 3 * k, canvas.height / 2);
      ctx.fillStyle = f.color;
      ctx.fillText(text, 3 * k, canvas.height / 2);
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
    material.userData = { wpx: canvas.width / k, hpx: canvas.height / k };
    this.labelMaterials.set(key, material);
    return { material, wpx: canvas.width / k, hpx: canvas.height / k };
  }

  /** Camera directions for the shaders of panels, and the position and size of the text labels. */
  private updateDownholes(): void {
    const where = new THREE.Vector3();
    for (const layer of this.drillholes.values()) {
      if (!layer.group.visible || !layer.labelGroup.visible) continue;
      for (const item of layer.labels) {
        where.set(item.position.x, item.position.y, item.position.z * this.vexag);
        const pxPerM = this.pixelsPerUnit(where);
        item.sprite.center.set(-layer.labelShift / item.wpx, 0.5);
        item.sprite.scale.set(item.wpx / pxPerM, item.hpx / pxPerM, 1);
      }
    }
    // The side direction of panels and ticks follows the camera.
    this.camera.updateMatrixWorld();
    const view = this.camera.getWorldDirection(this.side.view.value).normalize();
    const right = this.side.right.value.setFromMatrixColumn(this.camera.matrixWorld, 0).normalize();
    this.side.vexag.value = this.vexag;
    // Written depths and names of crossings: a label that would cover one already placed is hidden
    // (crossings first, then depths), so a wide view does not turn into a heap of text.
    const taken = new Set<number>();
    const CELL_W = 24;
    const CELL_H = 12;
    const place = (x: number, y: number, w: number, h: number): boolean => {
      const x0 = Math.floor(x / CELL_W);
      const x1 = Math.floor((x + w) / CELL_W);
      const y0 = Math.floor((y - h / 2) / CELL_H);
      const y1 = Math.floor((y + h / 2) / CELL_H);
      for (let i = x0; i <= x1; i++) for (let j = y0; j <= y1; j++) if (taken.has(i * 100_003 + j)) return false;
      for (let i = x0; i <= x1; i++) for (let j = y0; j <= y1; j++) taken.add(i * 100_003 + j);
      return true;
    };
    for (const layer of this.drillholes.values()) {
      if (!layer.group.visible || !layer.marks.visible) continue;
      // Ticks are in pixels: metres per pixel at the target.
      if (layer.hasTicks) layer.tickScale.value = 1 / Math.max(this.pixelsPerUnit(this.controls.target), 1e-9);
      for (const item of layer.markLabels) {
        const screen = this.visibleInSection(item.position) ? this.project(item.position) : null;
        const show = !!screen && place(screen.x + item.shift, screen.y, item.wpx, item.hpx);
        item.sprite.visible = show;
        if (!show) continue;
        where.set(item.position.x, item.position.y, item.position.z * this.vexag);
        const pxPerM = this.pixelsPerUnit(where);
        item.sprite.scale.set(item.wpx / pxPerM, item.hpx / pxPerM, 1);
      }
    }
    if (this.downholes.size === 0) return;
    const tw = new THREE.Vector3();
    const sw = new THREE.Vector3();
    const at = new THREE.Vector3();
    const target = this.controls.target;
    for (const layer of this.downholes.values()) {
      // On a large site a column of a few metres is a hairline: widen every column to a minimum on screen.
      layer.scale.value = Math.max(1, PANEL_MIN_PX / Math.max(layer.panelWidth * this.pixelsPerUnit(target), 1e-9));
      if (!layer.group.visible || layer.labels.length === 0) continue;
      const k = layer.scale.value;
      for (const item of layer.labels) {
        const l = item.label;
        at.set(l.position[0], l.position[1], l.position[2] * this.vexag);
        const pxPerM = this.pixelsPerUnit(at);
        const show = l.length * pxPerM >= LABEL_MIN_PX;
        item.sprite.visible = show;
        if (!show) continue;
        tw.set(l.tangent[0], l.tangent[1], l.tangent[2] * this.vexag).normalize();
        sw.crossVectors(tw, view);
        if (sw.length() < 0.05) sw.copy(right);
        else sw.normalize();
        if (sw.dot(right) < 0) sw.negate();
        // The label sits at the middle of its column; its left edge at the column's start, so it grows outwards.
        item.sprite.position.set(
          l.position[0] + sw.x * l.amp * k,
          l.position[1] + sw.y * l.amp * k,
          l.position[2] + (sw.z * l.amp * k) / this.vexag,
        );
        item.sprite.scale.set(item.wpx / pxPerM, item.hpx / pxPerM, 1);
      }
    }
  }

  removeDrillholes(setId: number, update = true): void {
    const layer = this.drillholes.get(setId);
    if (!layer) return;
    this.root.remove(layer.group);
    layer.labelGroup.clear(); // label materials are shared: they stay in the cache
    layer.group.remove(layer.labelGroup);
    layer.markLabelGroup.clear();
    layer.marks.remove(layer.markLabelGroup);
    disposeTree(layer.group, this.materials);
    this.drillholes.delete(setId);
    if (update) this.contentChanged();
  }

  /**
   * A block model: `render` holds the centre (relative to `origin`, project coordinates) and the
   * size of every block, 6 float32 each. Colours come with setBlockStyle.
   */
  addBlockModel(id: number, render: Float32Array, origin: number[], min: number[], max: number[]): void {
    this.removeBlockModel(id, false);
    const o = this.ensureOrigin(min, max);
    const group = new THREE.Group();
    group.position.set(origin[0] - o.x, origin[1] - o.y, origin[2] - o.z);
    const edges: BlockEdgeUniforms = { width: { value: 0 }, color: { value: new THREE.Color(0x1c2230) }, fade: { value: 1 }, back: { value: new THREE.Color(0) } };
    const material = new THREE.MeshLambertMaterial({ color: 0xffffff });
    const sliceMaterial = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
    outlineFaces(material, edges);
    outlineFaces(sliceMaterial, edges);
    this.blocks.set(id, {
      extent: new THREE.Box3(new THREE.Vector3(...min).sub(o), new THREE.Vector3(...max).sub(o)),
      group, render, mesh: null, material, index: new Int32Array(0),
      colors: null, all: null, drawn: null, stale: false, shown: true, opacity: 1,
      slice: false, sliceMesh: null, sliceMaterial, sliceIndex: new Int32Array(0), edges,
    });
    this.root.add(group);
    this.contentChanged();
  }

  /**
   * A swath plot over the scene: the cuboid cut into swaths and, once it is counted, the plot of
   * means over them (it turns toward the viewer about the cuboid's axis). null removes it.
   */
  setSwath(id: number, build: SwathBuild | null): void {
    const old = this.swaths.get(id);
    if (old) {
      this.root.remove(old.group);
      old.group.traverse((child) => {
        if (child instanceof THREE.Sprite) return; // labels share their materials
        const m = child as THREE.Mesh;
        m.geometry?.dispose();
        const material = m.material as THREE.Material | undefined;
        if (material instanceof LineMaterial) this.materials.delete(material);
        material?.dispose();
      });
      this.swaths.delete(id);
    }
    if (build) {
      const o = this.ensureOrigin(build.centre, build.centre);
      const layer = new SwathLayer(build, o, {
        makeLines: (positions, width, color) => this.makeLines(positions, null, width, color),
        labelMaterial: (text, font) => this.labelMaterial(text, font),
        pointTexture: this.pointTexture,
        background: this.renderer.getClearColor(new THREE.Color()),
      });
      this.swaths.set(id, layer);
      this.root.add(layer.group);
    }
    // The first thing in an empty scene: show it; otherwise the view stays where the user left it.
    if (build && !old && !this.fitted) this.contentChanged();
    else this.requestRender();
  }

  /** Centre and size of every block of a model in the scene (6 float32 each), or null. */
  blockRender(id: number): Float32Array | null {
    return this.blocks.get(id)?.render ?? null;
  }

  /**
   * How a model is shown: colours, the blocks that pass its restriction and those drawn as boxes,
   * outlines, and whether a clipped section shows the cut. Returns the number of boxes (negative:
   * more than can be drawn).
   */
  setBlockStyle(id: number, d: BlockDisplay): number {
    const layer = this.blocks.get(id);
    if (!layer) return 0;
    if (layer.colors !== d.colors || layer.drawn !== d.drawn) layer.stale = true;
    layer.colors = d.colors;
    layer.all = d.all;
    layer.drawn = d.drawn;
    layer.shown = d.shown;
    layer.opacity = d.opacity;
    layer.slice = d.slice;
    layer.edges.width.value = d.edges ? Math.max(0.6, d.edges.width / 2) : 0;
    if (d.edges) layer.edges.color.value.set(d.edges.color);
    layer.group.visible = d.shown;
    let count = 0;
    for (let i = 0; i < d.drawn.length; i++) count += d.drawn[i];
    this.refreshBlocks(layer);
    this.requestRender();
    return count > MAX_SHOWN_BLOCKS ? -count : count;
  }

  /** Is the model shown as its cut by the section plane now? */
  private slicing(layer: BlockLayer): boolean {
    return layer.slice && this.clip && this.section !== null && this.origin !== null;
  }

  /** The boxes or the cut of a model, whichever the view needs now; the other is hidden. */
  private refreshBlocks(layer: BlockLayer): void {
    if (layer.sliceMesh) {
      layer.group.remove(layer.sliceMesh);
      layer.sliceMesh.geometry.dispose();
      layer.sliceMesh = null;
      layer.sliceIndex = new Int32Array(0);
    }
    if (!layer.colors || !layer.all || !layer.drawn) return;
    if (!layer.shown) return; // built when the model is shown
    if (this.slicing(layer)) {
      if (layer.mesh) layer.mesh.visible = false;
      // An underlay, as a vertical raster: drawn first and without depth, so holes and wireframe
      // cuts on either side of the plane stay on top; its opacity mixes it toward the background.
      this.renderer.getClearColor(layer.edges.back.value);
      layer.edges.fade.value = layer.opacity;
      layer.sliceMaterial.depthTest = false;
      layer.sliceMaterial.depthWrite = false;
      this.buildSlice(layer);
      return;
    }
    layer.edges.fade.value = 1;
    this.applyOpacity(layer.material, layer.opacity);
    if (layer.stale || !layer.mesh) this.buildBoxes(layer);
    if (layer.mesh) layer.mesh.visible = true;
  }

  private buildBoxes(layer: BlockLayer): void {
    if (layer.mesh) {
      layer.group.remove(layer.mesh);
      layer.mesh.dispose();
      layer.mesh = null;
    }
    layer.stale = false;
    layer.index = new Int32Array(0);
    const visible = layer.drawn as Uint8Array;
    const colors = layer.colors as Uint8Array;
    const n = layer.render.length / 6;
    let count = 0;
    for (let i = 0; i < n; i++) count += visible[i];
    if (count === 0 || count > MAX_SHOWN_BLOCKS) return;
    const mesh = new THREE.InstancedMesh(BLOCK_BOX, layer.material, count);
    const index = new Int32Array(count);
    const m = mesh.instanceMatrix.array as Float32Array;
    const c = new Float32Array(count * 3);
    const r = layer.render;
    let k = 0;
    for (let i = 0; i < n; i++) {
      if (!visible[i]) continue;
      const b = i * 6;
      const e = k * 16;
      // Scale by the size, then move to the centre (column-major).
      m[e] = r[b + 3]; m[e + 5] = r[b + 4]; m[e + 10] = r[b + 5]; m[e + 15] = 1;
      m[e + 12] = r[b]; m[e + 13] = r[b + 1]; m[e + 14] = r[b + 2];
      c[k * 3] = colors[i * 3] / 255;
      c[k * 3 + 1] = colors[i * 3 + 1] / 255;
      c[k * 3 + 2] = colors[i * 3 + 2] / 255;
      index[k++] = i;
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.instanceColor = new THREE.InstancedBufferAttribute(c, 3);
    mesh.computeBoundingSphere();
    mesh.computeBoundingBox();
    layer.mesh = mesh;
    layer.index = index;
    layer.group.add(mesh);
  }

  /**
   * The cut of a model by the vertical plane of the section: every block that passes the
   * restriction and is crossed by the plane gives a rectangle on it (the part of the section line
   * inside the block, by the block's height), in the block's colour.
   */
  private buildSlice(layer: BlockLayer): void {
    const s = this.section;
    const o = this.origin;
    if (!s || !o) return;
    const all = layer.all as Uint8Array;
    const colors = layer.colors as Uint8Array;
    const r = layer.render;
    const n = all.length;
    const along = sectionDirection(s.az);
    const normal = sectionNormal(s.az);
    const ax = along.x, ay = along.y, nx = normal.x, ny = normal.y;
    const anx = Math.abs(nx), any = Math.abs(ny);
    // The start of the section line in the coordinates of the model's group.
    const px = s.x0 - o.x - layer.group.position.x;
    const py = s.y0 - o.y - layer.group.position.y;
    // A line without a length runs both ways from its start.
    const u0min = s.length === null ? -Infinity : 0;
    const u1max = s.length ?? Infinity;
    const crossed: number[] = [];
    for (let i = 0, b = 0; i < n; i++, b += 6) {
      if (!all[i]) continue;
      const d = (r[b] - px) * nx + (r[b + 1] - py) * ny;
      if (Math.abs(d) <= (anx * r[b + 3] + any * r[b + 4]) / 2) crossed.push(i);
    }
    const position = new Float32Array(crossed.length * 12);
    const color = new Uint8Array(crossed.length * 12);
    const uv = new Float32Array(crossed.length * 8);
    const index = new Uint32Array(crossed.length * 6);
    const blocks = new Int32Array(crossed.length);
    let k = 0;
    for (const i of crossed) {
      const b = i * 6;
      const hx = r[b + 3] / 2, hy = r[b + 4] / 2, hz = r[b + 5] / 2;
      // The part of the line (by the distance along it) inside the block's footprint.
      let u0 = u0min, u1 = u1max;
      if (Math.abs(ax) > 1e-9) {
        const a = (r[b] - hx - px) / ax, c = (r[b] + hx - px) / ax;
        u0 = Math.max(u0, Math.min(a, c));
        u1 = Math.min(u1, Math.max(a, c));
      }
      if (Math.abs(ay) > 1e-9) {
        const a = (r[b + 1] - hy - py) / ay, c = (r[b + 1] + hy - py) / ay;
        u0 = Math.max(u0, Math.min(a, c));
        u1 = Math.min(u1, Math.max(a, c));
      }
      if (!(u1 - u0 > 1e-6)) continue;
      const x0 = px + ax * u0, y0 = py + ay * u0, x1 = px + ax * u1, y1 = py + ay * u1;
      const z0 = r[b + 2] - hz, z1 = r[b + 2] + hz;
      position.set([x0, y0, z0, x1, y1, z0, x1, y1, z1, x0, y0, z1], k * 12);
      uv.set([0, 0, 1, 0, 1, 1, 0, 1], k * 8);
      for (let v = 0; v < 4; v++) color.set([colors[i * 3], colors[i * 3 + 1], colors[i * 3 + 2]], k * 12 + v * 3);
      index.set([k * 4, k * 4 + 1, k * 4 + 2, k * 4, k * 4 + 2, k * 4 + 3], k * 6);
      blocks[k++] = i;
    }
    if (k === 0) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(position.subarray(0, k * 12), 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(color.subarray(0, k * 12), 3, true));
    geometry.setAttribute("uv", new THREE.BufferAttribute(uv.subarray(0, k * 8), 2));
    geometry.setIndex(new THREE.BufferAttribute(index.subarray(0, k * 6), 1));
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, layer.sliceMaterial);
    mesh.renderOrder = -5;
    layer.sliceMesh = mesh;
    layer.sliceIndex = blocks.subarray(0, k);
    layer.group.add(mesh);
  }

  removeBlockModel(id: number, update = true): void {
    const layer = this.blocks.get(id);
    if (!layer) return;
    layer.mesh?.dispose();
    layer.sliceMesh?.geometry.dispose();
    layer.material.dispose();
    layer.sliceMaterial.dispose();
    this.root.remove(layer.group);
    this.blocks.delete(id);
    if (update) this.contentChanged();
  }

  /** The block under the cursor: nearest hit over every shown model. */
  private pickBlock(raycaster: THREE.Raycaster): { modelId: number; index: number; distance: number } | null {
    if (!this.blocks.size) return null;
    const planes = this.renderer.clippingPlanes;
    let best: { modelId: number; index: number; distance: number } | null = null;
    for (const [modelId, layer] of this.blocks) {
      if (!layer.group.visible) continue;
      if (layer.sliceMesh) {
        // The cut: a quad (two triangles) per block.
        for (const hit of raycaster.intersectObject(layer.sliceMesh, false)) {
          if (planes.length && !planes.every((p) => p.distanceToPoint(hit.point) >= -1e-6)) continue;
          if (hit.faceIndex !== undefined && hit.faceIndex !== null && (!best || hit.distance < best.distance)) best = { modelId, index: layer.sliceIndex[hit.faceIndex >> 1], distance: hit.distance };
          break;
        }
        continue;
      }
      if (!layer.mesh || !layer.mesh.visible) continue;
      for (const hit of raycaster.intersectObject(layer.mesh, false)) {
        // Hidden by the section window: not pickable.
        if (planes.length && !planes.every((p) => p.distanceToPoint(hit.point) >= -1e-6)) continue;
        if (hit.instanceId !== undefined && (!best || hit.distance < best.distance)) best = { modelId, index: layer.index[hit.instanceId], distance: hit.distance };
        break;
      }
    }
    return best;
  }

  /** The wireframe under the cursor: nearest hit on the shown level of every shown wireframe. */
  private pickWireframe(raycaster: THREE.Raycaster): { id: number; distance: number } | null {
    const planes = this.renderer.clippingPlanes;
    let best: { id: number; distance: number } | null = null;
    for (const [id, layer] of this.wireframes) {
      if (!layer.group.visible || layer.material.opacity <= 0.02) continue;
      for (const level of layer.levels.values()) {
        if (!level.object.visible) continue;
        for (const hit of raycaster.intersectObject(level.object, true)) {
          if (planes.length && !planes.every((p) => p.distanceToPoint(hit.point) >= -1e-6)) continue;
          if (!best || hit.distance < best.distance) best = { id, distance: hit.distance };
          break;
        }
      }
    }
    return best;
  }

  /** What is under the cursor among blocks and wireframes, the nearest of them. */
  private pickObject(clientX: number, clientY: number):
    { kind: "block"; modelId: number; index: number } | { kind: "wireframe"; id: number } | null {
    if (!this.blocks.size && !this.wireframes.size) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, this.camera);
    const block = this.onPickBlock ? this.pickBlock(raycaster) : null;
    const wire = this.onPickWireframe ? this.pickWireframe(raycaster) : null;
    if (block && (!wire || block.distance <= wire.distance)) return { kind: "block", modelId: block.modelId, index: block.index };
    return wire ? { kind: "wireframe", id: wire.id } : null;
  }

  /** The wireframe chosen in the view or the tree: drawn a little lighter. */
  setSelectedWireframe(id: number | null): void {
    if (id === this.selectedWireframe) return;
    const old = this.selectedWireframe === null ? undefined : this.wireframes.get(this.selectedWireframe);
    old?.material.emissive.setHex(0x000000);
    this.selectedWireframe = id;
    this.wireframes.get(id ?? -1)?.material.emissive.setHex(0x3a3a3a);
    this.requestRender();
  }

  setDrillholeStyle(setId: number, style: DrillholeStyle): void {
    const layer = this.drillholes.get(setId);
    if (!layer) return;
    layer.group.visible = style.visible;
    if (layer.paths) layer.paths.visible = style.paths;
    layer.marks.visible = style.paths;
    if (layer.intervals) layer.intervals.visible = style.intervals;
    if (layer.points) layer.points.visible = style.collars;
    layer.labelGroup.visible = style.collars;
    this.requestRender();
  }

  /**
   * A wireframe placed at its `origin` (project coordinates); its levels of detail come later
   * (setWireframeLevel). The group's offset from the scene origin is combined with the camera in
   * double precision, so float32 vertices keep sub-millimetre accuracy wherever the object is.
   */
  addWireframe(id: number, origin: number[], min: number[], max: number[], color: string, opacity: number): void {
    this.removeWireframe(id, false);
    const o = this.ensureOrigin(min, max);
    const group = new THREE.Group();
    group.position.set(origin[0] - o.x, origin[1] - o.y, origin[2] - o.z);
    // Flat shading takes normals from screen-space derivatives: no normal buffer is needed.
    const material = new THREE.MeshLambertMaterial({ color, side: THREE.DoubleSide, flatShading: true });
    this.applyOpacity(material, opacity);
    const drape: DrapeUniforms = {
      uDrapeMap: { value: null }, uDrapeU: { value: new THREE.Vector3() }, uDrapeV: { value: new THREE.Vector3() },
      uDrapeTint: { value: new THREE.Color(0xffffff) }, uDrapeMix: { value: 0 }, uDrapeShade: { value: 1 },
    };
    this.drapeMaterial(material, drape);
    const local = new THREE.Box3(new THREE.Vector3(...min.map((v, k) => v - origin[k])), new THREE.Vector3(...max.map((v, k) => v - origin[k])));
    this.wireframes.set(id, {
      extent: new THREE.Box3(new THREE.Vector3(...min).sub(o), new THREE.Vector3(...max).sub(o)),
      group, material, levels: new Map(), sphere: local.getBoundingSphere(new THREE.Sphere()), cutLines: null, cut: null,
      origin: [...origin], drape, drapeTexture: null, drapeUrl: null,
    });
    if (this.selectedWireframe === id) material.emissive.setHex(0x3a3a3a);
    this.root.add(group);
    this.applyDrape(id);
    this.contentChanged();
  }

  /**
   * One level of detail: float32 `vertices` relative to the origin and uint32 `triangles`, used
   * without copying. `chunks` ([first triangle, count, min xyz, max xyz]) split the full level
   * into parts sharing one vertex buffer, so parts outside the view are skipped.
   */
  setWireframeLevel(id: number, level: number, vertices: Float32Array, triangles: Uint32Array, chunks: number[][] | null = null): void {
    const layer = this.wireframes.get(id);
    if (!layer || triangles.length === 0) return;
    this.disposeLevel(layer, level);
    const position = new THREE.BufferAttribute(vertices, 3);
    const parts = chunks && chunks.length > 1 ? chunks : [[0, triangles.length / 3, ...layer.sphere.center.toArray(), 0, 0, 0]];
    const geometries: THREE.BufferGeometry[] = [];
    const object = parts.length > 1 ? new THREE.Group() : null;
    let single: THREE.Mesh | null = null;
    for (const [first, count, ...box] of parts) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", position);
      geometry.setIndex(new THREE.BufferAttribute(triangles.subarray(first * 3, (first + count) * 3), 1));
      if (parts.length > 1) {
        geometry.boundingBox = new THREE.Box3(new THREE.Vector3(box[0], box[1], box[2]), new THREE.Vector3(box[3], box[4], box[5]));
        geometry.boundingSphere = geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
      } else {
        geometry.boundingSphere = layer.sphere.clone();
      }
      const mesh = new THREE.Mesh(geometry, layer.material);
      geometries.push(geometry);
      if (object) object.add(mesh);
      else single = mesh;
    }
    const entry: WireframeLevel = { object: object ?? single!, triangles: triangles.length / 3, geometries };
    layer.levels.set(level, entry);
    layer.group.add(entry.object);
    this.chooseLevels();
    this.requestRender();
  }

  /** Whether a level of a wireframe is already in the scene (the viewer loads coarse to fine). */
  hasWireframeLevel(id: number, level: number): boolean {
    return this.wireframes.get(id)?.levels.has(level) ?? false;
  }

  private disposeLevel(layer: WireframeLayer, level: number): void {
    const old = layer.levels.get(level);
    if (!old) return;
    layer.group.remove(old.object);
    old.geometries.forEach((g) => g.dispose());
    layer.levels.delete(level);
  }

  /** Pixels per scene unit at a point, for the current camera. */
  private pixelsPerUnit(at: THREE.Vector3): number {
    const h = this.container.clientHeight || 1;
    if (this.camera === this.ortho) return (h * this.ortho.zoom) / (this.ortho.top - this.ortho.bottom);
    const dist = Math.max(this.perspective.position.distanceTo(at), 1e-6);
    return h / (2 * dist * Math.tan(THREE.MathUtils.degToRad(this.perspective.fov) / 2));
  }

  /** Every wireframe shows the finest loaded level that is not denser than the screen can show. */
  private chooseLevels(): void {
    const centre = new THREE.Vector3();
    for (const layer of this.wireframes.values()) {
      if (layer.levels.size === 0) continue;
      centre.copy(layer.sphere.center).add(layer.group.position);
      centre.z *= this.vexag;
      const r = layer.sphere.radius * Math.max(1, this.vexag) * this.pixelsPerUnit(centre);
      const inside = this.camera === this.perspective && this.perspective.position.distanceTo(centre) < layer.sphere.radius * Math.max(1, this.vexag);
      const area = Math.max(Math.PI * r * r, 1);
      const byDetail = [...layer.levels.entries()].sort((a, b) => b[1].triangles - a[1].triangles);
      let pick = byDetail.find(([, l]) => inside || l.triangles / area <= TRIANGLES_PER_PIXEL) ?? byDetail[byDetail.length - 1];
      // With clipping on, the section must come from the exact geometry when it is loaded.
      if (this.clip && layer.levels.has(0)) pick = [0, layer.levels.get(0)!];
      for (const [level, entry] of layer.levels) entry.object.visible = level === pick[0];
    }
  }

  removeWireframe(id: number, update = true): void {
    const layer = this.wireframes.get(id);
    if (!layer) return;
    for (const level of [...layer.levels.keys()]) this.disposeLevel(layer, level);
    this.removeCut(layer);
    this.root.remove(layer.group);
    layer.material.dispose();
    layer.drapeTexture?.dispose();
    this.wireframes.delete(id);
    this.updateDrapedRasters();
    if (update) this.contentChanged();
  }

  setWireframeStyle(id: number, color: string, visible: boolean, opacity: number): void {
    const layer = this.wireframes.get(id);
    if (!layer) return;
    const recolor = !layer.material.color.equals(new THREE.Color(color));
    layer.material.color.set(color);
    this.applyOpacity(layer.material, opacity);
    const shown = layer.group.visible !== visible;
    layer.group.visible = visible;
    if (recolor) this.updateCut(layer);
    if (shown) this.updateDrapedRasters();
    this.requestRender();
  }

  /**
   * The surface's material also takes a raster: the image's coordinates come from the vertex
   * position in plan, so no texture coordinates are stored and every level of detail is covered.
   */
  private drapeMaterial(material: THREE.MeshLambertMaterial, uniforms: DrapeUniforms): void {
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nuniform vec3 uDrapeU;\nuniform vec3 uDrapeV;\nvarying vec2 vDrapeUv;")
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvDrapeUv = vec2(dot(uDrapeU, vec3(position.xy, 1.0)), dot(uDrapeV, vec3(position.xy, 1.0)));");
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nuniform sampler2D uDrapeMap;\nuniform vec3 uDrapeTint;\nuniform float uDrapeMix;\nuniform float uDrapeShade;\nvarying vec2 vDrapeUv;")
        .replace(
          "#include <map_fragment>",
          `#include <map_fragment>
          vec4 drapeTexel = texture2D(uDrapeMap, vDrapeUv);
          vec3 drapeRgb = drapeTexel.rgb * uDrapeTint;
          float drapeK = uDrapeMix * drapeTexel.a * step(0.0, vDrapeUv.x) * step(vDrapeUv.x, 1.0) * step(0.0, vDrapeUv.y) * step(vDrapeUv.y, 1.0);
          diffuseColor.rgb = mix(diffuseColor.rgb, drapeRgb, drapeK);`,
        )
        .replace("#include <opaque_fragment>", "outgoingLight = mix(outgoingLight, drapeRgb, drapeK * (1.0 - uDrapeShade));\n#include <opaque_fragment>");
    };
  }

  /** Lays a raster on a wireframe (a terrain model) or takes it off (null). */
  setDrape(wireframeId: number, drape: Drape | null): void {
    const old = this.drapes.get(wireframeId);
    if (JSON.stringify(old ?? null) === JSON.stringify(drape)) return;
    if (drape) this.drapes.set(wireframeId, drape);
    else this.drapes.delete(wireframeId);
    this.applyDrape(wireframeId);
  }

  /** The draped raster of a wireframe, as it is set now. */
  drapeOf(wireframeId: number): Drape | null {
    return this.drapes.get(wireframeId) ?? null;
  }

  private applyDrape(id: number): void {
    const layer = this.wireframes.get(id);
    if (layer) {
      const drape = this.drapes.get(id);
      const axes = drape ? imageAxes(drape.corners, layer.origin) : null;
      const u = layer.drape;
      if (!drape || !axes) {
        u.uDrapeMix.value = 0;
        u.uDrapeMap.value = null;
        layer.drapeTexture?.dispose();
        layer.drapeTexture = null;
        layer.drapeUrl = null;
      } else {
        u.uDrapeU.value.set(...axes.u);
        u.uDrapeV.value.set(...axes.v);
        u.uDrapeTint.value.set(drape.tint);
        u.uDrapeShade.value = drape.shade ? 1 : 0;
        // The image appears when it is loaded; until then the surface keeps its own colour.
        u.uDrapeMix.value = layer.drapeTexture && layer.drapeUrl === drape.url ? drape.opacity : 0;
        if (layer.drapeUrl !== drape.url) {
          const url = drape.url;
          layer.drapeUrl = url;
          new THREE.TextureLoader().load(url, (texture) => {
            if (this.wireframes.get(id) !== layer || layer.drapeUrl !== url) {
              texture.dispose();
              return;
            }
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
            layer.drapeTexture?.dispose();
            layer.drapeTexture = texture;
            u.uDrapeMap.value = texture;
            u.uDrapeMix.value = this.drapes.get(id)?.opacity ?? 0;
            this.requestRender();
          });
        }
      }
    }
    this.updateDrapedRasters();
    this.requestRender();
  }

  /**
   * A raster lying on a shown terrain model is not drawn flat as well: hidden whole when the
   * model keeps only its own part of the image, otherwise cut out where the model covers it.
   */
  private updateDrapedRasters(): void {
    const o = this.origin;
    for (const [id, layer] of this.rasters) {
      const holes: THREE.Vector4[] = [];
      let hidden = false;
      if (!layer.corners3d && o) {
        for (const [wireframeId, drape] of this.drapes) {
          const surface = this.wireframes.get(wireframeId);
          if (drape.rasterId !== id || !surface?.group.visible) continue;
          if (drape.clip) hidden = true;
          const min = surface.extent.min;
          const max = surface.extent.max;
          const window = imageWindow(layer.corners, [min.x + o.x, min.y + o.y], [max.x + o.x, max.y + o.y]);
          if (window && holes.length < MAX_RASTER_HOLES) holes.push(new THREE.Vector4(...window));
        }
      }
      layer.holes.uHoleCount.value = holes.length;
      holes.forEach((h, i) => layer.holes.uHoles.value[i].copy(h));
      layer.mesh.visible = layer.visible && !hidden;
    }
  }

  /** `vertices` float32 relative to `origin`; `offsets` are line starts plus the end. Placed like wireframes.
   *  A line of one vertex is a point: drawn as a marker. */
  setLines(id: number, origin: number[], vertices: Float32Array, offsets: Uint32Array, color: string, local: Bounds | null = null): void {
    this.removeLines(id, false);
    if (vertices.length < 3) return;
    const b = local ?? bounds(vertices);
    if (!b) return;
    const min = b.min.map((v, k) => v + origin[k]);
    const max = b.max.map((v, k) => v + origin[k]);
    const o = this.ensureOrigin(min, max);
    let segments = 0;
    let single = 0;
    for (let l = 0; l + 1 < offsets.length; l++) {
      const n = offsets[l + 1] - offsets[l];
      segments += Math.max(0, n - 1);
      if (n === 1) single++;
    }
    if (segments === 0 && single === 0) return;
    const group = new THREE.Group();
    group.position.set(origin[0] - o.x, origin[1] - o.y, origin[2] - o.z);
    const hex = new THREE.Color(color).getHex();
    let lines: LineSegments2 | null = null;
    if (segments) {
      const positions = new Float32Array(segments * 6);
      let j = 0;
      for (let l = 0; l + 1 < offsets.length; l++) {
        for (let i = offsets[l]; i + 1 < offsets[l + 1]; i++) {
          positions.set(vertices.subarray(i * 3, i * 3 + 6), j);
          j += 6;
        }
      }
      lines = this.makeLines(positions, null, 2, hex);
      lines.renderOrder = 5;
      group.add(lines);
    }
    let points: THREE.Points | null = null;
    if (single) {
      const positions = new Float32Array(single * 3);
      let j = 0;
      for (let l = 0; l + 1 < offsets.length; l++) {
        if (offsets[l + 1] - offsets[l] === 1) {
          positions.set(vertices.subarray(offsets[l] * 3, offsets[l] * 3 + 3), j);
          j += 3;
        }
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      points = new THREE.Points(geometry, new THREE.PointsMaterial({ color: hex, size: 9, sizeAttenuation: false, map: this.pointTexture, alphaTest: 0.5, transparent: true }));
      points.frustumCulled = false;
      points.renderOrder = 6;
      group.add(points);
    }
    this.lineLayers.set(id, { extent: new THREE.Box3(new THREE.Vector3(...min).sub(o), new THREE.Vector3(...max).sub(o)), group, lines, points });
    this.root.add(group);
    this.contentChanged();
  }

  removeLines(id: number, update = true): void {
    const layer = this.lineLayers.get(id);
    if (!layer) return;
    this.root.remove(layer.group);
    disposeTree(layer.group, this.materials);
    this.lineLayers.delete(id);
    if (update) this.contentChanged();
  }

  setLinesStyle(id: number, color: string, visible: boolean): void {
    const layer = this.lineLayers.get(id);
    if (!layer) return;
    if (layer.lines) (layer.lines.material as LineMaterial).color.set(color);
    if (layer.points) (layer.points.material as THREE.PointsMaterial).color.set(color);
    layer.group.visible = visible;
    this.requestRender();
  }

  // ---------------------------------------------------------------- the string editor

  /** The ray under the cursor in project coordinates (float64): from the eye, through the pixel. */
  rayAt(clientX: number, clientY: number): { origin: [number, number, number]; dir: [number, number, number] } | null {
    const o = this.origin;
    if (!o) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, this.camera);
    const p = raycaster.ray.origin;
    const d = raycaster.ray.direction;
    // The scene is the project shifted by the origin and stretched along Z: a straight line stays straight.
    const dir: [number, number, number] = [d.x, d.y, d.z / this.vexag];
    const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
    return { origin: [p.x + o.x, p.y + o.y, p.z / this.vexag + o.z], dir: [dir[0] / len, dir[1] / len, dir[2] / len] };
  }

  /** Project coordinates → clip space (column-major 4×4), the canvas size, and the section window test. */
  viewTransform(): { matrix: number[]; width: number; height: number; visible?: (x: number, y: number, z: number) => boolean } | null {
    const o = this.origin;
    if (!o) return null;
    this.camera.updateMatrixWorld();
    const m = new THREE.Matrix4().multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse)
      .multiply(new THREE.Matrix4().makeScale(1, 1, this.vexag))
      .multiply(new THREE.Matrix4().makeTranslation(-o.x, -o.y, -o.z));
    const s = this.section;
    let visible: ((x: number, y: number, z: number) => boolean) | undefined;
    if (this.clip && s) {
      const n = sectionNormal(s.az);
      const along = sectionDirection(s.az);
      visible = (x, y) => {
        const dx = x - s.x0;
        const dy = y - s.y0;
        const d = n.x * dx + n.y * dy;
        if (d > s.towards + 1e-6 || d < -s.away - 1e-6) return false;
        if (s.length === null) return true;
        const u = along.x * dx + along.y * dy;
        return u >= -1e-6 && u <= s.length + 1e-6;
      };
    }
    return { matrix: Array.from(m.elements), width: this.container.clientWidth || 1, height: this.container.clientHeight || 1, visible };
  }

  /** The point the view is centred on, project coordinates. */
  focus(): [number, number, number] | null {
    const o = this.origin;
    const t = this.controls.target;
    return o ? [t.x + o.x, t.y + o.y, t.z / this.vexag + o.z] : null;
  }

  /** Starts the scene around a point when nothing is loaded yet (the first line of an empty project). */
  startAt(x: number, y: number, z: number): void {
    if (this.origin) return;
    this.ensureOrigin([x - 50, y - 50, z - 50], [x + 50, y + 50, z + 50]);
    this.contentChanged();
  }

  private marks = new Map<string, THREE.Object3D>();
  /** Marks triangles over the scene (self-intersections, triangles picked for a fix): corners in
   *  project coordinates, 9 numbers per triangle; null removes the marks of this key. */
  setMarks(key: string, corners: ArrayLike<number> | null, color = "#ff3b30"): void {
    const old = this.marks.get(key);
    if (old) {
      this.root.remove(old);
      disposeTree(old, this.materials);
      this.marks.delete(key);
    }
    const o = this.origin;
    if (corners && corners.length >= 9 && o) {
      const positions = new Float32Array(corners.length);
      for (let i = 0; i < corners.length; i += 3) {
        positions[i] = corners[i] - o.x;
        positions[i + 1] = corners[i + 1] - o.y;
        positions[i + 2] = corners[i + 2] - o.z;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      const group = new THREE.Group();
      const fill = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: 0.55, depthTest: false }));
      fill.renderOrder = 15;
      const edges = new Float32Array((corners.length / 9) * 18);
      for (let t = 0, j = 0; t < corners.length / 9; t++) {
        for (let k = 0; k < 3; k++, j += 6) {
          edges.set(positions.subarray(t * 9 + k * 3, t * 9 + k * 3 + 3), j);
          edges.set(positions.subarray(t * 9 + ((k + 1) % 3) * 3, t * 9 + ((k + 1) % 3) * 3 + 3), j + 3);
        }
      }
      const outline = this.makeLines(edges, null, 2, new THREE.Color(color).getHex());
      (outline.material as LineMaterial).depthTest = false;
      outline.renderOrder = 16;
      group.add(fill, outline);
      this.marks.set(key, group);
      this.root.add(group);
    }
    this.requestRender();
  }

  /** Draws the working copy of the edited layer over the scene (null removes it). */
  setEditOverlay(o: EditOverlay | null): void {
    if (this.overlay) {
      this.root.remove(this.overlay);
      disposeTree(this.overlay, this.materials);
      this.overlay = null;
    }
    if (!o) {
      this.requestRender();
      return;
    }
    const first = o.vertices.length ? o.vertices : o.marker ? Float64Array.from(o.marker) : null;
    if (!this.origin && first) this.startAt(first[0], first[1], first[2]);
    const origin = this.origin;
    if (!origin) return;
    const local = (a: ArrayLike<number>): Float32Array => {
      const out = new Float32Array(a.length);
      for (let i = 0; i < a.length; i += 3) {
        out[i] = a[i] - origin.x;
        out[i + 1] = a[i + 1] - origin.y;
        out[i + 2] = a[i + 2] - origin.z;
      }
      return out;
    };
    const group = new THREE.Group();
    const hex = new THREE.Color(o.color).getHex();
    const addLines = (a: Float64Array, width: number, color: number, order: number) => {
      if (a.length < 6) return;
      const lines = this.makeLines(local(a), null, width, color);
      (lines.material as LineMaterial).depthTest = false;
      lines.renderOrder = order;
      group.add(lines);
    };
    const addPoints = (a: ArrayLike<number>, size: number, color: number, order: number) => {
      if (a.length < 3) return;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(local(a), 3));
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({ color, size, sizeAttenuation: false, map: this.pointTexture, alphaTest: 0.4, transparent: true, depthTest: false }));
      points.frustumCulled = false;
      points.renderOrder = order;
      group.add(points);
    };
    addLines(o.segments, 2.5, hex, 20);
    addLines(o.selected, 5, SELECT_COLOR, 21);
    addLines(o.draft, 1.5, DRAFT_COLOR, 22);
    addPoints(o.vertices, 8, hex, 23);
    addPoints(o.selectedVertices, 12, SELECT_COLOR, 24);
    if (o.marker) addPoints(o.marker, 14, DRAFT_COLOR, 25);
    this.overlay = group;
    this.root.add(group);
    this.requestRender();
  }

  /** A georeferenced image lying in plan; `corners` in project coordinates (TL, TR, BR, BL). */
  setRaster(id: number, url: string, corners: number[][], elevation: number | null, opacity: number, visible: boolean, corners3d: number[][] | null = null): void {
    this.removeRaster(id, false);
    const xs = corners.map((c) => c[0]);
    const ys = corners.map((c) => c[1]);
    const zs = corners3d ? corners3d.map((c) => c[2]) : [elevation ?? 0];
    this.ensureOrigin([Math.min(...xs), Math.min(...ys), Math.min(...zs)], [Math.max(...xs), Math.max(...ys), Math.max(...zs)]);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(12), 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 0], 2));
    geometry.setIndex([0, 3, 1, 1, 3, 2]);
    const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, color: 0xffffff });
    material.polygonOffset = true;
    material.polygonOffsetFactor = 1;
    material.polygonOffsetUnits = 1;
    const holes = { uHoles: { value: Array.from({ length: MAX_RASTER_HOLES }, () => new THREE.Vector4()) }, uHoleCount: { value: 0 } };
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, holes);
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nvarying vec2 vHoleUv;")
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvHoleUv = uv;");
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", `#include <common>\nuniform vec4 uHoles[${MAX_RASTER_HOLES}];\nuniform int uHoleCount;\nvarying vec2 vHoleUv;`)
        .replace(
          "#include <map_fragment>",
          `for (int i = 0; i < ${MAX_RASTER_HOLES}; i++) {
            if (i < uHoleCount && vHoleUv.x > uHoles[i].x && vHoleUv.y > uHoles[i].y && vHoleUv.x < uHoles[i].z && vHoleUv.y < uHoles[i].w) discard;
          }
          #include <map_fragment>`,
        );
    };
    const mesh = new THREE.Mesh(geometry, material);
    mesh.renderOrder = -1;
    const layer: RasterLayer = { corners, elevation, corners3d, mesh, texture: null, visible, opacity, holes };
    this.rasters.set(id, layer);
    this.layerRaster(layer);
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
    this.updateDrapedRasters();
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

  setRasterStyle(id: number, visible: boolean, opacity: number, elevation: number | null, corners: number[][], corners3d: number[][] | null): void {
    const layer = this.rasters.get(id);
    if (!layer) return;
    layer.visible = visible;
    layer.opacity = opacity;
    this.layerRaster(layer);
    const moved = JSON.stringify([corners, corners3d]) !== JSON.stringify([layer.corners, layer.corners3d]);
    if (layer.elevation !== elevation || moved) {
      layer.elevation = elevation;
      layer.corners = corners;
      layer.corners3d = corners3d;
      this.placeRaster(layer);
      this.rebuildGrid();
    }
    this.updateDrapedRasters();
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
    if (this.grid) this.grid.visible = visible;
    this.requestRender();
  }

  setSelected(hole: HoleRef | null): void {
    this.selected = hole;
    this.applySelected();
    this.requestRender();
  }

  /** The section line; with clipping on, only its window (towards / away) stays visible. */
  setSection(section: SectionLine | null): void {
    this.section = section;
    this.updateSectionLine();
    this.applyClipping();
    this.requestRender();
    this.onCamera();
  }

  /** Turns clipping to the section window on or off; the camera is not moved. */
  setClip(on: boolean): void {
    if (on === this.clip) return;
    this.clip = on;
    for (const layer of this.rasters.values()) this.layerRaster(layer);
    this.updateSectionLine();
    this.applyClipping();
    this.requestRender();
    this.onCamera();
  }

  /** Orthographic (plans and sections are true to scale) or perspective; the view direction is kept. */
  setPerspective(on: boolean): void {
    const next = on ? this.perspective : this.ortho;
    if (next === this.camera) return;
    const direction = this.viewDirection();
    this.camera = next;
    this.controls.dispose();
    this.controls = this.makeControls();
    this.lookFrom(direction);
  }

  /**
   * Horizontal direction of a hole, collar to bottom, in degrees clockwise from north;
   * null for a (nearly) vertical hole, which has no direction in plan.
   */
  holeAzimuth(hole: HoleRef): number | null {
    const path = this.drillholes.get(hole.setId)?.pathOf.get(hole.holeId);
    if (!path || path.length < 2) return null;
    const a = path[0];
    const b = path[path.length - 1];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    if (Math.hypot(dx, dy) < 1) return null;
    return ((THREE.MathUtils.radToDeg(Math.atan2(dx, dy)) % 360) + 360) % 360;
  }

  /**
   * The frame of the current section in project coordinates: along the line over the content
   * (or the drawn length) and from the top to the bottom of the content. Used to stand an image on it.
   */
  sectionFrame(): VerticalPlacement | null {
    const s = this.section;
    const o = this.origin;
    const box = this.contentBox(false);
    if (!s || !o || !box) return null;
    const along = sectionDirection(s.az);
    let from = 0;
    let to = s.length ?? 0;
    if (s.length === null) {
      from = Infinity;
      to = -Infinity;
      for (const x of [box.min.x, box.max.x]) {
        for (const y of [box.min.y, box.max.y]) {
          const u = along.x * (x + o.x - s.x0) + along.y * (y + o.y - s.y0);
          from = Math.min(from, u);
          to = Math.max(to, u);
        }
      }
    }
    return {
      x1: s.x0 + along.x * from, y1: s.y0 + along.y * from,
      x2: s.x0 + along.x * to, y2: s.y0 + along.y * to,
      z_top: box.max.z + o.z, z_bottom: box.min.z + o.z,
    };
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

  /** Fits everything visible into the window without turning the view. */
  fit(): void {
    this.lookFrom(this.viewDirection());
  }

  /** Turns the camera to a standard direction and fits the content. Rotation stays free afterwards. */
  view(preset: ViewPreset): void {
    let direction: THREE.Vector3;
    switch (preset) {
      case "top": // Y up the screen, X to the right
        direction = new THREE.Vector3(0, -0.0001, 1);
        break;
      case "front": // looking north: Z up, X to the right
        direction = new THREE.Vector3(0, -1, 0);
        break;
      case "side": // looking west: Z up, Y to the right
        direction = new THREE.Vector3(1, 0, 0);
        break;
      case "section": // from the viewer's side of the line: Z up, the line runs to the right
        if (!this.section) return;
        direction = sectionNormal(this.section.az);
        break;
      default:
        direction = new THREE.Vector3(0.9, -1.2, 0.8);
    }
    this.lookFrom(direction, preset === "section");
  }

  /**
   * "В план": turns the view to look straight down (Y up, X to the right) about the point the view
   * is centred on, keeping the scale: the plan shows the part of the project that was on the
   * screen, not everything (that is "Вписать"). Pressing it again on a plan changes nothing.
   */
  toPlan(): void {
    const target = this.controls.target.clone();
    const distance = Math.max(this.camera.position.distanceTo(target), 1);
    const down = new THREE.Vector3(0, -0.0001, 1).normalize();
    this.camera.up.set(0, 0, 1);
    this.camera.position.copy(target).addScaledVector(down, distance);
    if (this.camera === this.ortho) {
      // Room below for everything under the view, however deep.
      const box = this.contentBox();
      const depth = box ? box.getSize(new THREE.Vector3()).length() * Math.max(1, this.vexag) : 0;
      this.ortho.near = 0.1;
      this.ortho.far = Math.max(this.ortho.far, distance * 2 + depth * 2);
      this.ortho.updateProjectionMatrix();
    }
    this.camera.lookAt(target);
    this.controls.update();
    this.requestRender();
  }

  /** Unit vector from the view target to the camera. */
  private viewDirection(): THREE.Vector3 {
    const direction = this.camera.position.clone().sub(this.controls.target);
    return direction.lengthSq() > 0 ? direction.normalize() : new THREE.Vector3(0.9, -1.2, 0.8).normalize();
  }

  /** Places the camera on `direction` from the content and sizes the view to hold it. */
  private lookFrom(direction: THREE.Vector3, onSection = false): void {
    const box = (this.contentBox(true, true) ?? this.contentBox() ?? new THREE.Box3(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50))).clone();
    box.min.z *= this.vexag;
    box.max.z *= this.vexag;
    const s = this.section;
    if (onSection && s && s.length !== null && this.origin) {
      // A drawn line: the view holds the line from end to end (and its window), not the whole project.
      const along = sectionDirection(s.az);
      const n = sectionNormal(s.az);
      const p0 = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
      const ends = [0, s.length].flatMap((u) => [-s.away, s.towards].map((d) => p0.clone().addScaledVector(along, u).addScaledVector(n, d)));
      box.min.x = Math.min(...ends.map((e) => e.x));
      box.max.x = Math.max(...ends.map((e) => e.x));
      box.min.y = Math.min(...ends.map((e) => e.y));
      box.max.y = Math.max(...ends.map((e) => e.y));
    }
    const centre = box.getCenter(new THREE.Vector3());
    if (onSection && s && this.origin) {
      // Centre on the section plane, so the clipped window is in the middle of the view.
      const n = sectionNormal(s.az);
      const c = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, centre.z);
      centre.sub(n.clone().multiplyScalar(n.dot(centre.clone().sub(c))));
    }
    const dir = direction.clone().normalize();
    const forward = dir.clone().negate();
    const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 0, 1)).normalize();
    const up = new THREE.Vector3().crossVectors(right, forward).normalize();
    // Half extents of the content on the screen axes.
    let halfRight = 0;
    let halfUp = 0;
    let radius = 0;
    const corner = new THREE.Vector3();
    for (let i = 0; i < 8; i++) {
      corner.set(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).sub(centre);
      halfRight = Math.max(halfRight, Math.abs(corner.dot(right)));
      halfUp = Math.max(halfUp, Math.abs(corner.dot(up)));
      radius = Math.max(radius, corner.length());
    }
    // The plots of swaths stand beside their cuboids on one side: the view moves over to hold them too.
    const plots = onSection ? [] : [...this.swaths.values()].flatMap((layer) => layer.corners(forward, right, up, this.vexag));
    if (plots.length) {
      let [r0, r1, u0, u1] = [-halfRight, halfRight, -halfUp, halfUp];
      for (const p of plots) {
        p.sub(centre);
        r0 = Math.min(r0, p.dot(right));
        r1 = Math.max(r1, p.dot(right));
        u0 = Math.min(u0, p.dot(up));
        u1 = Math.max(u1, p.dot(up));
        radius = Math.max(radius, p.length());
      }
      centre.addScaledVector(right, (r0 + r1) / 2).addScaledVector(up, (u0 + u1) / 2);
      halfRight = (r1 - r0) / 2;
      halfUp = (u1 - u0) / 2;
    }
    radius = Math.max(radius, 10);
    const aspect = Math.max(this.container.clientWidth, 1) / Math.max(this.container.clientHeight, 1);
    const half = Math.max(halfUp, halfRight / aspect, 5) * 1.08;

    if (this.camera === this.perspective) {
      const distance = (half / Math.tan(THREE.MathUtils.degToRad(this.perspective.fov / 2))) + radius;
      this.perspective.position.copy(centre).add(dir.multiplyScalar(distance));
      this.perspective.near = Math.max(distance / 500, 0.1);
      this.perspective.far = distance * 20 + radius * 4;
      this.perspective.updateProjectionMatrix();
    } else {
      this.ortho.left = -half * aspect;
      this.ortho.right = half * aspect;
      this.ortho.top = half;
      this.ortho.bottom = -half;
      this.ortho.zoom = 1;
      const distance = radius * 4 + 100;
      this.ortho.position.copy(centre).add(dir.multiplyScalar(distance));
      this.ortho.near = 0.1;
      this.ortho.far = distance * 2 + radius * 4;
      this.ortho.updateProjectionMatrix();
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
    for (const id of [...this.blocks.keys()]) this.removeBlockModel(id, false);
    for (const key of [...this.downholes.keys()]) this.removeDownhole(key, false);
    for (const id of [...this.swaths.keys()]) this.setSwath(id, null);
    for (const m of this.labelMaterials.values()) {
      m.map?.dispose();
      m.dispose();
    }
    this.labelMaterials.clear();
    for (const [shape, t] of this.markerTextures) if (shape !== "circle") t.dispose();
    this.markerTextures.clear();
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
    controls.addEventListener("change", () => {
      this.requestRender();
      this.onCamera();
    });
    return controls;
  }

  /** Everything visible (or everything, when nothing is visible). Rasters count only with `withRasters`. */
  private contentBox(withRasters = true, clipped = false): THREE.Box3 | null {
    // With `clipped`, objects entirely outside the section window do not count.
    const inWindow = clipped ? this.windowTest() : null;
    const collect = (visibleOnly: boolean) => {
      const box = new THREE.Box3();
      const add = (extent: THREE.Box3) => {
        if (!inWindow || inWindow(extent)) box.union(extent);
      };
      for (const layer of this.drillholes.values()) if (!visibleOnly || layer.group.visible) add(layer.extent);
      for (const layer of this.wireframes.values()) if (!visibleOnly || layer.group.visible) add(layer.extent);
      for (const layer of this.blocks.values()) if (!visibleOnly || layer.group.visible) add(layer.extent);
      for (const layer of this.lineLayers.values()) if (!visibleOnly || layer.group.visible) add(layer.extent);
      for (const layer of this.swaths.values()) add(layer.extent);
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

  /** A test "does this box reach into the clipping window", or null when nothing is clipped. */
  private windowTest(): ((box: THREE.Box3) => boolean) | null {
    const s = this.section;
    if (!this.clip || !s || !this.origin) return null;
    const n = sectionNormal(s.az);
    const offset = n.x * (s.x0 - this.origin.x) + n.y * (s.y0 - this.origin.y);
    return (box) => {
      let min = Infinity;
      let max = -Infinity;
      for (const x of [box.min.x, box.max.x]) {
        for (const y of [box.min.y, box.max.y]) {
          const d = n.x * x + n.y * y - offset;
          min = Math.min(min, d);
          max = Math.max(max, d);
        }
      }
      return max >= -s.away && min <= s.towards;
    };
  }

  /**
   * In a clipped section a vertical raster (a scanned section) is an underlay, as in Micromine:
   * drawn first and without depth, so holes and wireframe cuts on either side of it stay on top.
   * Otherwise it is an ordinary object of the 3D scene.
   */
  private layerRaster(layer: RasterLayer): void {
    const material = layer.mesh.material as THREE.MeshBasicMaterial;
    if (this.clip && layer.corners3d) {
      material.transparent = false;
      material.opacity = 1;
      material.depthTest = false;
      material.depthWrite = false;
      // A dim copy of the opacity: the colour is mixed toward the background instead of blending.
      const background = new THREE.Color();
      this.renderer.getClearColor(background);
      material.color.copy(new THREE.Color(0xffffff).lerp(background, 1 - layer.opacity));
      layer.mesh.renderOrder = -10;
    } else {
      material.depthTest = true;
      material.color.set(0xffffff);
      this.applyOpacity(material, layer.opacity);
      layer.mesh.renderOrder = -1;
    }
    material.needsUpdate = true;
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
    if (layer.corners3d) layer.corners3d.forEach((c, i) => position.setXYZ(i, c[0] - o.x, c[1] - o.y, c[2] - o.z));
    else layer.corners.forEach((c, i) => position.setXYZ(i, c[0] - o.x, c[1] - o.y, z));
    position.needsUpdate = true;
    layer.mesh.geometry.computeBoundingSphere();
  }

  private contentChanged(): void {
    for (const layer of this.rasters.values()) if (layer.elevation === null && !layer.corners3d) this.placeRaster(layer);
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
    this.grid.visible = this.gridVisible;
    this.root.add(this.grid);
  }

  private applyOpacity(material: THREE.Material, opacity: number): void {
    material.opacity = opacity;
    material.transparent = opacity < 1;
    material.depthWrite = opacity >= 1;
    material.needsUpdate = true;
  }

  private makeLines(positions: number[] | Float32Array, colors: number[] | null, width: number, color: number): LineSegments2 {
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

  /** The section trace and the edges of its window, drawn on top of the content while clipping is off. */
  private updateSectionLine(): void {
    if (this.sectionLine) {
      this.scene.remove(this.sectionLine);
      this.sectionLine.geometry.dispose();
      (this.sectionLine.material as THREE.Material).dispose();
      this.sectionLine = null;
    }
    const s = this.section;
    const box = this.contentBox();
    if (!s || !this.origin || !box || this.clip) return;
    const along = sectionDirection(s.az);
    const n = sectionNormal(s.az);
    const size = box.getSize(new THREE.Vector3());
    const reach = s.length ?? Math.max(size.x, size.y) * 0.75 + 50;
    const start = s.length === null ? -reach : 0;
    const p0 = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
    const z = (box.max.z + 2) * this.vexag;
    const point = (u: number, d: number) => p0.clone().add(along.clone().multiplyScalar(u)).add(n.clone().multiplyScalar(d)).setZ(z);
    const pts: THREE.Vector3[] = [];
    const edge = (d: number) => pts.push(point(start, d), point(reach, d));
    edge(0);
    edge(-s.away);
    edge(s.towards);
    const geometry = new THREE.BufferGeometry().setFromPoints(pts);
    this.sectionLine = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: SECTION_COLOR, depthTest: false, transparent: true, opacity: 0.9 }));
    this.sectionLine.renderOrder = 10;
    this.scene.add(this.sectionLine);
  }

  /** With clipping on, only the window of the section stays: `towards` the viewer, `away` behind the plane. */
  private applyClipping(): void {
    for (const layer of this.wireframes.values()) this.updateCut(layer);
    // Block models shown by their cut follow the section plane.
    for (const layer of this.blocks.values()) if (layer.slice || layer.sliceMesh) this.refreshBlocks(layer);
    const s = this.section;
    if (!this.clip || !s || !this.origin) {
      this.renderer.clippingPlanes = [];
      return;
    }
    const n = sectionNormal(s.az);
    const along = sectionDirection(s.az);
    const c = new THREE.Vector3(s.x0 - this.origin.x, s.y0 - this.origin.y, 0);
    // A fragment is kept where every plane's signed distance is >= 0.
    const planes = [
      new THREE.Plane(n.clone(), -n.dot(c) + s.away),
      new THREE.Plane(n.clone().negate(), n.dot(c) + s.towards),
    ];
    if (s.length !== null) {
      planes.push(new THREE.Plane(along.clone(), -along.dot(c)));
      planes.push(new THREE.Plane(along.clone().negate(), along.dot(c) + s.length));
    }
    this.renderer.clippingPlanes = planes;
  }

  private removeCut(layer: WireframeLayer): void {
    if (!layer.cut) return;
    layer.group.remove(layer.cut);
    disposeTree(layer.cut, this.materials);
    layer.cut = null;
  }

  /**
   * The exact section of a wireframe by the section plane, computed on the server in float64
   * (points relative to the wireframe's origin), or null to clear it. In a clipped section a
   * surface seen edge-on would vanish, so the section is drawn as a thick line in its colour.
   */
  setWireframeCut(id: number, polylines: { closed: boolean; points: number[][] }[] | null): void {
    const layer = this.wireframes.get(id);
    if (!layer) return;
    layer.cutLines = polylines;
    this.updateCut(layer);
    this.requestRender();
  }

  private updateCut(layer: WireframeLayer): void {
    this.removeCut(layer);
    if (!this.clip || !this.section || !layer.cutLines) return;
    const out: number[] = [];
    for (const line of layer.cutLines) {
      const p = line.points;
      const n = line.closed ? p.length : p.length - 1;
      for (let i = 0; i < n; i++) {
        const a = p[i];
        const b = p[(i + 1) % p.length];
        out.push(a[0], a[1], a[2], b[0], b[1], b[2]);
      }
    }
    if (out.length === 0) return;
    layer.cut = this.makeLines(out, null, 3, layer.material.color.getHex());
    layer.cut.renderOrder = 4;
    layer.group.add(layer.cut);
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
    if (!this.clip || !s || !this.origin) return true;
    const d = sectionNormal(s.az).dot(new THREE.Vector3(position.x - (s.x0 - this.origin.x), position.y - (s.y0 - this.origin.y), 0));
    return d <= s.towards && d >= -s.away;
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

  /**
   * A screen point picked for drawing (rendered space): on the horizontal plane through the point
   * the view is centred on, or, for a view from the side, on the vertical plane through it facing
   * the viewer. So a line can be drawn in any view, where the data are, without turning it.
   */
  private pickPoint(clientX: number, clientY: number): THREE.Vector3 | null {
    if (!this.origin) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, this.camera);
    const target = this.controls.target;
    const view = this.viewDirection();
    const plane = Math.abs(view.z) > 0.2
      ? new THREE.Plane(new THREE.Vector3(0, 0, 1), -target.z)
      : new THREE.Plane().setFromNormalAndCoplanarPoint(new THREE.Vector3(view.x, view.y, 0).normalize(), target);
    return raycaster.ray.intersectPlane(plane, new THREE.Vector3());
  }

  /** Project coordinates (x, y) of a rendered-space point. */
  private toProject(p: THREE.Vector3): { x: number; y: number } {
    return { x: p.x + this.origin!.x, y: p.y + this.origin!.y };
  }

  /**
   * Starts (`start` = null: waiting for the first point), continues (the first point given) or
   * ends (`false`) drawing a section line: the first point is marked and a rubber band follows
   * the cursor to it.
   */
  setDraft(start: { x: number; y: number } | null | false): void {
    if (start === false || start === null) {
      this.draftStart = null;
      this.updateDraft(null);
      return;
    }
    if (!this.origin) return;
    const z = this.draftStart?.z ?? this.controls.target.z;
    this.draftStart = new THREE.Vector3(start.x - this.origin.x, start.y - this.origin.y, z);
    this.updateDraft(this.draftStart);
  }

  private updateDraft(end: THREE.Vector3 | null): void {
    if (this.draftLine) {
      this.scene.remove(this.draftLine);
      this.draftLine.geometry.dispose();
      (this.draftLine.material as THREE.Material).dispose();
      this.draftLine = null;
    }
    const a = this.draftStart;
    if (a && end) {
      // Cross marks at both ends, sized to the screen, and the line between them.
      const mark = 6 / Math.max(this.pixelsPerUnit(a), 1e-9);
      const pts: THREE.Vector3[] = [a, end.clone().setZ(a.z)];
      for (const p of [a, end.clone().setZ(a.z)]) {
        pts.push(p.clone().add(new THREE.Vector3(-mark, -mark, 0)), p.clone().add(new THREE.Vector3(mark, mark, 0)));
        pts.push(p.clone().add(new THREE.Vector3(-mark, mark, 0)), p.clone().add(new THREE.Vector3(mark, -mark, 0)));
      }
      this.draftLine = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: DRAFT_COLOR, depthTest: false, transparent: true }));
      this.draftLine.renderOrder = 11;
      this.scene.add(this.draftLine);
    }
    this.requestRender();
  }

  /**
   * What the plan overview of a section shows (project coordinates, visible objects only): hole
   * traces, line layers (thinned to a budget), wireframe and raster outlines.
   */
  planOverview(): PlanOverview {
    const o = this.origin;
    const out: PlanOverview = { holes: [], collars: [], segments: [], boxes: [] };
    if (!o) return out;
    const POINTS = 20000;
    let total = 0;
    for (const layer of this.drillholes.values()) if (layer.group.visible) for (const path of layer.pathOf.values()) total += path.length;
    const every = Math.max(1, Math.ceil(total / POINTS));
    for (const layer of this.drillholes.values()) {
      if (!layer.group.visible) continue;
      for (const path of layer.pathOf.values()) {
        if (!path.length) continue;
        const trace: [number, number][] = [];
        for (let i = 0; i < path.length; i += every) trace.push([path[i][0], path[i][1]]);
        const last = path[path.length - 1];
        if ((path.length - 1) % every) trace.push([last[0], last[1]]);
        out.holes.push(trace);
        out.collars.push([path[0][0], path[0][1]]);
      }
    }
    for (const layer of this.lineLayers.values()) {
      if (!layer.group.visible || !layer.lines) continue;
      const start = layer.lines.geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute | undefined;
      if (!start) continue;
      const a = start.data.array;
      const count = Math.floor(a.length / 6);
      const step = Math.max(1, Math.ceil(count / POINTS));
      const gx = layer.group.position.x + o.x;
      const gy = layer.group.position.y + o.y;
      for (let i = 0; i < count; i += step) out.segments.push([a[i * 6] + gx, a[i * 6 + 1] + gy, a[i * 6 + 3] + gx, a[i * 6 + 4] + gy]);
    }
    for (const layer of this.wireframes.values()) {
      if (!layer.group.visible || layer.extent.isEmpty()) continue;
      out.boxes.push([layer.extent.min.x + o.x, layer.extent.min.y + o.y, layer.extent.max.x + o.x, layer.extent.max.y + o.y]);
    }
    for (const layer of this.rasters.values()) {
      if (!layer.visible) continue;
      const p = layer.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
      const b = new THREE.Box3();
      for (let i = 0; i < 4; i++) b.expandByPoint(new THREE.Vector3(p.getX(i), p.getY(i), 0));
      out.boxes.push([b.min.x + o.x, b.min.y + o.y, b.max.x + o.x, b.max.y + o.y]);
    }
    for (const layer of this.blocks.values()) {
      if (!layer.group.visible) continue;
      out.boxes.push([layer.extent.min.x + o.x, layer.extent.min.y + o.y, layer.extent.max.x + o.x, layer.extent.max.y + o.y]);
    }
    return out;
  }

  private attachPointerEvents(): void {
    const el = this.renderer.domElement;
    let down: { x: number; y: number; t: number } | null = null;
    let hoverFrame = 0;
    // The string editor sees every event first; while it holds a drag, the camera stands still.
    let editDrag = false;
    const offer = (type: ScenePointer["type"], e: MouseEvent): boolean => {
      if (!this.onEditPointer) return false;
      const rect = el.getBoundingClientRect();
      return this.onEditPointer({ type, x: e.clientX - rect.left, y: e.clientY - rect.top, clientX: e.clientX, clientY: e.clientY,
        button: e.button, shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey });
    };
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY, t: performance.now() };
      if (offer("down", e)) {
        editDrag = true;
        this.controls.enabled = false;
      }
    };
    const onUp = (e: PointerEvent) => {
      if (editDrag) {
        editDrag = false;
        this.controls.enabled = true;
        const taken = offer("up", e);
        if (taken) {
          down = null;
          return;
        }
      }
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      const quick = performance.now() - down.t < 600;
      down = null;
      if (moved >= 4 || !quick) return;
      if (offer("click", e)) return;
      if (this.onGroundClick) {
        const p = this.pickPoint(e.clientX, e.clientY);
        if (p && this.onGroundClick(this.toProject(p).x, this.toProject(p).y)) return;
      }
      const hit = this.nearestCollar(e.clientX, e.clientY, 14);
      if (!hit) {
        const object = this.pickObject(e.clientX, e.clientY);
        if (object?.kind === "block" && this.onPickBlock?.(object)) return;
        if (object?.kind === "wireframe" && this.onPickWireframe?.(object.id, this.rayAt(e.clientX, e.clientY))) return;
      }
      this.onPick(hit ? { setId: hit.setId, holeId: hit.holeId } : null);
    };
    const onMove = (e: PointerEvent) => {
      if (this.onEditPointer && (editDrag || !down)) {
        offer("move", e);
        if (editDrag) return;
      }
      if (this.draftStart && !down) {
        const p = this.pickPoint(e.clientX, e.clientY);
        if (p) this.updateDraft(p);
      }
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
    const onDouble = (e: MouseEvent) => void offer("dblclick", e);
    el.addEventListener("dblclick", onDouble);
    this.cleanup.push(() => {
      el.removeEventListener("dblclick", onDouble);
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
    // Cuts of block models are mixed toward the background.
    for (const layer of this.blocks.values()) this.renderer.getClearColor(layer.edges.back.value);
    // Swath plots take the colour of their lines and of the plate under the plot from the background.
    for (const [id, layer] of [...this.swaths]) this.setSwath(id, layer.build);
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
    this.onCamera();
  }

  /**
   * The near and far planes hold everything there is, from wherever the camera now looks: set
   * once at «Вписать», they cut the scene after a turn, a pan, a zoom or newly shown objects.
   * The orthographic camera also steps back out of the content (its distance does not change the
   * picture, only what lies behind it).
   */
  private fitDepth(): void {
    const box = this.contentBox(true) ?? new THREE.Box3();
    // The plot of a swath stands beside its cuboid, wherever the viewer is.
    for (const layer of this.swaths.values()) box.union(layer.reach);
    if (this.grid) {
      // In the content's own coordinates (the root's Z stretch comes below, for both).
      const g = this.grid.geometry;
      if (!g.boundingBox) g.computeBoundingBox();
      this.grid.updateMatrix();
      box.union(g.boundingBox!.clone().applyMatrix4(this.grid.matrix));
    }
    if (box.isEmpty()) return;
    const scaled = box.clone();
    scaled.min.z *= this.vexag;
    scaled.max.z *= this.vexag;
    if (this.vexag < 0) [scaled.min.z, scaled.max.z] = [scaled.max.z, scaled.min.z];
    const sphere = scaled.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sphere.radius * 1.15, 10);
    const cam = this.camera.position;
    const forward = this.controls.target.clone().sub(cam);
    if (forward.lengthSq() === 0) return;
    forward.normalize();
    let depth = sphere.center.clone().sub(cam).dot(forward);
    if (this.camera === this.ortho) {
      if (depth - radius < 1) {
        cam.addScaledVector(forward, depth - radius - 1);
        depth = radius + 1;
      }
      this.ortho.near = Math.max(0.1, depth - radius);
      this.ortho.far = depth + radius;
    } else {
      const far = Math.max(depth + radius, 1);
      this.perspective.near = Math.max(0.1, depth - radius, far / 20000);
      this.perspective.far = far;
    }
    this.camera.updateProjectionMatrix();
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
      this.chooseLevels();
      this.updateDownholes();
      if (this.swaths.size) {
        this.camera.updateMatrixWorld();
        for (const layer of this.swaths.values()) layer.update(this.camera, this.vexag, (at) => this.pixelsPerUnit(at));
      }
      this.fitDepth();
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

/** A white marker with a dark outline, tinted by the colour of the point. */
function makeMarkerTexture(shape: MarkerShape): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "rgba(0,0,0,0.6)";
  ctx.lineWidth = 5;
  ctx.lineJoin = "round";
  const poly = (pts: number[][]) => {
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  };
  if (shape === "square") poly([[8, 8], [56, 8], [56, 56], [8, 56]]);
  else if (shape === "diamond") poly([[32, 4], [60, 32], [32, 60], [4, 32]]);
  else if (shape === "triangle") poly([[32, 5], [59, 56], [5, 56]]);
  else if (shape === "cross") poly([[22, 4], [42, 4], [42, 22], [60, 22], [60, 42], [42, 42], [42, 60], [22, 60], [22, 42], [4, 42], [4, 22], [22, 22]]);
  else if (shape === "star") {
    const pts: number[][] = [];
    for (let i = 0; i < 10; i++) {
      const r = i % 2 ? 13 : 29;
      const a = -Math.PI / 2 + (i * Math.PI) / 5;
      pts.push([32 + r * Math.cos(a), 32 + r * Math.sin(a)]);
    }
    poly(pts);
  } else {
    ctx.beginPath();
    ctx.arc(32, 32, 28, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  return new THREE.CanvasTexture(canvas);
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
