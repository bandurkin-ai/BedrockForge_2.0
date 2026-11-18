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
  /** line and point layers: layers in the scene, parts, segments and markers shown, simplified parts */
  lines: { layers: number; parts: number; segments: number; points: number; simplified: number; over: boolean };
  /** rasters: the copy each shows (0 — ≤ 4096 px, 1 — ≤ 1024 px, null — not yet), those drawn as backdrops */
  rasters: { count: number; levels: Record<string, number | null>; backdrops: number[]; over: boolean };
  /** the object picked by the last click that hit no hole */
  picked: { kind: "lines"; id: number; line: number; on: "line" | "point"; xyz: number[] } | { kind: "raster"; id: number; xyz: number[] } | null;
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
    run(id: string, args?: Record<string, unknown>): Promise<{ ok: boolean; result: string }>;
    bench(frames: number): { frames: number; ms_per_frame: number; ms_median: number; calls: number; triangles: number; gpu_bytes: number; gpu: { renderer: string; gpuClass: string } };
  };
  __bfLose?: { loseContext(): void; restoreContext(): void } | null;
}
