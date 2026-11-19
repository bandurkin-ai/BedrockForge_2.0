/**
 * A raster draped on a surface — a digital terrain model (ТЗ 7.20, ADR 0011 §4; BF `imageAxes`, `imageWindow`,
 * `drapeMaterial` 183–205, 1483–1502). The image coordinates of a fragment come from the plan position of the
 * vertex, not from stored texture coordinates: they are an affine function of the plan point, so the image
 * lies the same on every level of detail and repeats the relief.
 *
 * Accuracy. The affine of the raster is float64 (its corners: top-left, top-right, bottom-right, bottom-left —
 * `rasters.georef.corners3d` of the core). A vertex is float32 from the integer origin of its part, so the
 * shader gets the top-left corner of the image from that same origin (float64 difference → float32) and
 * subtracts first: `uv = M · (position.xy − o)`. The constant error is the rounding of `o` (≤ 0.25 mm at
 * 8 km from the origin), the rest is relative 1e-7 — whatever the size of the image. UTM coordinates never
 * reach the video card (ТЗ 5.4).
 *
 * Unlike BF the rows of the image count from the top (v = 0 — row 0): BF2 uploads an `ImageBitmap` as it is,
 * and the quad of the flat raster is laid out the same way, so both share one texture.
 * Pure: no three.js; the layer makes the uniforms.
 */

export type Corner = ArrayLike<number | null | undefined> | null | undefined;

export interface DrapeAxes {
  /** the top-left corner of the image in the coordinates of the vertices (plan x, y) */
  o: [number, number];
  /** u = u[0]·(x − o[0]) + u[1]·(y − o[1]): 0 at the left edge of the image, 1 at the right */
  u: [number, number];
  /** v — the same down the image: 0 at its top row, 1 at the bottom */
  v: [number, number];
}

function xy(c: Corner): [number, number] | null {
  const x = c?.[0];
  const y = c?.[1];
  return typeof x === "number" && typeof y === "number" && Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
}

/**
 * Image coordinates of a plan point: `corners` — top-left, top-right, bottom-right, bottom-left of the image
 * (absolute float64; heights are not used), `origin` — the point the vertices are counted from. Null for an
 * image without area or with an empty corner.
 */
export function imageAxes(corners: ArrayLike<Corner> | null | undefined, origin: ArrayLike<number>): DrapeAxes | null {
  if (!corners || corners.length !== 4) return null;
  const tl = xy(corners[0]);
  const tr = xy(corners[1]);
  const bl = xy(corners[3]);
  if (!tl || !tr || !bl) return null;
  const e1 = [tr[0] - tl[0], tr[1] - tl[1]]; // along the top edge: u
  const e2 = [bl[0] - tl[0], bl[1] - tl[1]]; // down the left edge: v
  const det = e1[0] * e2[1] - e1[1] * e2[0];
  if (!(Math.abs(det) > 1e-9 * Math.hypot(e1[0], e1[1]) * Math.hypot(e2[0], e2[1]))) return null;
  // p − tl = u·e1 + v·e2  →  u = (d × e2) / det, v = (e1 × d) / det
  return { o: [tl[0] - origin[0], tl[1] - origin[1]], u: [e2[1] / det, -e2[0] / det], v: [-e1[1] / det, e1[0] / det] };
}

/** Image coordinates of a point given in the coordinates of the vertices, float64. */
export function drapeUv(a: DrapeAxes, x: number, y: number): [number, number] {
  const dx = x - a.o[0];
  const dy = y - a.o[1];
  return [a.u[0] * dx + a.u[1] * dy, a.v[0] * dx + a.v[1] * dy];
}

const f = Math.fround;

/** The same as the vertex shader computes it: float32 uniforms, float32 operations (tests, the E2E probe). */
export function drapeUvFloat32(a: DrapeAxes, x: number, y: number): [number, number] {
  const dx = f(f(x) - f(a.o[0]));
  const dy = f(f(y) - f(a.o[1]));
  return [f(f(f(a.u[0]) * dx) + f(f(a.u[1]) * dy)), f(f(f(a.v[0]) * dx) + f(f(a.v[1]) * dy))];
}

/**
 * The part of an image (u0, v0, u1, v1) covered by a box in plan (absolute `min`, `max`), or null when they
 * do not meet: the flat raster is cut out there while it lies on a surface.
 */
export function imageWindow(corners: ArrayLike<Corner> | null | undefined, min: ArrayLike<number>, max: ArrayLike<number>): [number, number, number, number] | null {
  const axes = imageAxes(corners, [0, 0]);
  if (!axes) return null;
  const points = [drapeUv(axes, min[0], min[1]), drapeUv(axes, max[0], min[1]), drapeUv(axes, max[0], max[1]), drapeUv(axes, min[0], max[1])];
  const u = points.map((p) => p[0]);
  const v = points.map((p) => p[1]);
  const window: [number, number, number, number] = [Math.min(...u), Math.min(...v), Math.max(...u), Math.max(...v)];
  return window[0] >= 1 || window[1] >= 1 || window[2] <= 0 || window[3] <= 0 ? null : window;
}

// --- the shader ---------------------------------------------------------------------------------------

/** One program for every draped material (`customProgramCacheKey`). */
export const DRAPE_PROGRAM_KEY = "bf-mesh-drape";

const VERTEX_PARS = "uniform vec2 uDrapeO;\nuniform vec2 uDrapeU;\nuniform vec2 uDrapeV;\nvarying vec2 vDrapeUv;";
const VERTEX_MAIN = "vec2 bfDrape = position.xy - uDrapeO;\n\tvDrapeUv = vec2(dot(uDrapeU, bfDrape), dot(uDrapeV, bfDrape));";
const FRAGMENT_PARS = "uniform sampler2D uDrapeMap;\nuniform vec3 uDrapeTint;\nuniform float uDrapeMix;\nuniform float uDrapeShade;\nvarying vec2 vDrapeUv;";
// the tint multiplies the image, the opacity covers the colour of the surface, outside the image nothing changes
const FRAGMENT_MAP = [
  "vec4 drapeTexel = texture2D(uDrapeMap, vDrapeUv);",
  "vec3 drapeRgb = drapeTexel.rgb * uDrapeTint;",
  "float drapeK = uDrapeMix * drapeTexel.a * step(0.0, vDrapeUv.x) * step(vDrapeUv.x, 1.0) * step(0.0, vDrapeUv.y) * step(vDrapeUv.y, 1.0);",
  "diffuseColor.rgb = mix(diffuseColor.rgb, drapeRgb, drapeK);",
].join("\n\t");
// without the shadows of the relief the image keeps its colours as they are (the selection still glows)
const FRAGMENT_LIGHT = "outgoingLight = mix(outgoingLight, drapeRgb + totalEmissiveRadiance, drapeK * (1.0 - uDrapeShade));";

/**
 * Adds the drape to the Lambert shader of three.js (`onBeforeCompile`). False — the shader has no such places
 * (another version of three.js): it is left as it is and the surface keeps its own colour.
 */
export function patchDrapeShader(shader: { vertexShader: string; fragmentShader: string }): boolean {
  const anchors: [keyof typeof shader, string, string][] = [
    ["vertexShader", "#include <common>", `#include <common>\n${VERTEX_PARS}`],
    ["vertexShader", "#include <begin_vertex>", `#include <begin_vertex>\n\t${VERTEX_MAIN}`],
    ["fragmentShader", "#include <common>", `#include <common>\n${FRAGMENT_PARS}`],
    ["fragmentShader", "#include <map_fragment>", `#include <map_fragment>\n\t${FRAGMENT_MAP}`],
    ["fragmentShader", "#include <opaque_fragment>", `${FRAGMENT_LIGHT}\n\t#include <opaque_fragment>`],
  ];
  if (!anchors.every(([side, at]) => shader[side].includes(at))) return false;
  for (const [side, at, to] of anchors) shader[side] = shader[side].replace(at, to);
  return true;
}
