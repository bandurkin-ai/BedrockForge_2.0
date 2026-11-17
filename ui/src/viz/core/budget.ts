/**
 * Budgets of the 3D window (ADR 0004 §3): the class of graphics from the renderer string, triangles per
 * frame (coarser levels for the smallest parts on screen, «упрощено»), and the video memory account
 * with eviction by age — first parts out of view, then the finer levels; the coarsest level stays.
 */

export type GpuClass = "integrated" | "discrete" | "software";

export const TRIANGLE_BUDGET: Readonly<Record<GpuClass, number>> = { integrated: 8_000_000, discrete: 30_000_000, software: 1_000_000 };

const GB = 1024 ** 3;
/** Software rendering keeps buffers in RAM: the budget of integrated graphics. */
export const MEMORY_BUDGET: Readonly<Record<GpuClass, number>> = { integrated: 1.5 * GB, discrete: 4 * GB, software: 1.5 * GB };

/**
 * Class of graphics by `WEBGL_debug_renderer_info` (UNMASKED_RENDERER_WEBGL): SwiftShader / WARP /
 * llvmpipe → software; NVIDIA, Radeon RX / Pro, Arc A·B → discrete; Intel, Iris, UHD, Vega, Radeon
 * Graphics → integrated. Unknown → integrated, the class of the acceptance PC (the smallest budgets
 * of real graphics). "Intel Arc Graphics" without a model number is the integrated graphics of Core Ultra.
 */
export function gpuClass(renderer: string | null | undefined): GpuClass {
  const s = (renderer ?? "").toLowerCase();
  if (/swiftshader|llvmpipe|softpipe|\bwarp\b|basic render driver|software rasterizer/.test(s)) return "software";
  if (/nvidia|geforce|quadro|\brtx\b|\bgtx\b|tesla/.test(s)) return "discrete";
  if (/radeon(\(tm\))?\s*(rx|pro)\b/.test(s)) return "discrete";
  if (/\barc(\(tm\))?\s*(a|b)\d{3}/.test(s)) return "discrete";
  return "integrated";
}

export interface BudgetPart {
  key: string;
  /** size on screen (px), the smallest are coarsened first */
  screen: number;
  /** triangles of the levels that can be shown, finest first */
  triangles: readonly number[];
  /** the level chosen by LOD */
  level: number;
}

export interface BudgetResult {
  /** levels after the budget, in the order of the input */
  levels: number[];
  triangles: number;
  /** a level was coarsened by the budget: «упрощено» in the status bar */
  simplified: boolean;
  /** still above the budget with every part at its coarsest */
  over: boolean;
}

/** Keeps the triangles of a frame within `budget`: the smallest parts on screen go coarser first. */
export function applyTriangleBudget(parts: readonly BudgetPart[], budget: number): BudgetResult {
  const levels = parts.map((p) => Math.max(0, Math.min(p.level, p.triangles.length - 1)));
  let total = 0;
  parts.forEach((p, i) => (total += p.triangles[levels[i]] ?? 0));
  let simplified = false;
  if (total > budget) {
    const order = parts.map((_, i) => i).sort((a, b) => parts[a].screen - parts[b].screen || a - b);
    for (const i of order) {
      const tri = parts[i].triangles;
      while (total > budget && levels[i] < tri.length - 1) {
        total -= tri[levels[i]] - tri[levels[i] + 1];
        levels[i] += 1;
        simplified = true;
      }
      if (total <= budget) break;
    }
  }
  return { levels, triangles: total, simplified, over: total > budget };
}

/** After three lost contexts within a minute the budgets are halved (ADR 0004 §4). */
export function lossFactor(lossTimes: readonly number[], now: number): 1 | 0.5 {
  return lossTimes.filter((t) => now - t <= 60_000 && t <= now).length >= 3 ? 0.5 : 1;
}

export interface Resident {
  part: string;
  level: number;
  bytes: number;
  /** the coarsest level of the part: never evicted */
  coarsest: boolean;
  lastUsed: number;
}

/**
 * The account of buffers and textures on the video card. The scene registers what it uploads, marks
 * what is drawn (`touch`) and which parts are in view; before an upload it asks for room and disposes
 * what `makeRoom` gives back.
 */
export class GpuMemory {
  private readonly items = new Map<string, Resident>();
  private visible = new Set<string>();
  private usedBytes = 0;

  constructor(public limit: number) {}

  get used(): number {
    return this.usedBytes;
  }

  get count(): number {
    return this.items.size;
  }

  private static id(part: string, level: number): string {
    return `${part}\u0000${level}`;
  }

  has(part: string, level: number): boolean {
    return this.items.has(GpuMemory.id(part, level));
  }

  add(part: string, level: number, bytes: number, coarsest: boolean, now: number): void {
    this.remove(part, level);
    this.items.set(GpuMemory.id(part, level), { part, level, bytes, coarsest, lastUsed: now });
    this.usedBytes += bytes;
  }

  remove(part: string, level: number): void {
    const id = GpuMemory.id(part, level);
    const r = this.items.get(id);
    if (!r) return;
    this.usedBytes -= r.bytes;
    this.items.delete(id);
  }

  /** Every level of a part (the object was removed from the scene). */
  removePart(part: string): Resident[] {
    const gone = [...this.items.values()].filter((r) => r.part === part);
    for (const r of gone) this.remove(r.part, r.level);
    return gone;
  }

  touch(part: string, level: number, now: number): void {
    const r = this.items.get(GpuMemory.id(part, level));
    if (r) r.lastUsed = now;
  }

  /** The parts in view this frame (after culling). */
  setVisible(parts: Iterable<string>): void {
    this.visible = new Set(parts);
  }

  /**
   * Frees room for `need` more bytes: first parts out of view (oldest first), then the finer levels of
   * parts in view (finest, then oldest, first); the coarsest level is never evicted, nor what was used
   * at or after `keepSince` (drawn this frame). The evicted entries leave the account; the caller
   * disposes them. When the room cannot be found nothing is evicted and `ok` is false — except for
   * `need` 0 (trimming to a lowered limit), which evicts what it can.
   */
  makeRoom(need: number, keepSince = Infinity): { ok: boolean; evicted: Resident[] } {
    const excess = this.usedBytes + need - this.limit;
    if (excess <= 0) return { ok: true, evicted: [] };
    const candidates = [...this.items.values()].filter((r) => !r.coarsest && r.lastUsed < keepSince);
    const out = (r: Resident) => !this.visible.has(r.part);
    candidates.sort((a, b) => {
      const oa = out(a) ? 0 : 1;
      const ob = out(b) ? 0 : 1;
      if (oa !== ob) return oa - ob;
      if (oa === 1 && a.level !== b.level) return a.level - b.level;
      return a.lastUsed - b.lastUsed;
    });
    const evicted: Resident[] = [];
    let freed = 0;
    for (const r of candidates) {
      if (freed >= excess) break;
      evicted.push(r);
      freed += r.bytes;
    }
    const ok = freed >= excess;
    if (!ok && need > 0) return { ok: false, evicted: [] };
    for (const r of evicted) this.remove(r.part, r.level);
    return { ok, evicted };
  }
}
