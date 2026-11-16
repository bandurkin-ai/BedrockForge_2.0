/** Helpers of the editor tests: view matrices in the layout of three.js (column-major). */

/** Orthographic view of the box: x left → right, y bottom → top, z0 → near, z1 → far. */
export function ortho(x0: number, x1: number, y0: number, y1: number, z0: number, z1: number): number[] {
  return [
    2 / (x1 - x0), 0, 0, 0,
    0, 2 / (y1 - y0), 0, 0,
    0, 0, 2 / (z1 - z0), 0,
    -(x1 + x0) / (x1 - x0), -(y1 + y0) / (y1 - y0), -(z1 + z0) / (z1 - z0), 1,
  ];
}

/** Perspective camera at the origin looking along −Z, Y up. */
export function perspective(fovDeg: number, aspect: number, near: number, far: number): number[] {
  const f = 1 / Math.tan((fovDeg * Math.PI) / 360);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0];
}

/** A repeatable pseudo-random generator in [0, 1). */
export function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
