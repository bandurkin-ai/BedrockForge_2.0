/**
 * The command registry is the single description of what the application can do.
 * The ribbon, the command palette, hotkeys, the built-in Claude assistant and the
 * MCP bridge all run the same commands, so a new feature is added in one place.
 */

export interface ParamSchema {
  type: "string" | "number" | "integer" | "boolean";
  description?: string;
  enum?: readonly (string | number)[];
  /** The value may also be null (JSON Schema "type": [type, "null"]). */
  nullable?: boolean;
}

export interface Command {
  /** Stable dotted id, e.g. "view.open". */
  id: string;
  title: string;
  /** Written for the model: what the command does and when to use it. */
  description: string;
  params?: Record<string, ParamSchema>;
  required?: string[];
  /** Hidden from Claude (needs a person, or only makes sense from the UI). */
  uiOnly?: boolean;
  /** Not written to the command log (pure UI chrome such as opening the palette). */
  silent?: boolean;
  /** Changes stored data: Claude must get the user's approval first. */
  confirm?: boolean;
  /** Returns a short human-readable result; it is logged and sent back to Claude. */
  run: (args: Record<string, unknown>) => string | void | Promise<string | void>;
}

export type CommandSource = "user" | "claude" | "remote";

export interface LogEntry {
  id: number;
  time: Date;
  source: CommandSource;
  title: string;
  args: Record<string, unknown>;
  result: string;
  ok: boolean;
}

/** Tool definition in the shape of the Claude Messages API. */
export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: {
    type: "object";
    properties: Record<string, unknown>;
    required: string[];
    additionalProperties: false;
  };
}

const TOOL_PREFIX = "ui_";

/** "view.open" -> "ui_view_open" (tool names allow only letters, digits, "_" and "-"). */
export const toolName = (commandId: string) => TOOL_PREFIX + commandId.replace(/\./g, "_");

export function commandTools(commands: Command[]): ToolDefinition[] {
  return commands
    .filter((c) => !c.uiOnly)
    .map((c) => ({
      name: toolName(c.id),
      description: `${c.title}. ${c.description}`,
      input_schema: {
        type: "object",
        properties: Object.fromEntries(
          Object.entries(c.params ?? {}).map(([key, p]) => {
            const { nullable, ...schema } = p;
            return [key, nullable ? { ...schema, type: [p.type, "null"] } : schema];
          }),
        ),
        required: c.required ?? [],
        additionalProperties: false,
      },
    }));
}

export function findByToolName(commands: Command[], name: string): Command | undefined {
  return commands.find((c) => !c.uiOnly && toolName(c.id) === name);
}

/** Check arguments that came from outside the UI (Claude, MCP) against the command's parameters. */
export function validateArgs(command: Command, args: unknown): string | null {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "Аргументы должны быть объектом";
  const params = command.params ?? {};
  const values = args as Record<string, unknown>;
  for (const key of Object.keys(values)) {
    if (!(key in params)) return `Неизвестный параметр «${key}»`;
  }
  for (const key of command.required ?? []) {
    if (!(key in values)) return `Не задан параметр «${key}»`;
  }
  for (const [key, value] of Object.entries(values)) {
    const p = params[key];
    if (value === null) {
      if (!p.nullable) return `Параметр «${key}» не может быть пустым`;
      continue;
    }
    const ok =
      p.type === "string" ? typeof value === "string"
      : p.type === "boolean" ? typeof value === "boolean"
      : typeof value === "number" && Number.isFinite(value) && (p.type === "number" || Number.isInteger(value));
    if (!ok) return `Параметр «${key}» должен иметь тип ${p.type}`;
    if (p.enum && !p.enum.includes(value as string | number)) {
      return `Параметр «${key}»: допустимые значения — ${p.enum.join(", ")}`;
    }
  }
  return null;
}

/** "ctrl+k", "escape", "3" — layout-independent: letters and digits are matched by physical key. */
export function eventShortcut(e: KeyboardEvent): string {
  let key = e.key.toLowerCase();
  if (/^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
  else if (/^Digit\d$/.test(e.code)) key = e.code.slice(5);
  else if (e.code === "Backquote") key = "`";
  return [(e.ctrlKey || e.metaKey) && "ctrl", e.altKey && "alt", e.shiftKey && "shift", key].filter(Boolean).join("+");
}

export function formatShortcut(shortcut: string): string {
  return shortcut
    .split("+")
    .map((part) => (part === "ctrl" ? "Ctrl" : part === "alt" ? "Alt" : part === "shift" ? "Shift" : part === "escape" ? "Esc" : part.toUpperCase()))
    .join("+");
}

/** Case-insensitive match of every query word against the text. */
export function matchesQuery(text: string, query: string): boolean {
  const haystack = text.toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}
