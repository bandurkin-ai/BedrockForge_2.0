import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface View {
  /** world point in the centre of the viewport */
  cx: number;
  cy: number;
  /** pixels per world unit along x */
  scale: number;
}

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/**
 * Pan and zoom for a 2D SVG view. World y points up; `vexag` stretches y only
 * (vertical exaggeration of a section). Zoom is around the cursor.
 */
export function useViewport(vexag = 1) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setViewState] = useState<View>({ cx: 0, cy: 0, scale: 1 });
  const viewRef = useRef(view);
  viewRef.current = view;
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const vexagRef = useRef(vexag);
  vexagRef.current = vexag;
  const dragged = useRef(false);

  const setView = useCallback((next: View) => {
    viewRef.current = next;
    setViewState(next);
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setSize({ w: Math.max(el.clientWidth, 1), h: Math.max(el.clientHeight, 1) });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const toScreen = useCallback(
    (x: number, y: number): [number, number] => [
      size.w / 2 + (x - view.cx) * view.scale,
      size.h / 2 - (y - view.cy) * view.scale * vexag,
    ],
    [size, view, vexag],
  );
  const toWorld = useCallback(
    (px: number, py: number): [number, number] => [
      view.cx + (px - size.w / 2) / view.scale,
      view.cy - (py - size.h / 2) / (view.scale * vexag),
    ],
    [size, view, vexag],
  );

  const fit = useCallback(
    (b: Bounds, padding = 0.08) => {
      const { w, h } = sizeRef.current;
      const spanX = Math.max(b.maxX - b.minX, 1e-6);
      const spanY = Math.max(b.maxY - b.minY, 1e-6);
      const scale = Math.min(w / (spanX * (1 + 2 * padding)), h / (spanY * vexagRef.current * (1 + 2 * padding)));
      setView({ cx: (b.minX + b.maxX) / 2, cy: (b.minY + b.maxY) / 2, scale });
    },
    [setView],
  );

  // Wheel zoom must be a non-passive native listener to stop the page from scrolling.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const v = viewRef.current;
      const { w, h } = sizeRef.current;
      const ve = vexagRef.current;
      const factor = Math.exp(-e.deltaY * 0.0015);
      const scale = Math.min(Math.max(v.scale * factor, 1e-4), 1e5);
      // keep the world point under the cursor fixed
      const wx = v.cx + (px - w / 2) / v.scale;
      const wy = v.cy - (py - h / 2) / (v.scale * ve);
      setView({ scale, cx: wx - (px - w / 2) / scale, cy: wy + (py - h / 2) / (scale * ve) });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [setView]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      dragged.current = false;
      let lastX = e.clientX;
      let lastY = e.clientY;
      const startX = e.clientX;
      const startY = e.clientY;
      const move = (m: MouseEvent) => {
        if (Math.hypot(m.clientX - startX, m.clientY - startY) > 3) dragged.current = true;
        if (!dragged.current) return;
        const v = viewRef.current;
        setView({
          scale: v.scale,
          cx: v.cx - (m.clientX - lastX) / v.scale,
          cy: v.cy + (m.clientY - lastY) / (v.scale * vexagRef.current),
        });
        lastX = m.clientX;
        lastY = m.clientY;
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    },
    [setView],
  );

  return { ref, size, view, setView, toScreen, toWorld, fit, onMouseDown, dragged };
}
