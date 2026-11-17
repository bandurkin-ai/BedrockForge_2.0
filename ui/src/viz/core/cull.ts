/**
 * Culling of parts by the view frustum (ADR 0004 §1, §3): six planes of the camera against the box of
 * each part, and the order of loading — parts nearer the centre of the screen first. Rendered space
 * (relative to the scene origin, Z stretched).
 */
import { boxCenter, type Box3 } from "./bounds";
import { addScaled, distance, dot, normalize, scale, type Plane, type Vec3 } from "./vec3";
import { cameraBasis, project, type CameraState, type ViewOptions } from "./views";

function through(normal: Vec3, point: Vec3): Plane {
  const n = normalize(normal);
  return { normal: n, constant: -dot(n, point) };
}

/** Planes of the frustum with normals inside: near, far, left, right, bottom, top. */
export function frustumPlanes(cam: CameraState, opts: ViewOptions): Plane[] {
  const { x: right, y: up, z } = cameraBasis(cam);
  const forward = scale(z, -1);
  const p = cam.position;
  const planes = [through(forward, addScaled(p, forward, cam.near)), through(scale(forward, -1), addScaled(p, forward, cam.far))];
  if (opts.perspective) {
    const t = Math.tan(((opts.fov / 2) * Math.PI) / 180);
    const tw = t * opts.aspect;
    planes.push(
      through(addScaled(right, forward, tw), p),
      through(addScaled(scale(right, -1), forward, tw), p),
      through(addScaled(up, forward, t), p),
      through(addScaled(scale(up, -1), forward, t), p),
    );
  } else {
    const hw = cam.half * opts.aspect;
    planes.push(
      through(right, addScaled(p, right, -hw)),
      through(scale(right, -1), addScaled(p, right, hw)),
      through(up, addScaled(p, up, -cam.half)),
      through(scale(up, -1), addScaled(p, up, cam.half)),
    );
  }
  return planes;
}

/** A box is out when it lies entirely behind one plane (its corner farthest along the normal is behind). */
export function boxInFrustum(box: Box3, planes: readonly Plane[]): boolean {
  for (const pl of planes) {
    const n = pl.normal;
    const px = n[0] > 0 ? box.max[0] : box.min[0];
    const py = n[1] > 0 ? box.max[1] : box.min[1];
    const pz = n[2] > 0 ? box.max[2] : box.min[2];
    if (n[0] * px + n[1] * py + n[2] * pz + pl.constant < 0) return false;
  }
  return true;
}

/** Indices of the boxes in the frustum. */
export function cullBoxes(boxes: readonly Box3[], planes: readonly Plane[]): number[] {
  const out: number[] = [];
  boxes.forEach((b, i) => {
    if (boxInFrustum(b, planes)) out.push(i);
  });
  return out;
}

/**
 * Loading priority of a part (smaller — sooner): in the frustum, the distance of its centre from the
 * centre of the screen in normalised units (0 … ~1.4); out of it, 1000 plus the distance from the target.
 */
export function screenPriority(box: Box3, cam: CameraState, opts: ViewOptions, planes: readonly Plane[] = frustumPlanes(cam, opts)): number {
  const c = boxCenter(box);
  if (boxInFrustum(box, planes)) {
    const p = project(cam, opts, c);
    if (p) return Math.min(Math.hypot(p.x, p.y), 999);
  }
  return 1000 + distance(c, cam.target);
}
