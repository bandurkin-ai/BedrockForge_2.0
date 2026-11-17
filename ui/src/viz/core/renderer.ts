/**
 * The WebGL2 renderer of the 3D window (ADR 0004 §1, §4; BF `scene3d.ts` 422–451, 2206–2230): one canvas,
 * `powerPreference: "high-performance"`, no `preserveDrawingBuffer` (a snapshot is a separate pass into a
 * render target), local clipping planes on, the class of graphics from `WEBGL_debug_renderer_info`, and
 * loss / restore of the context: three.js keeps its objects, the scene rebuilds its resources from the core.
 */
import * as THREE from "three";
import { VizError } from "../error";
import { gpuClass, lossFactor, type GpuClass } from "./budget";

export interface GpuInfo {
  renderer: string;
  vendor: string;
  gpuClass: GpuClass;
}

export interface FrameStats {
  calls: number;
  triangles: number;
  points: number;
  lines: number;
  geometries: number;
  textures: number;
}

/** The renderer string of a context, unmasked when the browser allows it. */
function describe(gl: WebGLRenderingContext | WebGL2RenderingContext): { renderer: string; vendor: string } {
  const dbg = gl.getExtension("WEBGL_debug_renderer_info");
  return {
    renderer: String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)),
    vendor: String(dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)),
  };
}

export interface WebGl2Probe {
  ok: boolean;
  /** what the window could find out about the graphics (for the diagnostic window) */
  renderer: string;
  reason: string;
}

/** Can this window make a WebGL2 context, and if not — why, and which graphics it sees (via WebGL 1). */
export function probeWebGL2(): WebGl2Probe {
  let reason = "";
  const canvas = document.createElement("canvas");
  canvas.addEventListener("webglcontextcreationerror", (e) => (reason = (e as WebGLContextEvent).statusMessage || reason), { once: true });
  let gl: WebGL2RenderingContext | null = null;
  try {
    gl = canvas.getContext("webgl2", { powerPreference: "high-performance" });
  } catch (e) {
    reason = e instanceof Error ? e.message : String(e);
  }
  if (gl) {
    const info = describe(gl);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return { ok: true, renderer: info.renderer, reason: "" };
  }
  let renderer = "не определена";
  try {
    const gl1 = document.createElement("canvas").getContext("webgl");
    if (gl1) {
      renderer = describe(gl1).renderer;
      gl1.getExtension("WEBGL_lose_context")?.loseContext();
    }
  } catch {
    /* no WebGL at all */
  }
  return { ok: false, renderer, reason: reason || "Видеокарта или драйвер не дают контекст WebGL2." };
}

export class Renderer {
  readonly three: THREE.WebGLRenderer;
  readonly canvas: HTMLCanvasElement;
  readonly info: GpuInfo;
  lost = false;
  /** times of the context losses (ms), for «три потери за минуту» */
  readonly losses: number[] = [];
  onLost: () => void = () => {};
  onRestored: () => void = () => {};
  private readonly cleanup: (() => void)[] = [];

  constructor(host: HTMLElement) {
    this.canvas = document.createElement("canvas");
    this.canvas.dataset.bfScene = "1";
    this.canvas.style.cssText = "display:block;width:100%;height:100%;outline:none";
    let three: THREE.WebGLRenderer;
    try {
      three = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: false, alpha: false, preserveDrawingBuffer: false, powerPreference: "high-performance" });
    } catch (e) {
      throw new VizError("no_webgl2", "Не удалось создать контекст WebGL2.", `Обновите драйвер видеокарты или включите программную отрисовку. ${e instanceof Error ? e.message : ""}`.trim());
    }
    this.three = three;
    three.localClippingEnabled = true;
    const d = describe(three.getContext());
    this.info = { ...d, gpuClass: gpuClass(d.renderer) };
    // ADR 0004 §1: the integrated graphics of the acceptance PC — no MSAA, pixel density 1.
    three.setPixelRatio(this.info.gpuClass === "discrete" ? Math.min(window.devicePixelRatio || 1, 2) : 1);
    host.appendChild(this.canvas);
    const lost = () => {
      // three.js calls preventDefault, so the browser restores the context
      this.lost = true;
      this.losses.push(performance.now());
      if (this.losses.length > 10) this.losses.shift();
      this.onLost();
    };
    const restored = () => {
      this.lost = false;
      this.onRestored();
    };
    this.canvas.addEventListener("webglcontextlost", lost);
    this.canvas.addEventListener("webglcontextrestored", restored);
    this.cleanup.push(() => {
      this.canvas.removeEventListener("webglcontextlost", lost);
      this.canvas.removeEventListener("webglcontextrestored", restored);
    });
  }

  get pixelRatio(): number {
    return this.three.getPixelRatio();
  }

  /** 0.5 after three losses within a minute (ADR 0004 §4): budgets halved. */
  budgetFactor(now = performance.now()): 1 | 0.5 {
    return lossFactor(this.losses, now);
  }

  setSize(w: number, h: number): void {
    this.three.setSize(Math.max(1, w), Math.max(1, h), false);
  }

  setClearColor(css: string): void {
    this.three.setClearColor(new THREE.Color(css || "#11161d"), 1);
  }

  render(scene: THREE.Scene, camera: THREE.Camera): void {
    if (this.lost) return;
    this.three.render(scene, camera);
  }

  stats(): FrameStats {
    const i = this.three.info;
    return { calls: i.render.calls, triangles: i.render.triangles, points: i.render.points, lines: i.render.lines, geometries: i.memory.geometries, textures: i.memory.textures };
  }

  /**
   * A snapshot pass (ADR 0004 §1): the scene drawn into a render target of the canvas size and read back,
   * RGBA rows from the bottom. The picture on the screen is not touched.
   */
  snapshot(scene: THREE.Scene, camera: THREE.Camera): { width: number; height: number; data: Uint8Array } | null {
    if (this.lost) return null;
    const size = this.three.getDrawingBufferSize(new THREE.Vector2());
    const w = Math.max(1, Math.floor(size.x));
    const h = Math.max(1, Math.floor(size.y));
    const target = new THREE.WebGLRenderTarget(w, h, { type: THREE.UnsignedByteType, format: THREE.RGBAFormat });
    target.texture.colorSpace = THREE.SRGBColorSpace;
    const data = new Uint8Array(w * h * 4);
    const before = this.three.getRenderTarget();
    try {
      this.three.setRenderTarget(target);
      this.three.render(scene, camera);
      this.three.readRenderTargetPixels(target, 0, 0, w, h, data);
    } finally {
      this.three.setRenderTarget(before);
      target.dispose();
    }
    return { width: w, height: h, data };
  }

  /** Waits for the GPU to finish the frame (a one-pixel read): the frame benchmark measures whole frames. */
  finish(): void {
    if (this.lost) return;
    const gl = this.three.getContext();
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, this.probe);
  }

  private readonly probe = new Uint8Array(4);

  dispose(): void {
    this.cleanup.forEach((fn) => fn());
    this.three.dispose();
    this.three.forceContextLoss();
    this.canvas.remove();
  }
}

/** Pixels that differ from the commonest colour (the background) by more than `tolerance` per channel. */
export function nonBackgroundPixels(data: Uint8Array, tolerance = 8): number {
  const counts = new Map<number, number>();
  for (let i = 0; i < data.length; i += 4) {
    const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let bg = 0;
  let best = -1;
  for (const [k, n] of counts) if (n > best) [bg, best] = [k, n];
  const r = (bg >> 16) & 255;
  const g = (bg >> 8) & 255;
  const b = bg & 255;
  let out = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (Math.abs(data[i] - r) > tolerance || Math.abs(data[i + 1] - g) > tolerance || Math.abs(data[i + 2] - b) > tolerance) out++;
  }
  return out;
}

/**
 * Bytes of the buffers and textures of a scene as uploaded to the video card (an estimate for the counter
 * and the frame budget): every attribute, index and interleaved buffer once, textures as RGBA with mipmaps.
 */
export function sceneBytes(root: THREE.Object3D): number {
  const seen = new Set<unknown>();
  let bytes = 0;
  const add = (a: { byteLength: number } | null | undefined) => {
    if (!a || seen.has(a)) return;
    seen.add(a);
    bytes += a.byteLength;
  };
  root.traverse((o) => {
    const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
    if (g?.attributes) {
      for (const a of Object.values(g.attributes)) add((a as THREE.InterleavedBufferAttribute).data?.array ?? (a as THREE.BufferAttribute).array);
      add(g.index?.array);
    }
    const mats = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
    for (const m of Array.isArray(mats) ? mats : mats ? [mats] : []) {
      const map = (m as THREE.MeshBasicMaterial).map;
      const img = map?.image as { width?: number; height?: number } | undefined;
      if (map && !seen.has(map) && img?.width && img.height) {
        seen.add(map);
        bytes += Math.round(img.width * img.height * 4 * 1.34);
      }
    }
  });
  return bytes;
}
