import { useRef } from "react";

/** Drag handle between a dock and the viewport. `sign` is +1 when dragging right/down grows the dock. */
export function Splitter({
  axis,
  size,
  sign,
  label,
  onResize,
}: {
  axis: "x" | "y";
  size: number;
  sign: 1 | -1;
  label: string;
  onResize: (size: number) => void;
}) {
  const start = useRef<{ pointer: number; size: number } | null>(null);
  const position = (e: React.PointerEvent) => (axis === "x" ? e.clientX : e.clientY);

  return (
    <div
      className={`splitter splitter-${axis}`}
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={label}
      tabIndex={0}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { pointer: position(e), size };
      }}
      onPointerMove={(e) => {
        if (start.current) onResize(start.current.size + sign * (position(e) - start.current.pointer));
      }}
      onPointerUp={() => (start.current = null)}
      onPointerCancel={() => (start.current = null)}
      onKeyDown={(e) => {
        const grow = axis === "x" ? "ArrowRight" : "ArrowDown";
        const shrink = axis === "x" ? "ArrowLeft" : "ArrowUp";
        if (e.key === grow) onResize(size + sign * 16);
        else if (e.key === shrink) onResize(size - sign * 16);
      }}
    />
  );
}
