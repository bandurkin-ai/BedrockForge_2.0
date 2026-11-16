import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./icons";

/**
 * An icon button with the application's own hover tip (name, what it does, the shortcut):
 * the tip appears after a short pause, below the button, and stays inside the window.
 */
export function ToolButton({ icon, label, hint, off, keys, active, disabled, onClick, className, children, ...rest }: {
  icon: IconName;
  label: string;
  hint?: string;
  /** Why the button is disabled (shown in the tip while it is). */
  off?: string;
  keys?: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  className?: string;
  children?: ReactNode;
  "aria-expanded"?: boolean;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number } | null>(null);
  const timer = useRef(0);
  const hide = () => {
    window.clearTimeout(timer.current);
    setTip(null);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const show = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const r = ref.current?.getBoundingClientRect();
      if (r) setTip({ x: r.left + r.width / 2, y: r.bottom + 6 });
    }, 350);
  };
  return (
    // The wrapper hears the pointer: a disabled button itself gets no mouse events.
    <span className="tool-wrap" onMouseEnter={show} onMouseLeave={hide}>
      <button ref={ref} type="button" className={`tool-btn${active ? " active" : ""}${className ? ` ${className}` : ""}`} aria-label={label} aria-pressed={active}
        disabled={disabled} onClick={() => { hide(); onClick?.(); }} onFocus={show} onBlur={hide} {...rest}>
        <Icon name={icon} size={17} />
        {children}
      </button>
      {tip && createPortal(<TipBox x={tip.x} y={tip.y} label={label} hint={hint} keys={keys} off={disabled ? off : undefined} />, document.body)}
    </span>
  );
}

function TipBox({ x, y, label, hint, keys, off }: { x: number; y: number; label: string; hint?: string; keys?: string; off?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [left, setLeft] = useState(x);
  useEffect(() => {
    const w = ref.current?.offsetWidth ?? 0;
    setLeft(Math.min(Math.max(8 + w / 2, x), window.innerWidth - 8 - w / 2));
  }, [x]);
  return (
    <div ref={ref} className="tool-tip" role="tooltip" style={{ left, top: y }}>
      <div className="tool-tip-head"><strong>{label}</strong>{keys && <kbd>{keys}</kbd>}</div>
      {hint && <div className="tool-tip-hint">{hint}</div>}
      {off && <div className="tool-tip-off">{off}</div>}
    </div>
  );
}
