/**
 * «Показать загруженное» (ТЗ 7.13, 7.8): after an import the view moves to what was loaded only when it is not
 * seen — off the screen, or smaller than 1 % of it (a file far from the project: 466 000 / 4 584 000 against
 * a demo at 5 000 / 8 000). It is fitted to the objects not seen, keeping the direction of the view, as
 * «Вписать» does. Pure, no three.js.
 */
import { scaleZ, union, type Box3 } from "./bounds";
import { fit, project, type CameraState, type ViewOptions } from "./views";

/** Below this share of the larger side of the screen an object is not seen. */
export const SEEN_SHARE = 0.01;

/**
 * The share (0…1) of the larger side of the screen that `box` (rendered space: Z stretched) covers, clipped to
 * the screen; 0 — off the screen. A perspective camera inside the box, or with the box behind it in part,
 * sees it (1); a box wholly behind it is not seen.
 */
export function screenShare(cam: CameraState, opts: ViewOptions, box: Box3): number {
  let x0 = Infinity;
  let x1 = -Infinity;
  let y0 = Infinity;
  let y1 = -Infinity;
  let behind = 0;
  for (let i = 0; i < 8; i++) {
    const p = project(cam, opts, [i & 1 ? box.max[0] : box.min[0], i & 2 ? box.max[1] : box.min[1], i & 4 ? box.max[2] : box.min[2]]);
    if (!p) {
      behind++;
      continue;
    }
    x0 = Math.min(x0, p.x);
    x1 = Math.max(x1, p.x);
    y0 = Math.min(y0, p.y);
    y1 = Math.max(y1, p.y);
  }
  if (behind === 8) return 0;
  if (behind > 0) return 1;
  const w = Math.min(x1, 1) - Math.max(x0, -1);
  const h = Math.min(y1, 1) - Math.max(y0, -1);
  if (!(w >= 0 && h >= 0)) return 0;
  const aspect = opts.aspect > 0 ? opts.aspect : 1;
  // NDC spans 2 on both axes: the larger side of the screen in pixels is the wider one
  return aspect >= 1 ? Math.max(w, h / aspect) / 2 : Math.max(w * aspect, h) / 2;
}

/**
 * The camera that shows the boxes (scene coordinates, Z not stretched) not seen by `cam`, looking the same
 * way; null — every box is seen, the view stays.
 */
export function revealCamera(cam: CameraState, opts: ViewOptions, boxes: Box3[], minShare = SEEN_SHARE): CameraState | null {
  const unseen = boxes.filter((b) => screenShare(cam, opts, scaleZ(b, opts.vexag)) < minShare);
  if (!unseen.length) return null;
  return fit(cam, unseen.reduce((a, b) => union(a, b)), opts);
}
