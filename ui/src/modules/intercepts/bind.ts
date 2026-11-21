/**
 * Handlers of `intercept.tt` and `intercept.run` (ADR 0021 §9). Without the required arguments a command opens its
 * window with what is given (`intercepts-tt`, `intercepts-run`); with them — the values and the block «Ориентировка»
 * are checked first (a value out of the ranges of 8.5.4 never reaches the core), the tables named by the assistant
 * are resolved, and the job starts. 409 `table_exists` (`/run`): the assistant takes the free name. Rebuild and
 * delete are the commands of Э12 (`composite.rebuild` goes to `/intercepts` by the operation of the result).
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { renameOnExists, resolveTable, type Started, type TableRow } from "../composites/api";
import { hasRequired, toolReason } from "../composites/bind";
import { parseNames, valuesFromArgs, type ToolSpec } from "../composites/params";
import { loadOrientTables, startRun, startTt } from "./api";
import { interceptBody, orientationFromArgs, RUN_TOOL, TT_TOOL, type OrientationState } from "./params";

export const TT_WINDOW = "intercepts-tt";
export const RUN_WINDOW = "intercepts-run";

const HINT_RANGE = "Диапазоны — в описании команды (ТЗ 8.5.4); исправьте значение и повторите.";

export function openIntercept(tool: ToolSpec, args: Record<string, unknown> = {}): string {
  const given = Object.fromEntries(Object.entries(args).filter(([, v]) => v !== undefined && v !== null));
  return openWindow(tool.id === "tt" ? TT_WINDOW : RUN_WINDOW, tool.title, "normal", Object.keys(given).length ? { args: given } : undefined, ...viewport());
}

function key(tables: readonly TableRow[], given: string, what = "Таблица"): string {
  const k = resolveTable(tables, given);
  if (k === null) throw refusal("table_not_found", `${what} «${given}» не найдена среди таблиц набора.`, "Имена таблиц — в дереве набора скважин.");
  return k;
}

/**
 * The body of the job from the arguments of a command: the values are checked before any request (with the table
 * as given), then the tables are resolved by name and the body is built again with the keys.
 */
export async function interceptJobBody(tool: ToolSpec, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const values = valuesFromArgs(tool.params, args);
  const orient = orientationFromArgs(args, tool.id === "run");
  if (typeof orient === "string") throw refusal("bad_params", orient, HINT_RANGE);
  if (typeof args.set_id !== "number") throw refusal("set_not_found", "Не задан набор скважин.", "Укажите set_id — номер набора в дереве проекта.");
  const table = String(args.table).trim();
  const field = typeof args.field === "string" ? args.field.trim() : null;
  const first = interceptBody(tool, { setId: args.set_id, table, field }, values, orient);
  if (!first.body) throw refusal("out_of_range", first.reason ?? "Параметры не заданы.", HINT_RANGE);
  const tables = await loadOrientTables(args.set_id);
  const resolved: OrientationState = { ...orient };
  if (orient.source === "domain" && orient.domainBy === "field" && orient.domainTable) resolved.domainTable = key(tables, orient.domainTable, "Таблица домена");
  if (orient.source === "core" && orient.enabled) resolved.coreTable = key(tables, orient.coreTable, "Таблица замеров керна");
  const source = key(tables.filter((t) => t.kind !== "event"), table);
  const r = interceptBody(tool, { setId: args.set_id, table: source, field }, values, resolved);
  if (!r.body) throw refusal("bad_params", r.reason ?? "Параметры не заданы.", HINT_RANGE);
  if (tool.id === "run" && typeof args.fields === "string" && args.fields.trim()) r.body.fields = parseNames(args.fields);
  return r.body;
}

const startedText = (tool: ToolSpec, s: Started): string => `«${tool.title}»: задание №${s.id} поставлено в очередь — ход на вкладке «Задания»`;

function bindOne(app: App, tool: ToolSpec, start: typeof startTt): void {
  app.registry.bind(
    tool.command,
    noted(app, async (a, source: CommandSource) => {
      if (!hasRequired(tool, a)) {
        openIntercept(tool, a);
        return `Открыто окно «${tool.title}»${source === "claude" ? ": заданы не все обязательные параметры — " + tool.required.join(", ") : ""}`;
      }
      const s = await start(await interceptJobBody(tool, a), renameOnExists);
      if (!s) return "Отменено";
      showJobs(app);
      await refreshJobs().catch(() => undefined);
      return startedText(tool, s);
    }),
    (ctx) => toolReason(app, tool, ctx),
  );
}

export function bindIntercepts(app: App): void {
  bindOne(app, TT_TOOL, startTt);
  bindOne(app, RUN_TOOL, startRun);
}
