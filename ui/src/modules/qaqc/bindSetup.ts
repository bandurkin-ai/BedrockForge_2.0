/**
 * The handlers of the setup commands of QAQC (ADR 0020 §9; the commands are declared by `commands.ts`, here they
 * are bound by id): `qaqc.setup` opens the window «Настройка QAQC» on a tab; `qaqc.import` loads control samples
 * from a file or from a table of a set (without a source — the form of the window; with one — read, mapped
 * automatically where the mapping is not given, the job `qaqc.import`); `qaqc.data_rename`, `qaqc.data_refresh`
 * (`POST /data/{id}/refresh`: the core imports again from the source and with the mapping it keeps), `qaqc.data_delete`; `qaqc.config` and `qaqc.crm` save the setup of
 * the project and the CRM reference with `expected_version`. A change from the interface asks our own question
 * where the command is marked confirm (the assistant is asked by the registry). The assistant gets the totals.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import type { CommandContext } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { configChanged, resetConfig, runConfig, runCrm } from "./setup/configStore";
import { dataStore, dropData, findData, finishRun, openForm, refuseImport, reloadData, resetData, startedRun, upsertData } from "./setup/dataStore";
import { completeMapping, countLines, defaultDataName, mappingFromArg, mappingProblems } from "./setup/mapping";
import { deleteData, getData, inspectSource, refreshData, renameData, sourceBody, startImport, waitJob, type InspectSource, type Mapping, type QaqcData, type ReadOptions, type Started } from "./setupApi";
import { SetupWindow, SETUP_TABS, type SetupTab } from "./SetupWindow";

export const SETUP_WINDOW = "qaqc-setup";
export const SETUP_TITLE = "Настройка QAQC";

/** The ids of ADR 0020 §9 bound here (declared in `commands.ts`). */
export const SETUP_COMMANDS = ["qaqc.setup", "qaqc.import", "qaqc.data_rename", "qaqc.data_refresh", "qaqc.data_delete", "qaqc.config", "qaqc.crm"] as const;

/** Opens the window «Настройка QAQC» (one) on a tab; an open one comes to the front on that tab. */
export function openSetup(tab: SetupTab = "data"): string {
  const open = windowStore.get().windows.find((w) => w.kind === SETUP_WINDOW);
  if (open) {
    updateWindow(open.id, { props: { ...open.props, tab } });
    focusWindow(open.id);
    return open.id;
  }
  return openWindow(SETUP_WINDOW, SETUP_TITLE, "normal", { tab }, ...viewport());
}

const notFound = (id: unknown) => refusal("qaqc_data_not_found", `Данные QAQC №${String(id)} не найдены.`, "Номера данных — в «Настройка QAQC → Данные» и в разделе дерева «Аудит и статистика».");

/** The data of the arguments: the list, else the core (404 — not found). */
export async function dataOf(id: unknown): Promise<QaqcData> {
  if (typeof id !== "number" || !Number.isInteger(id)) throw notFound(id);
  const known = findData(id);
  if (known) return known;
  try {
    const row = await getData(id);
    upsertData(row);
    return row;
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) throw notFound(id);
    throw e;
  }
}

const READ_KEYS = ["sheet", "encoding", "delimiter", "header_row"] as const;

/** The read options a mapping of the form carries (`mapping.read`): the sheet of a book, the encoding… */
function readOptions(mapping: unknown): ReadOptions {
  const r = mapping && typeof mapping === "object" ? (mapping as { read?: unknown }).read : undefined;
  if (!r || typeof r !== "object") return {};
  return Object.fromEntries(READ_KEYS.filter((k) => (r as Record<string, unknown>)[k] !== undefined).map((k) => [k, (r as Record<string, unknown>)[k]])) as ReadOptions;
}

function parseArgMapping(value: unknown): { mapping: Mapping | null; read: ReadOptions } {
  if (value === undefined || value === null || value === "") return { mapping: null, read: {} };
  const m = mappingFromArg(value);
  if (typeof m === "string") throw refusal("bad_param", `Сопоставление колонок не разобрано: ${m}.`, "mapping — JSON-объект {sample_id: «колонка», type: «колонка», …, elements: [{column, name?, unit?, dl_column?}], extra: […], read?: {sheet?}}.");
  let raw: unknown = value;
  if (typeof value === "string") raw = JSON.parse(value);
  return { mapping: m, read: readOptions(raw) };
}

/** The source of `qaqc.import`: a file by `path` or a table of a set by `set_id` + `table`; null — none given. */
function sourceOf(args: Record<string, unknown>, read: ReadOptions): InspectSource | null {
  if (typeof args.path === "string" && args.path.trim()) return { kind: "file", path: args.path.trim(), ...read };
  if (typeof args.set_id === "number" && typeof args.table === "string" && args.table) return { kind: "table", set_id: args.set_id, table: args.table };
  return null;
}

const dataText = (name: string, id: number | null) => (id !== null ? `«${name}» (данные QAQC №${id})` : `«${name}»`);

/** Follows an import job: the list is read again, the totals go to the window; resolves with the assistant's text. */
async function follow(job: Started, name: string): Promise<string> {
  const mineNow = () => {
    const run = dataStore.get().run;
    return run?.job.source === job.source && run.job.id === job.id;
  };
  let j;
  try {
    j = await waitJob(job);
  } catch (e) {
    // the project changed or the job is gone: the window stops showing it as running
    if (mineNow()) finishRun(null);
    throw e;
  }
  const mine = mineNow();
  if (j.status !== "done") {
    if (mine) finishRun(null);
    if (j.status === "cancelled") return `Загрузка ${dataText(name, null)} отменена: ничего не записано`;
    throw refusal(j.error?.code ?? "job_failed", j.error?.message ?? "Загрузка контрольных проб завершилась ошибкой.", j.error?.hint ?? "Подробности — во вкладке «Задания».");
  }
  const r = (j.result ?? {}) as { data_id?: unknown; rows?: unknown; counts?: unknown };
  const dataId = typeof r.data_id === "number" ? r.data_id : null;
  const rows = typeof r.rows === "number" ? r.rows : 0;
  const counts = r.counts && typeof r.counts === "object" ? (r.counts as Record<string, number>) : {};
  if (mine) finishRun({ dataId, name, rows, counts });
  await reloadData();
  return `Загружены данные QAQC ${dataText(name, dataId)}: ${countLines(rows, counts).join("; ")}`;
}

/** Follows a started import job: in the window for the person, to its end for the assistant. */
function track(job: Started, name: string, replaceId: number | null, by: "user" | "claude", what: string): Promise<string> | string {
  startedRun({ job, name, replaceId });
  if (by === "claude") return follow(job, name);
  openSetup("data");
  void follow(job, name).catch(() => undefined); // the window shows the failure of the job
  return `${what} ${dataText(name, null)}: задание №${job.id}`;
}

/** Starts the job; a refusal of the core goes to its field of the form. */
async function start(name: string, source: InspectSource, mapping: Mapping, replaceId: number | null, by: "user" | "claude"): Promise<string> {
  let job: Started;
  try {
    job = await startImport({ name, source: sourceBody(source), mapping, replace_id: replaceId });
  } catch (e) {
    refuseImport(e);
    throw e;
  }
  return track(job, name, replaceId, by, "Загрузка");
}

const askReplace = (row: QaqcData | undefined, id: number) =>
  askConfirm({
    title: "Заменить данные QAQC",
    message: `Данные ${row ? `«${row.name}»` : `№${id}`} будут заменены новыми записями. Отчёты, построенные по ним, станут устаревшими.`,
    okLabel: "Заменить",
    danger: true,
  });

/** `qaqc.import` (ADR 0020 §9). */
export async function runImport(args: Record<string, unknown>, by: "user" | "claude"): Promise<string> {
  const { mapping: given, read } = parseArgMapping(args.mapping);
  const source = sourceOf(args, read);
  const replaceId = typeof args.replace_id === "number" ? args.replace_id : null;
  if (!source) {
    if (by === "claude") throw refusal("bad_param", "Не задан источник контрольных проб.", "Укажите path файла (CSV, TXT, DAT, XLSX, XLS) или set_id и table таблицы набора.");
    openSetup("data");
    openForm({ mode: typeof args.set_id === "number" ? "table" : "file", setId: typeof args.set_id === "number" ? args.set_id : null, table: null, replaceId });
    return "Открыта загрузка контрольных проб";
  }
  if (by === "user" && !given) {
    // the interface reads the source in the form: preview, mapping, units by hand
    openSetup("data");
    openForm({ mode: source.kind, path: source.kind === "file" ? source.path : null, setId: source.kind === "table" ? source.set_id : null, table: source.kind === "table" ? source.table : null, replaceId });
    return "Открыта загрузка контрольных проб";
  }
  let mapping = given;
  if (!mapping || !Object.keys(mapping.fields).length || !mapping.elements.length) {
    const answer = await inspectSource(source);
    mapping = completeMapping(mapping ?? { fields: {}, elements: [], extra: [] }, answer.columns);
  }
  const name = typeof args.name === "string" && args.name.trim() ? args.name.trim() : defaultDataName(source.kind === "file" ? source : { kind: "table", tableName: `таблица ${source.table}`, setName: `Набор №${source.set_id}` });
  const problems = mappingProblems(mapping, name);
  if (problems.length) {
    const e = refusal(problems[0].at === "sample_id" ? "no_sample_id" : problems[0].at === "type" ? "no_type" : problems[0].at === "elements" ? "no_elements" : "bad_param", problems.map((p) => p.message).join("; ") + ".", "Задайте mapping явно: колонки SAMPLE_ID, TYPE и элементы.");
    refuseImport(e);
    throw e;
  }
  if (replaceId !== null && by === "user") {
    const row = findData(replaceId);
    if (!(await askReplace(row, replaceId))) return "Отменено";
  }
  return start(name, source, mapping, replaceId, by);
}

/**
 * `qaqc.data_refresh {data_id}`: the core imports the data again from the source it keeps (the file by its path,
 * the table of the set) with the mapping it keeps (`POST /data/{id}/refresh`); a source gone — its refusal
 * (`source_missing`, `file_not_found`).
 */
export async function runRefresh(args: Record<string, unknown>, by: "user" | "claude"): Promise<string> {
  const row = await dataOf(args.data_id);
  const from = row.source?.kind === "table" ? `таблицы ${row.source.table_name ? `«${row.source.table_name}» ` : ""}набора` : "файла";
  if (by === "user" && !(await askConfirm({ title: "Обновить данные QAQC", message: `Данные «${row.name}» будут загружены заново из ${from} с прежним сопоставлением колонок. Отчёты, построенные по ним, станут устаревшими.`, okLabel: "Обновить" })))
    return "Отменено";
  const job = await refreshData(row.id);
  return track(job, row.name, row.id, by, "Обновление");
}

/** `qaqc.data_rename {data_id, name}`. */
export async function runRename(args: Record<string, unknown>): Promise<string> {
  const row = await dataOf(args.data_id);
  const name = typeof args.name === "string" ? args.name.trim() : "";
  if (!name) throw refusal("bad_param", "Пустое название данных.", "Задайте непустое название.");
  if (name === row.name) return `Название «${name}» не изменилось`;
  try {
    upsertData(await renameData(row.id, name, row.version));
  } catch (e) {
    if (e instanceof CoreError && e.status === 409 && e.code === "version_conflict") void reloadData();
    throw e;
  }
  return `Данные QAQC «${row.name}» переименованы в «${name}»`;
}

/** `qaqc.data_delete {data_id}`. */
export async function runDelete(args: Record<string, unknown>, by: "user" | "claude"): Promise<string> {
  const row = await dataOf(args.data_id);
  if (by === "user" && !(await askConfirm({ title: "Удалить данные QAQC", message: `Данные «${row.name}» (${row.rows.toLocaleString("ru-RU")} записей) удаляются из проекта. Отчёты QAQC остаются, с пометкой «источник удалён».`, okLabel: "Удалить", danger: true })))
    return "Отменено";
  await deleteData(row.id);
  dropData(row.id);
  return `Удалены данные QAQC «${row.name}»`;
}

const writable =
  (app: Pick<App, "store">) =>
  (ctx: CommandContext): string | null =>
    needProject(ctx) ?? (app.store.get().projectReadOnly ? "Проект открыт только для чтения" : null);

export function bindQaqcSetup(app: App): void {
  const can = writable(app);
  app.registry.bind(
    "qaqc.setup",
    noted(app, (a) => {
      const tab = SETUP_TABS.some((t) => t.id === a.tab) ? (a.tab as SetupTab) : "data";
      openSetup(tab);
      return `Открыто окно «${SETUP_TITLE}»`;
    }),
    needProject,
  );
  app.registry.bind("qaqc.import", noted(app, (a, by) => runImport(a, by)), can);
  app.registry.bind("qaqc.data_rename", noted(app, (a) => runRename(a)), can);
  app.registry.bind("qaqc.data_refresh", noted(app, (a, by) => runRefresh(a, by)), can);
  app.registry.bind("qaqc.data_delete", noted(app, (a, by) => runDelete(a, by)), can);
  app.registry.bind("qaqc.config", noted(app, (a, by) => runConfig(a, by)), can);
  app.registry.bind("qaqc.crm", noted(app, (a, by) => runCrm(a, by)), can);
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetData();
    resetConfig();
    if (cur) void reloadData();
  });
  onCoreEvent((event, data) => {
    if (event === "qaqc.changed") return configChanged(data);
    if (event !== "objects.changed") return;
    try {
      const d = JSON.parse(data) as { kind?: unknown };
      if (d.kind !== "qaqc_data") return;
    } catch {
      return;
    }
    void reloadData().catch((e: unknown) => app.store.set({ statusNote: errorText(e) }));
  });
}

/** The window «Настройка QAQC» for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderQaqcSetupWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== SETUP_WINDOW) return null;
  const tab = SETUP_TABS.some((t) => t.id === w.props?.tab) ? (w.props?.tab as SetupTab) : "data";
  return createElement(SetupWindow, { winId: w.id, tab });
}
