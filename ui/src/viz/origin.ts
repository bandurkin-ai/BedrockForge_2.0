/**
 * float64 world coordinates -> float32 GPU buffer relative to an integer origin near the
 * bbox centre (ТЗ 5.4, 6.8). Pure module, tested in node.
 */

export interface Origin {
  x: number;
  y: number;
  z: number;
}

/** Integer origin at the centre of the bounding box of xyz (interleaved float64). */
export function integerOrigin(xyz: Float64Array): Origin {
  if (xyz.length === 0) return { x: 0, y: 0, z: 0 };
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < xyz.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const v = xyz[i + k];
      if (v < lo[k]) lo[k] = v;
      if (v > hi[k]) hi[k] = v;
    }
  }
  return {
    x: Math.round((lo[0] + hi[0]) / 2),
    y: Math.round((lo[1] + hi[1]) / 2),
    z: Math.round((lo[2] + hi[2]) / 2),
  };
}

/** Relative float32 copy for display only; measurements stay on float64 in the core. */
export function toRelativeFloat32(xyz: Float64Array, o: Origin): Float32Array {
  const out = new Float32Array(xyz.length);
  for (let i = 0; i < xyz.length; i += 3) {
    out[i] = xyz[i] - o.x;
    out[i + 1] = xyz[i + 1] - o.y;
    out[i + 2] = xyz[i + 2] - o.z;
  }
  return out;
}

/** Largest absolute error (metres) of the float32 copy against the float64 source. */
export function maxRoundTripError(xyz: Float64Array, rel: Float32Array, o: Origin): number {
  let worst = 0;
  const oo = [o.x, o.y, o.z];
  for (let i = 0; i < xyz.length; i++) {
    const err = Math.abs(rel[i] + oo[i % 3] - xyz[i]);
    if (err > worst) worst = err;
  }
  return worst;
}
