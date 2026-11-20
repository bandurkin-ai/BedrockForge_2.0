/**
 * Handlers of the 13 commands of sample processing (ADR 0018 §10). A tool command without its required arguments
 * opens the tool window with what is given; with them — the values are checked first (a value out of the ranges of
 * 8.2.2 never reaches the core), tables named by the assistant are resolved, and the job starts. 409
 * `table_exists`: the assistant takes the free name (« (2)»); «Заменить» is only the window's. Delete from the
 * interface asks our own question (`danger`); the assistant's calls are confirmed by the registry (`confirm`).
 */
import type { App } from "../../shell/app";
import type { CommandContext, CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { sceneObjects } from "../scene/objects";
import { tablesStore } from "../tables/state";
import { deleteResult, editOre, exportProtocol, loadFields, loadTables, rebuildResult, renameOnExists, resolveTable, startTool, type Started, type TableRow } from "./api";
import { checkParams, firstError, parseNames, sourceFromArgs, toolBody, valuesFromArgs, type FormValues, type SourceState, type ToolSpec } from "./params";
import { TOOLS } from "./tools";

export const WINDOW_PREFIX = "composites-";

/** The values of the form of a tool from the arguments of its command. */
export function toolValues(tool: ToolSpec, args: Record<string, unknown>): FormValues {
  const values = valuesFromArgs(tool.params, args);
  return tool.fromArgs ? tool.fromArgs(args, values) : values;
}

export function openTool(tool: ToolSpec, args: Record<string, unknown> = {}): string {
  const given = Object.fromEntries(Object.entries(args).filter(([, v]) => v !== undefined && v !== null));
  return openWindow(`${WINDOW_PREFIX}${tool.id}`, tool.title, tool.id === "composite" || tool.id === "ore" ? "normal" : "form", Object.keys(given).length ? { args: given } : undefined, ...viewport());
}

/** Every required argument is given (a string — not empty). */
export function hasRequired(tool: ToolSpec, args: Record<string, unknown>): boolean {
  return tool.required.every((k) => args[k] !== undefined && args[k] !== null && (typeof args[k] !== "string" || (args[k] as string).trim() !== ""));
}

const HINT_RANGE = "Диапазоны — в описании команды (ТЗ 8.2.2); исправьте значение и повторите.";

function tableKey(tables: readonly TableRow[], given: string): string {
  const key = resolveTable(tables, given);
  if (key === null) throw refusal("table_not_found", `Таблица «${given}» не найдена среди интервальных таблиц набора.`, "Имена таблиц — в дереве набора скважин.");
  return key;
}

/**
 * The body of the job of a tool from the arguments of its command. The parameters are checked before any request;
 * then the tables are resolved by name, and «все числовые» fields are read when `fields` is not given.
 */
export async function jobBody(tool: ToolSpec, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const values = toolValues(tool, args);
  const bad = firstError(checkParams(tool.params, values).errors);
  if (bad) throw refusal("out_of_range", bad, HINT_RANGE);
  const given = sourceFromArgs(args);
  if (given.setId === null) throw refusal("set_not_found", "Не задан набор скважин.", "Укажите set_id — номер набора в дереве проекта.");
  const tables = await loadTables(given.setId);
  const src: SourceState = { ...given };
  if (given.table) src.table = tableKey(tables, given.table);
  src.tables = given.tables.map((t) => tableKey(tables, t));
  if (given.domain.kind === "field") src.domain = { ...given.domain, table: tableKey(tables, given.domain.table || given.table || "") };
  if (tool.source.fields === "many" && !src.fields.length && src.table) src.fields = (await loadFields(given.setId, src.table)).filter((f) => f.kind === "number").map((f) => f.name);
  const r = toolBody(tool, src, values);
  if (!r.body) throw refusal("bad_params", r.reason ?? "Параметры не заданы.", HINT_RANGE);
  return r.body;
}

const startedText = (tool: ToolSpec, s: Started): string => `«${tool.title}»: задание №${s.id} поставлено в очередь — ход на вкладке «Задания»`;

async function started(app: App, tool: ToolSpec, s: Started | null): Promise<string> {
  if (!s) return "Отменено";
  showJobs(app);
  await refreshJobs().catch(() => undefined);
  return startedText(tool, s);
}

/**
 * Why a tool of sample processing is unavailable (ADR 0018 decision 25): no project or read-only, no active set,
 * the active set has no interval tables (once its tables are read), «Кодировать каркасами» — no closed wireframe in
 * the project (once the wireframes are read; a body not checked yet counts as possible).
 */
export function toolReason(app: App, tool: ToolSpec, ctx: CommandContext): string | null {
  const base = needWritable(app)(ctx);
  if (base) return base;
  if (!ctx.hasSet) return "Нет активного набора скважин";
  const tabs = tablesStore.get().tabs;
  if (tabs.length && !tabs.some((t) => t.kind === "assay" || t.kind === "interval")) return "В наборе нет таблиц проб";
  if (tool.source.wireframes) {
    const scene = sceneObjects.get();
    if (scene.loaded && !scene.wireframes.some((w) => !w.dtm && w.stats?.closed !== false)) return "В проекте нет замкнутых каркасов";
  }
  return null;
}

function bindTool(app: App, tool: ToolSpec): void {
  app.registry.bind(
    tool.command,
    noted(app, async (a, source: CommandSource) => {
      if (!hasRequired(tool, a)) {
        openTool(tool, a);
        return `Открыто окно «${tool.title}»${source === "claude" ? ": заданы не все обязательные параметры — " + tool.required.join(", ") : ""}`;
      }
      const body = await jobBody(tool, a);
      return started(app, tool, await startTool(tool.endpoint, body, renameOnExists));
    }),
    (ctx) => toolReason(app, tool, ctx),
  );
}

const resultId = (a: Record<string, unknown>): number => {
  if (typeof a.result_id !== "number") throw refusal("result_not_found", "Не задан результат обработки проб.", "Номер результата — в дереве набора › «Обработка проб».");
  return a.result_id;
};

export function bindComposites(app: App): void {
  for (const tool of TOOLS) bindTool(app, tool);

  app.registry.bind(
    "composite.rebuild",
    noted(app, async (a) => {
      const s = await rebuildResult(resultId(a));
      showJobs(app);
      await refreshJobs().catch(() => undefined);
      return `Результат №${resultId(a)}: перестроение — задание №${s.id}`;
    }),
    needWritable(app),
  );
  app.registry.bind(
    "composite.delete",
    noted(app, async (a, source) => {
      const id = resultId(a);
      if (source === "user" && !(await askConfirm({ title: "Удалить результат", message: `Удалить результат обработки проб №${id}? Таблица-результат или записанные колонки будут удалены.`, okLabel: "Удалить", danger: true })))
        return "Отменено";
      await deleteResult(id);
      return `Результат №${id} удалён`;
    }),
    needWritable(app),
  );
  app.registry.bind(
    "composite.ore_intervals_edit",
    noted(app, async (a) => {
      const id = resultId(a);
      if (typeof a.hole !== "string" || !a.hole.trim()) throw refusal("hole_not_found", "Не задана скважина.", "Укажите hole — имя скважины.");
      if (typeof a.action !== "string" || typeof a.from !== "number" || typeof a.to !== "number") throw refusal("bad_params", "Нужны action, from и to.", "action: split, merge или move; from и to — глубины, м.");
      const r = await editOre(id, { hole: a.hole.trim(), action: a.action, from: a.from, to: a.to });
      const broken = Array.isArray(r.violations) ? r.violations.length : 0;
      return `Скважина ${a.hole.trim()}: правка «${a.action}» записана${broken ? `; нарушений кондиций: ${broken}` : ""}`;
    }),
    needWritable(app),
  );
  app.registry.bind(
    "composite.ore_protocol",
    noted(app, async (a) => {
      const id = resultId(a);
      const holes = typeof a.holes === "string" ? parseNames(a.holes) : [];
      const s = await exportProtocol(id, { format: a.format === "csv" ? "csv" : "xlsx", ...(holes.length ? { holes } : {}) });
      showJobs(app);
      await refreshJobs().catch(() => undefined);
      return `Протокол результата №${id}: задание №${s.id}`;
    }),
    needProject,
  );
}
