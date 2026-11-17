/**
 * Hiding of overlapping labels (ADR 0007 §5; BF `scene3d.ts` 947–979): labels are placed in order of
 * priority — crossings, then the depth at the end of a hole, then hole names, then tick depths — on a
 * grid of screen cells; a label that would cover a cell already taken is hidden, so a wide view does not
 * turn into a heap of text. Pure: screen positions in, a visibility mask out.
 */

export const LABEL_PRIORITY = { crossing: 3, depth: 2, name: 1, tick: 0 } as const;
export type LabelKind = keyof typeof LABEL_PRIORITY;

export const CELL_W = 24;
export const CELL_H = 12;

export interface LabelBox {
  /** left edge (anchor plus the shift to the right of the marker), CSS px; NaN — not on screen */
  x: number;
  /** vertical centre, CSS px */
  y: number;
  w: number;
  h: number;
  priority: number;
}

export interface DeclutterOptions {
  width: number;
  height: number;
  cellW?: number;
  cellH?: number;
}

/**
 * Visibility of each label (1 — shown), in the order of the input. Higher priority first, equal
 * priority in input order (the caller puts nearer ones first). Labels entirely off the screen are hidden
 * and take no room.
 */
export function declutter(labels: readonly LabelBox[], opts: DeclutterOptions): Uint8Array {
  const cw = opts.cellW ?? CELL_W;
  const ch = opts.cellH ?? CELL_H;
  const cols = Math.max(1, Math.ceil(opts.width / cw));
  const rows = Math.max(1, Math.ceil(opts.height / ch));
  const taken = new Uint8Array(cols * rows);
  const shown = new Uint8Array(labels.length);
  const order = labels.map((_, i) => i).sort((a, b) => labels[b].priority - labels[a].priority || a - b);
  for (const i of order) {
    const l = labels[i];
    if (!(Number.isFinite(l.x) && Number.isFinite(l.y) && l.w >= 0 && l.h >= 0)) continue;
    const top = l.y - l.h / 2;
    if (l.x + l.w < 0 || l.x > opts.width || top + l.h < 0 || top > opts.height) continue;
    // Cells the box covers; an edge exactly on a cell border does not take the next cell.
    const x0 = Math.min(cols - 1, Math.max(0, Math.floor(l.x / cw)));
    const x1 = Math.min(cols - 1, Math.max(x0, Math.ceil((l.x + l.w) / cw) - 1));
    const y0 = Math.min(rows - 1, Math.max(0, Math.floor(top / ch)));
    const y1 = Math.min(rows - 1, Math.max(y0, Math.ceil((top + l.h) / ch) - 1));
    let free = true;
    for (let r = y0; r <= y1 && free; r++) for (let c = x0; c <= x1; c++) if (taken[r * cols + c]) { free = false; break; }
    if (!free) continue;
    for (let r = y0; r <= y1; r++) taken.fill(1, r * cols + x0, r * cols + x1 + 1);
    shown[i] = 1;
  }
  return shown;
}
