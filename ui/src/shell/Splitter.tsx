import type { PointerEvent as RPointerEvent } from "react";
import css from "./Shell.module.css";

/** Drag handle between docks; reports the new size, the caller clamps it (ТЗ 7.0.3). */
export function Splitter({ axis, size, invert, onResize, label }: { axis: "x" | "y"; size: number; invert?: boolean; onResize: (px: number) => void; label: string }) {
  const start = (e: RPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const origin = axis === "x" ? e.clientX : e.clientY;
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const delta = (axis === "x" ? ev.clientX : ev.clientY) - origin;
      onResize(size + (invert ? -delta : delta));
    };
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };
  return <div role="separator" aria-label={label} aria-orientation={axis === "x" ? "vertical" : "horizontal"} className={axis === "x" ? css.splitX : css.splitY} onPointerDown={start} />;
}
