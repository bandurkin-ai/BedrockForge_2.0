/**
 * The first view of the 3D window (ТЗ 7.13, 7.5). The layers of an opened project arrive one by one, each from
 * its own request — holes, lines, images, surfaces — so the content is fitted when the first comes and again
 * whenever there is more of it, until the user, a command or a saved view takes the camera (`stop`). From then
 * on new objects never move the view. Without this the demo opened framed on whichever layer came first.
 */
import type { Box3 } from "./bounds";

/** Growth below this share of the diagonal of the fitted content is not «more»: a finer level of detail, a
 * raster that drops to the floor when its DTM is hidden. */
const SHARE = 0.02;

export class FirstView {
  private following = true;
  private box: Box3 | null = null;

  /** The camera belongs to the user from now on. Before the first content there is nothing to keep: it is fitted. */
  stop(): void {
    if (this.box) this.following = false;
  }

  /** True when the view is to be fitted now: `box` is the first content, or reaches beyond the last fitted one. */
  grown(box: Box3 | null): boolean {
    if (!this.following || !box) return false;
    const last = this.box;
    const eps = last ? SHARE * Math.hypot(last.max[0] - last.min[0], last.max[1] - last.min[1], last.max[2] - last.min[2]) : 0;
    const more = !last || [0, 1, 2].some((k) => box.min[k] < last.min[k] - eps || box.max[k] > last.max[k] + eps);
    if (more) this.box = { min: [box.min[0], box.min[1], box.min[2]], max: [box.max[0], box.max[1], box.max[2]] };
    return more;
  }
}
