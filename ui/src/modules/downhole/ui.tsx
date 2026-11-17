import { useEffect, useRef, useState, type ReactNode } from "react";
import { bridge } from "../../bridge";

/** Small controls shared by the sections of the downhole display form. */

/** "0,3" and "0.3" are both read; shown with a comma. */
export const parseNum = (text: string): number | null => {
  const t = text.trim().replace(",", ".");
  const n = Number(t);
  return t === "" || !Number.isFinite(n) ? null : n;
};
export const showNum = (v: number | null | undefined, digits = 6): string =>
  v === null || v === undefined || !Number.isFinite(v) ? "" : String(Number(v.toPrecision(digits))).replace(".", ",");

const OPEN_KEY = "bf.downhole.sections";
function readOpen(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY) ?? "{}") as Record<string, boolean>;
  } catch {
    return {};
  }
}

/** A collapsible part of the form; closed, its header says the setting in one line. Remembers its state. */
export function Section({ id, title, summary, open: byDefault = false, children }: { id: string; title: string; summary?: string; open?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(() => readOpen()[id] ?? byDefault);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify({ ...readOpen(), [id]: next }));
    } catch {
      /* the state is just not remembered */
    }
  };
  return (
    <section className={`dh-section${open ? " open" : ""}`}>
      <button type="button" className="dh-section-head" aria-expanded={open} onClick={toggle} title={open ? "Свернуть раздел" : "Развернуть раздел"}>
        <span className="dh-twisty" aria-hidden>{open ? "▾" : "▸"}</span>
        <strong>{title}</strong>
        {summary && <span className="dh-summary muted">{summary}</span>}
      </button>
      {open && <div className="dh-section-body">{children}</div>}
    </section>
  );
}

export interface SegOption<T extends string> { value: T; label: string; icon?: ReactNode; title: string }

/** A choice of a few options as buttons side by side. */
export function Segmented<T extends string>({ value, options, onChange, label, disabled }: {
  value: T | undefined; options: SegOption<T>[]; onChange: (v: T) => void; label: string; disabled?: boolean;
}) {
  return (
    <div className="dh-seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} className={value === o.value ? "active" : ""} title={o.title} disabled={disabled} onClick={() => onChange(o.value)}>
          {o.icon && <span className="dh-seg-icon" aria-hidden>{o.icon}</span>}
          <span>{o.label}</span>
        </button>
      ))}
    </div>
  );
}

/** A number field that takes a comma or a point; empty gives null. Commits on blur and Enter, and as typed when valid. */
export function NumField({ value, onChange, label, title, placeholder, unit, min, max, disabled, className }: {
  value: number | null | undefined; onChange: (v: number | null) => void; label: string; title?: string; placeholder?: string; unit?: string;
  min?: number; max?: number; disabled?: boolean; className?: string;
}) {
  const [text, setText] = useState(showNum(value));
  const typing = useRef(false);
  useEffect(() => {
    if (!typing.current) setText(showNum(value));
  }, [value]);
  const clamp = (v: number | null) => (v === null ? null : Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v)));
  return (
    <span className={`dh-num${className ? ` ${className}` : ""}`}>
      <input
        value={text} inputMode="decimal" aria-label={label} title={title ?? label} placeholder={placeholder} disabled={disabled}
        onFocus={() => (typing.current = true)}
        onChange={(e) => {
          setText(e.target.value);
          const v = parseNum(e.target.value);
          if (v !== null || e.target.value.trim() === "") onChange(clamp(v));
        }}
        onBlur={() => {
          typing.current = false;
          setText(showNum(clamp(parseNum(text))));
        }}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      />
      {unit && <span className="dh-unit muted">{unit}</span>}
    </span>
  );
}

/** A slider with a number field and a unit. */
export function SliderField({ value, onChange, label, title, min, max, step, unit, disabled }: {
  value: number; onChange: (v: number) => void; label: string; title: string; min: number; max: number; step: number; unit: string; disabled?: boolean;
}) {
  return (
    <label className="dh-slider" title={title}>
      <span className="dh-label">{label}</span>
      <input type="range" min={min} max={max} step={step} value={Math.min(max, Math.max(min, value))} aria-label={label} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
      <NumField value={value} label={`${label}, ${unit}`} unit={unit} min={min} disabled={disabled} onChange={(v) => v !== null && onChange(v)} />
    </label>
  );
}

/** A row of the form: a caption and a control. */
export function Row({ label, title, children }: { label: string; title?: string; children: ReactNode }) {
  return (
    <div className="dh-row" title={title}>
      <span className="dh-label">{label}</span>
      <div className="dh-control">{children}</div>
    </div>
  );
}

/** A button that opens a small menu or panel under itself; closes on a click outside and on Escape. */
export function Popover({ label, title, button, children, className, align = "left" }: {
  label: string; title: string; button: ReactNode; children: (close: () => void) => ReactNode; className?: string; align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  return (
    <span className="dh-pop" ref={ref}>
      <button type="button" className={className} aria-label={label} title={title} aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{button}</button>
      {open && <div className={`dh-pop-panel ${align}`} role="menu">{children(() => setOpen(false))}</div>}
    </span>
  );
}

export function MenuItem({ children, onClick, disabled, title, danger }: { children: ReactNode; onClick: () => void; disabled?: boolean; title?: string; danger?: boolean }) {
  return <button type="button" role="menuitem" className={`dh-menu-item${danger ? " danger" : ""}`} disabled={disabled} title={title} onClick={onClick}>{children}</button>;
}

const RECENT_KEY = "bf.downhole.colors";
export function recentColors(): string[] {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as unknown;
    return Array.isArray(list) ? list.filter((c): c is string => typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c)).slice(0, 10) : [];
  } catch {
    return [];
  }
}
export function rememberColor(color: string): void {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([color, ...recentColors().filter((c) => c !== color)].slice(0, 10)));
  } catch {
    /* not remembered */
  }
}

/** A colour: the system picker and swatches of recent colours and colours used in the display. */
export function ColorField({ value, onChange, label, extra = [], disabled }: { value: string; onChange: (c: string) => void; label: string; extra?: string[]; disabled?: boolean }) {
  const swatches = [...new Set([...extra, ...recentColors()])].slice(0, 12);
  return (
    <span className="dh-color">
      <input type="color" value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#9aa3b5"} aria-label={label} title={label} disabled={disabled}
        onChange={(e) => onChange(e.target.value)} onBlur={(e) => rememberColor(e.target.value)} />
      {swatches.map((c) => (
        <button key={c} type="button" className="dh-swatch" style={{ background: c }} title={`Цвет ${c}`} aria-label={`Цвет ${c}`} disabled={disabled} onClick={() => onChange(c)} />
      ))}
    </span>
  );
}

/** «Импорт из CSV»: the open dialog of the app; the text, or null when cancelled (throws with the reason). */
export async function openCsvText(): Promise<string | null> {
  const b = bridge();
  if (!b) throw new Error("Открытие файлов доступно только в приложении");
  const f = await b.openText("csv");
  if (!f) return null;
  if (f.text === null) throw new Error(f.error ?? "Файл не читается");
  return f.text;
}

/** «Экспорт в CSV»: the save dialog of the app (the shell adds the BOM); the path or null. */
export async function saveCsv(name: string, text: string): Promise<string | null> {
  const b = bridge();
  if (!b) throw new Error("Сохранение файлов доступно только в приложении");
  return b.saveFile(name.replace(/[\\/:*?"<>|]+/g, "_"), "csv", text);
}
