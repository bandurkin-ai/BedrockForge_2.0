interface BfSceneState {
  frames: number;
  idle: boolean;
  ready: boolean;
  lost: boolean;
  loading: number;
  stats: { calls: number; triangles: number; points: number; lines: number };
  camera: { position: number[]; target: number[]; up: number[]; half: number; near: number; far: number; dir: number[] };
  perspective: boolean;
  vexag: number;
  clipPlanes: number;
  section: { x0: number; y0: number; az: number; length: number | null; towards: number; away: number } | null;
  gpu: { renderer: string; vendor: string; gpuClass: string };
  sets: number[];
  segments: number;
  /** the last pick on the CPU, ms */
  pickMs: number;
  /** keys of the layers added to the scene (`ln:<id>`, `rs:<id>`, `dh:…`) */
  layers: string[];
  /**
   * line and point layers: layers in the scene, parts, segments and markers shown, simplified parts; `cloud` —
   * the points of clouds wanted in the last frame against the point budget of the frame (ADR 0014 §2)
   */
  lines: { layers: number; parts: number; segments: number; points: number; simplified: number; over: boolean; cloud: { points: number; budget: number; simplified: boolean; over: boolean } };
  /**
   * rasters: the copy each shows (0 — ≤ 4096 px, 1 — ≤ 1024 px, null — not yet), those drawn as backdrops, those
   * a DTM carries — `covered` (not drawn flat) and `cut` (flat with holes under the surfaces)
   */
  rasters: { count: number; levels: Record<string, number | null>; backdrops: number[]; covered: number[]; cut: number[]; over: boolean };
  /**
   * wireframes and DTMs: layers in the scene; of the visible ones — parts and triangles shown, parts shown with
   * fewer triangles than in full, parts shown at every level (`shown[lod]`), DTMs that show a raster
   */
  wireframes: { layers: number; parts: number; triangles: number; simplified: number; shown: number[]; draped: number[]; budgeted: boolean; over: boolean };
  /**
   * the object picked by the last click that hit no hole; of a wireframe — `exact`: the triangle the core found
   * under the click (null — none, absent — not answered yet), `xyz` is then the point on it
   */
  picked:
    | { kind: "lines"; id: number; line: number; on: "line" | "point"; xyz: number[] }
    | { kind: "raster"; id: number; xyz: number[] }
    | { kind: "wireframe"; id: number; xyz: number[]; exact?: { wireframeId: number; triangle: number; distance: number; point: number[]; area: number; dip: number | null; dipDirection: number | null } | null }
    | null;
  /**
   * what the core computes exactly (Э8): the section lines per wireframe under clipping and the marks where
   * the holes of a set cross wireframes
   */
  exact: {
    sections: { wireframe: number; lines: number; closed: number; segments: number; color: string; bbox: number[] | null }[];
    marks: { set: number; marks: number; labels: number; objects: number[] }[];
  };
  /** the red layers of checked wireframes: the triangles that cross each other (Э8, `chk:<id>`) */
  checks: { wireframe: number; triangles: number; groups: number; visible: boolean }[];
  /** the level of what has no height of its own: a metre under the lowest object */
  floor: number;
  /** the layer of displays along holes */
  displays: { legend: { key: string; name: string; holes: number; intervals: number; entries: { title: string; counts: number[] }[] }[]; simplified: number; loading: number; over: boolean };
}

interface Window {
  bf?: { core(): { baseUrl: string; token: string; version: string } | null };
  /** E2E hook of the 3D window (BF_E2E=1): ui/src/modules/scene/SceneView.tsx */
  __bfScene?: {
    ready(): boolean;
    state(): BfSceneState;
    pixels(): { width: number; height: number; nonBackground: number } | null;
    collars(limit?: number): { setId: number; name: string; x: number; y: number }[];
    screen(x: number, y: number, z: number): { x: number; y: number } | null;
    /** the colour of the frame at a client point, drawn now: [r, g, b, a]; null — outside the view */
    pixel(x: number, y: number): number[] | null;
    /** the surface of a wireframe over a plan point as drawn now; `uv` — image coordinates of the draped raster */
    probe(id: number, x: number, y: number): { k: number; lod: number; triangles: number; z: number; uv: [number, number] | null; exact: [number, number] | null } | null;
    /** every part of the surfaces at this level of detail (null — by the size on the screen) */
    lod(level: number | null): void;
    run(id: string, args?: Record<string, unknown>): Promise<{ ok: boolean; result: string }>;
    /** `frames` frames turning a full circle: the largest draw calls, triangles and points of a frame */
    bench(frames: number): { frames: number; ms_per_frame: number; ms_median: number; calls: number; triangles: number; points: number; gpu_bytes: number; gpu: { renderer: string; gpuClass: string } };
  };
  __bfLose?: { loseContext(): void; restoreContext(): void } | null;
}
