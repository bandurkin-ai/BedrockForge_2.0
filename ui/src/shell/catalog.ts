/** Auto-registration (ТЗ 6.9): every ui/src/modules/<module>/commands.ts contributes its commands. */
import type { Command } from "./commands";
import { CommandRegistry } from "./registry";

type ModuleExports = { commands: Command[] };

export function moduleCommands(): Command[] {
  const mods = import.meta.glob<ModuleExports>("../modules/*/commands.ts", { eager: true });
  return Object.keys(mods)
    .sort()
    .flatMap((k) => mods[k].commands);
}

export function createRegistry(): CommandRegistry {
  const r = new CommandRegistry();
  r.register(moduleCommands());
  return r;
}
