/**
 * The mouse over the 3D view (BF `attachPointerEvents` 2652–2729): a click is a press and release of the
 * left button that moved less than 4 px within 600 ms — otherwise it was a drag of the view. Moves feed the
 * rubber band of a section and the hover; coordinates are CSS px from the top left of the canvas.
 */

export const CLICK_MOVE_PX = 4;
export const CLICK_MS = 600;

export interface PointerHandlers {
  click(x: number, y: number, e: PointerEvent): void;
  move(x: number, y: number, e: PointerEvent): void;
  leave(): void;
}

export function attachPointer(el: HTMLElement, h: PointerHandlers): () => void {
  let down: { x: number; y: number; t: number } | null = null;
  const local = (e: PointerEvent): [number, number] => {
    const r = el.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const onDown = (e: PointerEvent) => {
    down = e.button === 0 ? { x: e.clientX, y: e.clientY, t: performance.now() } : null;
  };
  const onUp = (e: PointerEvent) => {
    if (!down || e.button !== 0) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    const quick = performance.now() - down.t < CLICK_MS;
    down = null;
    if (moved >= CLICK_MOVE_PX || !quick) return;
    const [x, y] = local(e);
    h.click(x, y, e);
  };
  const onMove = (e: PointerEvent) => {
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) >= CLICK_MOVE_PX) return;
    const [x, y] = local(e);
    h.move(x, y, e);
  };
  const onLeave = () => h.leave();
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerleave", onLeave);
  return () => {
    el.removeEventListener("pointerdown", onDown);
    el.removeEventListener("pointerup", onUp);
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("pointerleave", onLeave);
  };
}
