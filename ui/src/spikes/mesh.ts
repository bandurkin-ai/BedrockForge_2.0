/**
 * Spike Э0-б: draw ~1 million triangles with three.js (WebGL2) and measure frame time.
 * Runs under software rendering (SwiftShader) in CI; results are exposed on window.__bfSpike.
 */
import * as THREE from "three";
import { integerOrigin, toRelativeFloat32 } from "../viz/origin";
import { bridge } from "../bridge";

export interface MeshSpikeResult {
  triangles: number;
  frames: number;
  msPerFrame: number;
  firstFrameMs: number;
  renderer: string;
  webgl2: boolean;
}

declare global {
  interface Window {
    __bfSpike?: MeshSpikeResult | { error: string };
  }
}

/** Wavy surface in UTM coordinates: nx*ny quads -> 2*nx*ny triangles. */
function surface(nx: number, ny: number): { positions: Float64Array; index: Uint32Array } {
  const positions = new Float64Array((nx + 1) * (ny + 1) * 3);
  let p = 0;
  for (let j = 0; j <= ny; j++) {
    for (let i = 0; i <= nx; i++) {
      const x = 500_000 + i * 2.0;
      const y = 7_000_000 + j * 2.0;
      positions[p++] = x;
      positions[p++] = y;
      positions[p++] = 300 + 40 * Math.sin(i / 37) * Math.cos(j / 23);
    }
  }
  const index = new Uint32Array(nx * ny * 6);
  let q = 0;
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const a = j * (nx + 1) + i;
      const b = a + 1;
      const c = a + nx + 1;
      const d = c + 1;
      index[q++] = a; index[q++] = b; index[q++] = d;
      index[q++] = a; index[q++] = d; index[q++] = c;
    }
  }
  return { positions, index };
}

export async function runMeshSpike(host: HTMLElement): Promise<void> {
  try {
    const t0 = performance.now();
    document.body.style.cssText = "margin:0;overflow:hidden";
    host.style.cssText = "width:100vw;height:100vh;background:#15181d";
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "width:100%;height:100%;display:block";
    host.replaceChildren(canvas);
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: false });
    renderer.setPixelRatio(1);
    renderer.setSize(host.clientWidth || 1280, host.clientHeight || 720, false);
    const gl = renderer.getContext();
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    const rendererName = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));

    const { positions, index } = surface(1000, 500); // 1 000 000 triangles
    const origin = integerOrigin(positions);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(toRelativeFloat32(positions, origin), 3));
    geom.setIndex(new THREE.BufferAttribute(index, 1));
    geom.computeVertexNormals();
    const mesh = new THREE.Mesh(geom, new THREE.MeshLambertMaterial({ color: 0xc8a165, side: THREE.DoubleSide }));

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x15181d);
    scene.add(mesh);
    scene.add(new THREE.AmbientLight(0xffffff, 0.5));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(-1, -2, 3);
    scene.add(sun);

    geom.computeBoundingSphere();
    const r = geom.boundingSphere!.radius;
    const camera = new THREE.PerspectiveCamera(45, canvas.clientWidth / Math.max(1, canvas.clientHeight), r / 100, r * 10);
    camera.up.set(0, 0, 1);
    const probe = new Uint8Array(4);
    const frame = (k: number) => {
      const a = 0.6 + k * 0.05;
      camera.position.set(Math.cos(a) * r * 1.6, Math.sin(a) * r * 1.6, r * 0.9);
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, probe); // forces the GPU to finish
    };

    frame(0);
    const firstFrameMs = performance.now() - t0;
    const frames = 10;
    const t1 = performance.now();
    for (let k = 1; k <= frames; k++) frame(k);
    const msPerFrame = (performance.now() - t1) / frames;
    frame(0); // leave a stable picture for the screenshot

    window.__bfSpike = {
      triangles: index.length / 3,
      frames,
      msPerFrame: Math.round(msPerFrame * 10) / 10,
      firstFrameMs: Math.round(firstFrameMs),
      renderer: rendererName,
      webgl2: renderer.capabilities.isWebGL2,
    };
  } catch (e) {
    window.__bfSpike = { error: e instanceof Error ? e.message : String(e) };
  } finally {
    bridge()?.uiReady();
  }
}
