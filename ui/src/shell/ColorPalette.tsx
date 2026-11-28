/**
 * The quick choice of a colour (Э27, 27.6, Д-43 А4): ten standard colours and five plates of the colours the user chose
 * last — the same in all windows. A click picks the colour; the system picker stays for any other. The values and the
 * history are in `quickColors.ts`.
 */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { colorTitle, normalizeHex, recentColors, rememberColor, RECENT_COLORS_MAX, standardOf, STANDARD_COLORS, subscribeRecent } from "./quickColors";
import css from "./ColorPalette.module.css";

/** The recent colours of the user (the newest first); the component is drawn again when any window adds one. */
export function useRecentColors(): readonly string[] {
  return useSyncExternalStore(subscribeRecent, recentColors, recentColors);
}

/** `extra` — colours already used in the display of the window (the downhole styles): a row of its own. */
export function ColorPalette({ value, disabled, extra, onPick }: { value?: string; disabled?: boolean; extra?: readonly string[]; onPick: (hex: string) => void }) {
  const recent = useRecentColors();
  const current = normalizeHex(value);
  const pick = (hex: string, own: boolean) => {
    if (own) rememberColor(hex); // a colour of the user goes to the front again
    onPick(hex);
  };
  const used = (extra ?? []).map(normalizeHex).filter((c): c is string => c !== null && !standardOf(c));
  return (
    <div className={css.palette} role="group" aria-label="Быстрый выбор цвета">
      <span className={css.row} data-testid="color-standard">
        {STANDARD_COLORS.map((c) => (
          <button
            key={c.id}
            type="button"
            className={css.swatch}
            style={{ background: c.hex }}
            title={colorTitle(c.hex)}
            aria-label={colorTitle(c.hex)}
            aria-pressed={current === c.hex.toLowerCase()}
            disabled={disabled}
            onClick={() => pick(c.hex, false)}
          />
        ))}
      </span>
      <span className={css.row} data-testid="color-recent" aria-label="Последние выбранные цвета">
        {Array.from({ length: RECENT_COLORS_MAX }, (_, i) => {
          const hex = recent[i];
          return hex ? (
            <button
              key={`${i}-${hex}`}
              type="button"
              className={css.swatch}
              style={{ background: hex }}
              title={colorTitle(hex)}
              aria-label={colorTitle(hex)}
              aria-pressed={current === hex}
              disabled={disabled}
              onClick={() => pick(hex, true)}
            />
          ) : (
            <span key={`empty-${i}`} className={`${css.swatch} ${css.empty}`} title="Здесь появятся последние выбранные цвета" aria-hidden="true" />
          );
        })}
      </span>
      {used.length > 0 && (
        <span className={css.row} data-testid="color-used" aria-label="Цвета, уже использованные в показе">
          {[...new Set(used)].slice(0, 12).map((hex) => (
            <button key={hex} type="button" className={css.swatch} style={{ background: hex }} title={`${colorTitle(hex)} (уже в показе)`} aria-label={colorTitle(hex)} aria-pressed={current === hex} disabled={disabled} onClick={() => pick(hex, false)} />
          ))}
        </span>
      )}
    </div>
  );
}

/** A colour input of the system with the palette under a button: the input is the one the caller draws (its value and
 * its way of saving stay with the caller). The palette is a small panel over the window (fixed, so that no list or
 * dock clips it); it closes when a colour is picked, on Escape, on a click outside and when the page is scrolled. */
export function ColorChooser({ input, value, disabled, extra, onPick }: { input: ReactNode; value?: string; disabled?: boolean; extra?: readonly string[]; onPick: (hex: string) => void }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!open) return setAt(null);
    const w = wrap.current?.getBoundingClientRect();
    const p = pop.current?.getBoundingClientRect();
    if (!w || !p) return;
    const left = Math.max(4, Math.min(w.left, window.innerWidth - p.width - 4));
    const below = w.bottom + 4;
    setAt({ left, top: below + p.height > window.innerHeight - 4 ? Math.max(4, w.top - p.height - 4) : below });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const inside = (t: EventTarget | null) => t instanceof Node && (wrap.current?.contains(t) || pop.current?.contains(t));
    const down = (e: MouseEvent) => !inside(e.target) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const scroll = (e: Event) => !inside(e.target) && setOpen(false);
    const close = () => setOpen(false);
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    document.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
      document.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  return (
    <span className={css.field} ref={wrap}>
      {input}
      <button
        type="button"
        className={css.toggle}
        title="Палитра: стандартные и последние цвета"
        aria-label="Палитра цветов"
        aria-haspopup="true"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▴" : "▾"}
      </button>
      {open && (
        <div ref={pop} className={css.pop} style={{ left: at?.left ?? 0, top: at?.top ?? 0, visibility: at ? "visible" : "hidden" }}>
          <ColorPalette
            value={value}
            extra={extra}
            disabled={disabled}
            onPick={(hex) => {
              setOpen(false);
              onPick(hex);
            }}
          />
        </div>
      )}
    </span>
  );
}
