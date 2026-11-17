/**
 * The plate «О производителе» (ТЗ 7.35 Д-12): anchored under ⓘ in the title bar, our own component (not a
 * Windows window, no links: 17.4.1). Closes on Esc, a click elsewhere and a second click on ⓘ.
 */
import { useEffect, useRef } from "react";
import { bridge } from "../bridge";
import { useApp } from "./AppContext";
import { ABOUT, aboutLines, aboutStore, aboutText, toggleAbout } from "./about";
import { useStore } from "./useStore";
import logoMark from "../assets/logo-mark.png";
import css from "./Header.module.css";

export function About() {
  const app = useApp();
  const open = useStore(aboutStore, (s) => s.open);
  const coreVersion = useStore(app.store, (s) => s.coreVersion);
  const ref = useRef<HTMLDivElement>(null);
  const appVersion = bridge()?.appVersion() ?? null;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        toggleAbout(false);
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!ref.current?.contains(t) && !t.closest("[data-about-toggle]")) toggleAbout(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onDown, true);
    ref.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onDown, true);
    };
  }, [open]);

  if (!open) return null;
  return (
    <div ref={ref} className={css.about} role="dialog" aria-label="О производителе" tabIndex={-1} data-testid="about">
      <div className={css.aboutBrand}>
        <img src={logoMark} alt="" className={css.aboutMark} />
        <div className={css.wordmark}>
          <b>
            Bedrock<span className={css.forge}>Forge</span>
          </b>
          <span>{ABOUT.byline}</span>
        </div>
      </div>
      <div className={css.aboutSlogan}>{ABOUT.slogan}</div>
      {aboutLines(appVersion, coreVersion).map((line) => (
        <div key={line} className={css.aboutLine}>
          {line}
        </div>
      ))}
      <div className={css.aboutButtons}>
        <button type="button" onClick={() => bridge()?.copyText(aboutText(appVersion, coreVersion))}>
          Скопировать
        </button>
        <button
          type="button"
          onClick={() => {
            toggleAbout(false);
            void app.run("license.open");
          }}
        >
          Лицензия
        </button>
      </div>
    </div>
  );
}
