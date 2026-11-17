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
}

interface Window {
  bf?: { core(): { baseUrl: string; token: string; version: string } | null };
  /** E2E hook of the 3D window (BF_E2E=1): ui/src/modules/scene/SceneView.tsx */
  __bfScene?: {
    ready(): boolean;
    state(): BfSceneState;
    pixels(): { width: number; height: number; nonBackground: number } | null;
    collars(limit?: number): { setId: number; name: string; x: number; y: number }[];
  };
  __bfLose?: { loseContext(): void; restoreContext(): void } | null;
}
