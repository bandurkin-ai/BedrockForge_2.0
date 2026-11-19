/**
 * The mouse over the 3D view (BF `attachPointerEvents` 2652–2729): DOM pointer events of the canvas as plain
 * records for the input automaton (`toolInput.ts`). The listeners sit in the capture phase, so they run before
 * the camera controls on the same element: an event the automaton takes (an editing tool dragging a vertex or
 * a box) stops there and the view does not move. Coordinates are CSS px from the top left of the canvas.
 */

/** A press and release that moved less than this is a click; more — a drag of the view. */
export const CLICK_MOVE_PX = 4;
/** …and that lasted less than this, ms. */
export const CLICK_MS = 600;

export interface RawPointer {
  type: "down" | "move" | "up" | "dblclick" | "leave";
  /** CSS px inside the element */
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  button: number;
  shift: boolean;
  ctrl: boolean;
  /** ms, `performance.now()` */
  t: number;
}

type Listener = (e: PointerEvent | MouseEvent) => void;

/** Feeds the DOM events of `el` to `sink`; `true` from the sink — the event is taken (the camera does not see it). */
export function attachPointer(el: HTMLElement, sink: (e: RawPointer) => boolean): () => void {
  const read = (type: RawPointer["type"], e: MouseEvent): RawPointer => {
    const r = el.getBoundingClientRect();
    return { type, x: e.clientX - r.left, y: e.clientY - r.top, clientX: e.clientX, clientY: e.clientY, button: e.button, shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey, t: performance.now() };
  };
  const on = (type: RawPointer["type"]): Listener => (e) => {
    if (!sink(read(type, e))) return;
    e.stopImmediatePropagation();
    if (type === "down") {
      e.preventDefault();
      // the drag of a vertex or a box goes on outside the canvas
      if ("pointerId" in e) {
        try {
          el.setPointerCapture(e.pointerId);
        } catch {
          /* the pointer is already gone */
        }
      }
    }
  };
  const listeners: [string, Listener][] = [
    ["pointerdown", on("down")],
    ["pointermove", on("move")],
    ["pointerup", on("up")],
    ["dblclick", on("dblclick")],
    ["pointerleave", on("leave")],
  ];
  for (const [name, fn] of listeners) el.addEventListener(name, fn as EventListener, { capture: true });
  return () => {
    for (const [name, fn] of listeners) el.removeEventListener(name, fn as EventListener, { capture: true });
  };
}
