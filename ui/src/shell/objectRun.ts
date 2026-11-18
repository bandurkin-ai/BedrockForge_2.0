/**
 * Shared parts of the handlers of object commands (ADR 0011 §5: lines, rasters, wireframes, DTM): the reason
 * of a command that needs a project, a failure told also in the status bar (the ribbon, the palette and the
 * keys have no other place for it), the «Задания» tab opened for a started export. No React.
 */
import type { App } from "./app";
import type { Command, CommandContext } from "./commands";
import { errorText } from "./props";
import { setPanel } from "./workspace";

export const needProject = (ctx: CommandContext): string | null => (ctx.hasProject ? null : "Откройте или создайте проект");

export const needSection = (ctx: CommandContext): string | null => needProject(ctx) ?? (ctx.hasSection ? null : "Сначала проведите линию разреза");

export function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

/** A handler whose failure is a Russian text with its hint; a call from the interface shows it in the status bar too. */
export function noted(app: App, run: NonNullable<Command["run"]>): NonNullable<Command["run"]> {
  return async (args, source) => {
    try {
      return await run(args, source);
    } catch (e) {
      const text = errorText(e);
      if (source === "user") app.store.set({ statusNote: text });
      throw new Error(text);
    }
  };
}

/** Opens the «Задания» tab: a started job is seen there. */
export function showJobs(app: App): void {
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "jobs") }));
}
