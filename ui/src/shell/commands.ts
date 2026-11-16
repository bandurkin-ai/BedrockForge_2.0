/**
 * The command registry is the single description of what the application can do (ТЗ 6.9, 7.2).
 * The ribbon, the palette (Ctrl+K), hotkeys and the Claude assistant run the same commands.
 * Pure module: no React, no three.js (tested in node).
 */

export interface ParamSchema {
  type: "string" | "number" | "integer" | "boolean";
  description?: string;
  enum?: readonly (string | number)[];
  /** The value may also be null (JSON Schema "type": [type, "null"]). */
  nullable?: boolean;
}

/** What a command may look at to decide whether it is available. */
export interface CommandContext {
  hasProject: boolean;
  hasSet: boolean;
  hasHole: boolean;
  hasSection: boolean;
  editing: boolean;
  coreOnline: boolean;
}

export const EMPTY_CONTEXT: CommandContext = {
  hasProject: false,
  hasSet: false,
  hasHole: false,
  hasSection: false,
  editing: false,
  coreOnline: false,
};

export type CommandSource = "user" | "claude";

export interface Command {
  /** Stable dotted id, e.g. "scene.view". */
  id: string;
  /** Russian title for people. */
  title: string;
  /** Written for the model: what the command does and when to use it. */
  description: string;
  params?: Record<string, ParamSchema>;
  required?: string[];
  /** Hidden from Claude (needs a person, or only makes sense in the UI). */
  uiOnly?: boolean;
  /** Not written to the command log. */
  silent?: boolean;
  /** Changes stored data: a call from Claude runs only after the user's confirmation. */
  confirm?: boolean;
  /** Stage of the plan that implements the command; until then it is unavailable with a reason. */
  stage?: string;
  /** null — available; a string — why not (shown in the tooltip, returned to Claude). */
  unavailable?: (ctx: CommandContext) => string | null;
  /** Returns a short Russian result for the log and for Claude. */
  run?: (args: Record<string, unknown>, source: CommandSource) => string | void | Promise<string | void>;
}

/** Why the command cannot run now, or null. */
export function unavailableReason(command: Command, ctx: CommandContext): string | null {
  if (command.stage || !command.run) return `Функция появится на этапе ${command.stage ?? "разработки"}`;
  return command.unavailable?.(ctx) ?? null;
}

/** Tool definition in the shape of the Claude Messages API (ТЗ 6.9: generated from the registry). */
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

/** "scene.view" -> "ui_scene_view" (tool names allow only letters, digits, "_" and "-"). */
export const toolName = (commandId: string) => TOOL_PREFIX + commandId.replace(/\./g, "_");

export function commandTools(commands: Command[]): ToolDefinition[] {
  return commands
    .filter((c) => !c.uiOnly)
    .map((c) => ({
      name: toolName(c.id),
      description: `${c.title}. ${c.description}${c.confirm ? " Меняет данные: выполняется после подтверждения пользователем." : ""}`,
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

/** Checks arguments against the command's parameters; returns a Russian error or null. */
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
      p.type === "string"
        ? typeof value === "string"
        : p.type === "boolean"
          ? typeof value === "boolean"
          : typeof value === "number" && Number.isFinite(value) && (p.type === "number" || Number.isInteger(value));
    if (!ok) return `Параметр «${key}» должен иметь тип ${p.type}`;
    if (p.enum && !p.enum.includes(value as string | number)) {
      return `Параметр «${key}»: допустимые значения — ${p.enum.join(", ")}`;
    }
  }
  return null;
}

/** Case-insensitive match of every query word against the text. */
export function matchesQuery(text: string, query: string): boolean {
  const haystack = text.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}
