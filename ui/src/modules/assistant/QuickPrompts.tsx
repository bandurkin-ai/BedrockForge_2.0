/**
 * The quick requests of an empty dialog (Э23, 23.14; ТЗ 7.0.4; ADR 0029 решение 33): the three of `QUICK_PROMPTS` of the ribbon. A button runs the command
 * `assistant.ask {prompt}` (the registry: log, hotkeys and the assistant see it as any command). «Проверить скважину» without a chosen hole is unavailable
 * with the reason shown under the button; so are all of them when the assistant cannot be asked (not connected, no internet).
 */
import { QUICK_PROMPTS } from "../../shell/ribbonConfig";
import { commandContext } from "../../shell/store";
import type { CommandContext } from "../../shell/commands";
import type { App } from "../../shell/app";
import { useApp } from "../../shell/AppContext";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import css from "./QuickPrompts.module.css";

export const NO_HOLE_REASON = "Скважина не выбрана: выберите её в дереве или в окне 3D";

export interface QuickItem {
  label: string;
  prompt: string;
  /** why the request cannot be sent now; null — it can */
  reason: string | null;
}

/** The buttons with their reasons: the state of the assistant first, then the command `assistant.ask`, then the hole the request needs. */
export function quickItems(ctx: CommandContext, askReason: string | null, blocked: string | null): QuickItem[] {
  return QUICK_PROMPTS.map((q) => ({
    label: q.label,
    prompt: q.prompt,
    reason: blocked ?? askReason ?? (q.needsHole && !ctx.hasHole ? NO_HOLE_REASON : null),
  }));
}

export interface QuickPromptsViewProps {
  items: QuickItem[];
  onPick: (prompt: string) => void;
}

export function QuickPromptsView({ items, onPick }: QuickPromptsViewProps) {
  return (
    <div className={css.box} data-testid="quick-prompts">
      <div className={css.head}>Быстрые запросы</div>
      {items.map((q) => (
        <div key={q.label} className={css.item}>
          <button
            type="button"
            className={css.btn}
            data-testid="quick-prompt"
            aria-disabled={q.reason ? true : undefined}
            title={q.reason ? `Недоступно: ${q.reason}` : q.prompt}
            onClick={() => {
              if (!q.reason) onPick(q.prompt);
            }}
          >
            {q.label}
          </button>
          {q.reason ? (
            <span className={css.reason} data-testid="quick-reason">
              {q.reason}
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** A quick request is the command `assistant.ask {prompt}` (the registry gives it the log, the availability and the source). */
export const askPrompt = (app: Pick<App, "run">, prompt: string): void => void app.run("assistant.ask", { prompt });

/** The quick requests connected to the registry and to the state of the window. `blocked` is the reason the assistant cannot be asked now. */
export function QuickPrompts({ blocked }: { blocked: string | null }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const state = useStore(app.store, (s) => s);
  const ctx = commandContext(state);
  const items = quickItems(ctx, app.registry.reason("assistant.ask", ctx), blocked);
  return <QuickPromptsView items={items} onPick={(prompt) => askPrompt(app, prompt)} />;
}
