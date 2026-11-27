import { useApp } from "./AppContext";
import { About } from "./About";
import { Icon } from "./Icon";
import { useStore } from "./useStore";
import logoMark from "../assets/logo-mark.png";
import css from "./Header.module.css";
import { AccountBadge } from "../modules/assistant/AccountBadge";

/** Title bar of the window (ТЗ 7.0.3): logo, project, command search, theme, «О производителе» (Д-12), Claude. */
export function Header() {
  const app = useApp();
  const theme = useStore(app.store, (s) => s.workspace.theme);
  const project = useStore(app.store, (s) => s.projectName);
  return (
    <header className={css.header}>
      <div className={css.logo} title="BedrockForge by Mikhail Bandurkin">
        <img src={logoMark} alt="" className={css.mark} />
        <div className={css.wordmark}>
          <b>
            Bedrock<span className={css.forge}>Forge</span>
          </b>
          <span>by Mikhail Bandurkin</span>
        </div>
      </div>
      <button type="button" className={css.project} title="Проекты" data-testid="project-selector" onClick={() => void app.run("project.manage")}>
        <span>{project ?? "Проект не открыт"}</span>
        <Icon name="gear" size={13} />
      </button>
      <button type="button" className={css.search} onClick={() => void app.run("palette.open")} aria-label="Поиск команд и скважин">
        <Icon name="search" size={14} />
        Поиск команд и скважин
        <kbd>Ctrl+K</kbd>
      </button>
      <button type="button" className={css.icon} title={theme === "dark" ? "Тёмная тема (переключить на светлую)" : "Светлая тема (переключить на тёмную)"} aria-label="Тема" onClick={() => void app.run("theme.set")}>
        <Icon name={theme === "dark" ? "moon" : "sun"} size={16} />
      </button>
      <span className={css.aboutAnchor}>
        <button type="button" className={css.icon} title="О производителе" aria-label="О производителе" data-about-toggle="" onClick={() => void app.run("app.about")}>
          <Icon name="info" size={16} />
        </button>
        <About />
      </span>
      <button type="button" className={css.icon} title="Claude (Ctrl+J)" aria-label="Claude" onClick={() => void app.run("panel.set", { panel: "assistant" })}>
        <Icon name="spark" size={16} />
      </button>
      <AccountBadge onOpen={() => void app.run("assistant.connect")} />
    </header>
  );
}
