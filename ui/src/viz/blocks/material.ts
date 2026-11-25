/**
 * The material of the shell of a block model (ADR 0023 §11 «Отрисовка»): one instanced quad per face, the corners
 * and the colour found in the shader — the CPU never builds a vertex. The class `k` of the face is looked up in the
 * legend texture (`legend.ts`); the edges are drawn by the fragment shader from the distance to the edge of the
 * face in pixels (`fwidth`), so their width is the same at any zoom, and a face smaller than a few pixels fades its
 * edges out instead of turning dark. A fixed light from above and the side gives the faces their shade.
 *
 * The geometry is the buffers of the decoded answer as they are (float32 centres from the integer origin of the
 * model; `a`, `k` read by the GPU as bytes / shorts). The matrix of the layer is O + R_z(φ).
 */
import * as THREE from "three";
import type { BlocksData } from "./decode";
import { FACE_UV } from "./decode";

/** `vec3(…)` of the unit vector along `axis` (GLSL text of a constant of the shader). */
const unit = (axis: number): string => `vec3(${[0, 1, 2].map((i) => (i === axis ? "1.0" : "0.0")).join(", ")})`;

/** `axis < 0.5 ? A : axis < 1.5 ? B : C` over the three axes. */
const pick = (f: (axis: number) => string): string => `axis < 0.5 ? ${f(0)} : (axis < 1.5 ? ${f(1)} : ${f(2)})`;

export const VERTEX = /* glsl */ `
attribute vec3 iC;
attribute vec2 iH;
attribute float iA;
attribute float iK;
varying vec2 vLocal;
flat varying vec2 vHalf;
flat varying float vK;
flat varying float vShade;
uniform float uShade;
#include <clipping_planes_pars_vertex>
void main() {
  float axis = floor(iA * 0.5 + 0.25);
  float side = iA - 2.0 * axis;
  vec3 eu = ${pick((a) => unit(FACE_UV[a][0]))};
  vec3 ev = ${pick((a) => unit(FACE_UV[a][1]))};
  vec3 n = ${pick((a) => unit(a))} * (side > 0.5 ? 1.0 : -1.0);
  vec2 l = position.xy * iH;
  vec3 p = iC + eu * l.x + ev * l.y;
  vec3 nw = normalize(mat3(modelMatrix) * n);
  float lit = clamp(dot(nw, normalize(vec3(-0.4, -0.5, 0.77))) * 0.5 + 0.5, 0.0, 1.0);
  vShade = mix(1.0, 0.55 + 0.45 * lit, uShade);
  vLocal = l;
  vHalf = iH;
  vK = iK;
  vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  #include <clipping_planes_vertex>
}
`;

export const FRAGMENT = /* glsl */ `
uniform sampler2D uLegendMap;
uniform vec2 uLegend;
uniform vec3 uNoValue;
uniform float uOpacity;
uniform float uEdges;
uniform float uEdgeWidth;
uniform float uEdgeOpacity;
uniform vec3 uEdgeColor;
varying vec2 vLocal;
flat varying vec2 vHalf;
flat varying float vK;
flat varying float vShade;
#include <clipping_planes_pars_fragment>
void main() {
  #include <clipping_planes_fragment>
  float k = floor(vK + 0.5);
  vec3 base = uNoValue;
  if (k < 65534.5) {
    float row = floor(k / 256.0);
    float col = k - row * 256.0;
    base = texture2D(uLegendMap, vec2((col + 0.5) / 256.0, (row + 0.5) / uLegend.y)).rgb;
  }
  vec3 colour = base * vShade;
  if (uEdges > 0.5) {
    vec2 px = max(fwidth(vLocal), vec2(1e-9));
    vec2 toEdge = (vHalf - abs(vLocal)) / px;
    float inner = min(toEdge.x, toEdge.y);
    float line = 1.0 - smoothstep(uEdgeWidth - 0.5, uEdgeWidth + 0.5, inner);
    vec2 sizePx = vHalf / px;
    float big = smoothstep(1.5, 4.0, min(sizePx.x, sizePx.y));
    colour = mix(colour, uEdgeColor, line * big * uEdgeOpacity);
  }
  gl_FragColor = vec4(colour, uOpacity);
  #include <colorspace_fragment>
}
`;

/** The look the user sets (the window «Блочная модель»: рёбра, непрозрачность). */
export interface BlocksLook {
  /** draw the edges of the faces */
  edges: boolean;
  /** px */
  edgeWidth: number;
  /** 0…1 */
  edgeOpacity: number;
  /** `#rrggbb` */
  edgeColor: string;
  /** 0…1; below 1 the faces blend (no depth write) */
  opacity: number;
  /** the fixed light on the faces */
  shade: boolean;
}

/** Edges are for «Все блоки» (blocks apart); a shell is a smooth skin by default. Anthracite of the brand. */
export function defaultLook(mode: "shell" | "all"): BlocksLook {
  return { edges: mode === "all", edgeWidth: 1, edgeOpacity: 0.6, edgeColor: "#1B222B", opacity: 1, shade: true };
}

export interface BlocksMaterialContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
}

export function createBlocksMaterial(ctx: BlocksMaterialContext, legend: THREE.DataTexture, look: BlocksLook, noValue: string): THREE.ShaderMaterial {
  const m = new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: {
      uLegendMap: { value: legend },
      uLegend: { value: new THREE.Vector2(legend.image.width, legend.image.height) },
      uNoValue: { value: new THREE.Color(noValue) },
      uOpacity: { value: 1 },
      uEdges: { value: 0 },
      uEdgeWidth: { value: 1 },
      uEdgeOpacity: { value: 0 },
      uEdgeColor: { value: new THREE.Color() },
      uShade: { value: 1 },
    },
    side: THREE.DoubleSide,
    clipping: true,
  });
  m.clippingPlanes = ctx.clip;
  applyLook(m, look);
  return m;
}

/** Look → uniforms and blending; no new shader. */
export function applyLook(m: THREE.ShaderMaterial, look: BlocksLook): void {
  const u = m.uniforms;
  u.uEdges.value = look.edges ? 1 : 0;
  u.uEdgeWidth.value = Math.max(0.5, look.edgeWidth);
  u.uEdgeOpacity.value = Math.min(1, Math.max(0, look.edgeOpacity));
  (u.uEdgeColor.value as THREE.Color).set(look.edgeColor);
  u.uShade.value = look.shade ? 1 : 0;
  const opacity = Math.min(1, Math.max(0, look.opacity));
  u.uOpacity.value = opacity;
  const blend = opacity < 1;
  if (m.transparent !== blend) {
    m.transparent = blend;
    m.needsUpdate = true;
  }
  m.depthWrite = !blend;
}

/** The unit quad of a face, shared by all the instances. */
function quad(): THREE.InstancedBufferGeometry {
  const g = new THREE.InstancedBufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(Float32Array.of(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0), 3));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

/**
 * The instanced geometry of the shell: the decoded arrays are the attributes (no copy; the GPU reads `a` as bytes
 * and `k` as shorts). A face is an instance; the answer of a new request replaces the whole geometry.
 */
export function faceGeometry(d: BlocksData): THREE.InstancedBufferGeometry {
  const g = quad();
  g.setAttribute("iC", new THREE.InstancedBufferAttribute(d.c, 3));
  g.setAttribute("iH", new THREE.InstancedBufferAttribute(d.h, 2));
  g.setAttribute("iA", new THREE.InstancedBufferAttribute(d.a, 1));
  g.setAttribute("iK", new THREE.InstancedBufferAttribute(d.k, 1));
  g.instanceCount = d.faces;
  return g;
}

/**
 * The corners of face `i` as the shader places them, in the local axes of the model minus `origin` (float64 of the
 * float32 inputs): the counterpart of the vertex shader for tests, the pick and the extent — the same table of axes.
 */
export function faceCorners(d: Pick<BlocksData, "c" | "h" | "a">, i: number): number[][] {
  const axis = d.a[i] >> 1;
  const [u, v] = FACE_UV[axis];
  const out: number[][] = [];
  for (const [su, sv] of [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ]) {
    const p = [d.c[3 * i], d.c[3 * i + 1], d.c[3 * i + 2]];
    p[u] += su * d.h[2 * i];
    p[v] += sv * d.h[2 * i + 1];
    out.push(p);
  }
  return out;
}
