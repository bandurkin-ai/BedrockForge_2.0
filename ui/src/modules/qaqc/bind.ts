/**
 * The commands of the tab «QAQC» (ADR 0020 §9, §11): `qaqc.open` opens the bottom tab on a section with the data,
 * the element and the type of duplicates of the arguments; `qaqc.filter` edits the filter of every subtab. For the
 * assistant `qaqc.open` computes its section at once and answers with the number of records and the share of
 * failures, `qaqc.filter` — with the conditions and «записей N из M». Unavailable without a project and without
 * data of control samples (the reason says where to load them). The list of the data is read on a new project
 * and on `objects.changed {kind: "qaqc_data"}`; `qaqc.changed` (the setup) makes the subtabs compute again.
 */
import type { App } from "../../shell/app";
import type { CommandContext, CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needWritable, noted, refusal } from "../../shell/objectRun";
import { setPanel } from "../../shell/workspace";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { calc, deleteReport, getReport, listReports, renameReport, type CalcCounts, type QaqcData } from "./api";
import { DUP_KINDS, DUP_TITLES, QAQC_SECTIONS, SECTION_TITLES, type DupKind, type QaqcSection } from "./commands";
import { countLabel, filterFromArgs, filterJson, filterSummary, listArg } from "./filters";
import { int, pct, share } from "./format";
import { CHARTS_MODES, findReport, loadReports, rebuildReason, reportLine, reportsStore, resetReports, runReport, saveXlsx, selectReport, summaryText, type ChartsMode, type ReportRun, type RunSpec } from "./reportRun";
import { activeData, activeElement, calcBody, loadDatas, qaqcChanged, qaqcStore, reportsChanged, resetQaqc, setData, setDupKind, setElement, setFilter, setSection } from "./state";

export const NEED_PROJECT = "Откройте проект";
export const NEED_DATA = "Загрузите контрольные пробы: «Анализ → QAQC → Настройка»";

/** Why the tab's commands cannot run: no project; the data of the project are read and there are none. */
export function qaqcReason(ctx: CommandContext): string | null {
  if (!ctx.hasProject) return NEED_PROJECT;
  const datas = qaqcStore.get().datas;
  return datas !== null && datas.length === 0 ? NEED_DATA : null;
}

/** The data of the project, read when not yet; none — a refusal with where to load them. */
async function needDatas(): Promise<QaqcData[]> {
  const datas = qaqcStore.get().datas ?? (await loadDatas());
  if (datas === null) throw refusal("qaqc_data_unavailable", qaqcStore.get().dataError ?? "Список данных QAQC не прочитан.", "Повторите команду.");
  if (!datas.length) throw refusal("no_qaqc_data", "В проекте нет данных контрольных проб.", "Загрузите их: «Анализ → QAQC → Настройка → Данные».");
  return datas;
}

const head = (element: string, unit: string | null, counts: CalcCounts) => `${element}${unit ? `, ${unit}` : ""} · ${countLabel(counts.filtered, counts.rows)}`;
const failures = (fail: number, n: number) => `отказов ${int(fail)} (${pct(share(fail, n))})`;

/** The short answer of `qaqc.open` for the assistant: the section computed with the filter of the tab. */
export async function sectionText(section: QaqcSection): Promise<string> {
  const s = qaqcStore.get();
  const title = SECTION_TITLES[section];
  if (section === "report") {
    const reports = await listReports();
    const stale = reports.filter((r) => r.stale).length;
    return `${title}: отчётов QAQC ${int(reports.length)}${stale ? `, устарели ${int(stale)}` : ""}`;
  }
  const body = calcBody(s, section === "duplicates" ? { kind: s.dupKind } : {});
  if (!body) throw refusal("no_elements", "В данных QAQC нет элементов.", "Загрузите данные с колонками элементов.");
  if (section === "standards" || section === "blanks") {
    const a = await calc(section, body);
    const n = a.groups.reduce((t, g) => t + g.n, 0);
    const fail = a.groups.reduce((t, g) => t + g.fail, 0);
    return `${title} (${head(a.element, a.unit, a.counts)}): групп ${int(a.groups.length)}, проб ${int(n)}, ${failures(fail, n)}`;
  }
  if (section === "duplicates") {
    const a = await calc("duplicates", body);
    const verdict = a.passed === null ? "критерий не оценён" : a.passed ? "критерий выполнен" : "критерий не выполнен";
    return `${title}, ${DUP_TITLES[s.dupKind]} (${head(a.element, a.unit, a.counts)}): пар ${int(a.pairs)}, в расчёте ${int(a.used)}, HARD ≤ ${a.hard_limit} % — ${pct(a.share_at_limit)} (нужно ${a.share_limit} %), ${verdict}`;
  }
  if (section === "external") {
    const a = await calc("external", body);
    const failed = a.groups.filter((g) => g.passed === false).length;
    return `${title} (${head(a.element, a.unit, a.counts)}): групп ${int(a.groups.length)}, пар ${int(a.groups.reduce((t, g) => t + g.used, 0))}, критерий не выполнен в ${int(failed)} (${pct(share(failed, a.groups.length))})`;
  }
  const a = await calc("batches", { ...body, limit: 1 });
  return `${title} (${head(a.element, a.unit, a.counts)}): партий ${int(a.total)}, ${failures(a.failed, a.total)}, записей без партии ${int(a.no_batch)}`;
}

/** Opens the tab on the section of the arguments; the assistant gets the section computed. */
export async function openQaqc(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const section = args.section as QaqcSection;
  if (!QAQC_SECTIONS.includes(section)) throw refusal("bad_param", `Раздел «${String(section)}» неизвестен.`, `Разделы: ${QAQC_SECTIONS.join(", ")}.`);
  const datas = await needDatas();
  if (typeof args.data_id === "number") {
    const id = args.data_id;
    if (!datas.some((d) => d.id === id)) throw refusal("qaqc_data_not_found", `Данные QAQC № ${id} не найдены.`, `Данные проекта: ${datas.map((d) => `${d.name} (№ ${d.id})`).join(", ")}.`);
    setData(id);
  }
  if (typeof args.element === "string" && args.element.trim()) {
    const want = args.element.trim();
    const data = activeData()!;
    if (!data.elements.some((e) => e.name.toLowerCase() === want.toLowerCase())) throw refusal("bad_param", `Элемента «${want}» нет в данных «${data.name}».`, `Элементы: ${data.elements.map((e) => e.name).join(", ") || "нет"}.`);
    setElement(want);
  }
  if (typeof args.kind === "string" && (DUP_KINDS as readonly string[]).includes(args.kind)) setDupKind(args.kind as DupKind);
  setSection(section);
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "qaqc") }));
  const data = activeData()!;
  if (source !== "claude") return `Открыта вкладка «QAQC» · ${SECTION_TITLES[section]} · ${data.name} · ${activeElement() ?? "нет элементов"}`;
  return sectionText(section);
}

/** Edits the filter of the tab; the assistant gets the conditions and the counter of the records. */
export async function filterQaqc(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const next = filterFromArgs(qaqcStore.get().filter, args);
  if (typeof next === "string") throw refusal("bad_param", next, "Условия фильтра — в описании команды qaqc.filter.");
  setFilter(next);
  const text = `Фильтр QAQC: ${filterSummary(next)}`;
  if (source !== "claude") return text;
  await needDatas();
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "qaqc") }));
  const body = calcBody();
  if (!body) return text;
  const a = await calc("summary", body);
  return `${text} · ${countLabel(a.counts.filtered, a.counts.rows)}`;
}

export function bindQaqc(app: App): void {
  const bindAll = () => {
    app.registry.bind("qaqc.open", noted(app, (a, source) => openQaqc(app, a, source)), qaqcReason);
    app.registry.bind("qaqc.filter", noted(app, (a, source) => filterQaqc(app, a, source)), qaqcReason);
    bindReports(app);
  };
  bindAll();
  // the reason «Загрузите контрольные пробы…» follows the list: the ribbon and Ctrl+K are told by a new binding
  let hasData: boolean | null = null;
  qaqcStore.subscribe(() => {
    const datas = qaqcStore.get().datas;
    const now = datas === null ? null : datas.length > 0;
    if (now === hasData) return;
    hasData = now;
    bindAll();
  });
  // another project: the tab starts again and reads the data of the new one
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetQaqc();
    resetReports();
    if (cur) void loadDatas().then(() => loadReports());
  });
  onCoreEvent((event, data) => {
    if (event === "qaqc.changed") return qaqcChanged();
    if (event !== "objects.changed") return;
    let kind: unknown;
    try {
      kind = (JSON.parse(data) as { kind?: unknown }).kind;
    } catch {
      return;
    }
    if (kind === "qaqc_data") void loadDatas().then(qaqcChanged);
    else if (kind === "qaqc_report") {
      reportsChanged();
      void loadReports();
    }
  });
}

// ---- the reports (ADR 0020 §7, §9, §10): `qaqc.report`, `report_open`, `report_save`, `report_rebuild`, `report_rename`, `report_delete` ----

export const REPORT_COMMANDS = ["qaqc.report", "qaqc.report_open", "qaqc.report_save", "qaqc.report_rebuild", "qaqc.report_rename", "qaqc.report_delete"] as const;

const reportId = (args: Record<string, unknown>): number => {
  const id = args.report_id;
  if (typeof id !== "number" || !Number.isInteger(id)) throw refusal("bad_param", "Не задан номер отчёта QAQC.", "Параметр report_id — номер из раздела дерева «Отчёты».");
  return id;
};

function openReportTab(app: App, id: number | null = null): void {
  setSection("report");
  if (id !== null) selectReport(id);
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "qaqc") }));
}

/** The answer of a run for the assistant: the report and its totals, or a refusal with the step it stopped at. */
async function runAnswer(run: ReportRun): Promise<string> {
  if (run.state === "cancelled") return `Формирование отчёта QAQC «${run.spec.name}» отменено`;
  if (run.state === "failed") throw refusal("report_failed", `Отчёт QAQC «${run.spec.name}» не сформирован: ${run.error ?? "ошибка"}.`, "Подробности — во вкладке «QAQC › Отчёт» и «Задания»; «Повторить» продолжит с этого шага.");
  const r = await getReport(run.reportId!);
  const totals = summaryText(r.summary);
  return `${reportLine(r)}${run.total ? `; графиков ${int(run.done - run.empty)} из ${int(run.total)}${run.omitted ? ` (сверх 200 — ${int(run.omitted)})` : ""}` : ""}${totals ? `. ${totals}` : ""}${run.path ? `. Сохранён: ${run.path}` : ""}`;
}

/** Starts a run: the person sees its steps in «QAQC › Отчёт»; the assistant waits for the end. */
async function startRun(app: App, spec: RunSpec, source: CommandSource): Promise<string> {
  openReportTab(app, spec.kind === "create" ? null : spec.reportId);
  if (source === "claude") return runAnswer(await runReport(spec));
  void runReport(spec);
  return spec.kind === "rebuild" ? `Перестраивается отчёт QAQC «${spec.name}»` : `Формируется отчёт QAQC «${spec.name}»`;
}

/** `qaqc.report`: the data (of the tab without `data_id`), the elements (all without them), the filter of the tab. */
export async function reportQaqc(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const datas = await needDatas();
  const data = typeof args.data_id === "number" ? datas.find((d) => d.id === args.data_id) : activeData();
  if (!data) throw refusal("qaqc_data_not_found", `Данные QAQC № ${String(args.data_id)} не найдены.`, `Данные проекта: ${datas.map((d) => `${d.name} (№ ${d.id})`).join(", ")}.`);
  let elements: string[] | undefined;
  if (args.elements !== undefined && args.elements !== "") {
    const list = listArg(args.elements);
    if (typeof list === "string") throw refusal("bad_param", `Параметр «elements»: ${list}.`, "Элементы — через запятую.");
    elements = [];
    for (const want of list) {
      const el = data.elements.find((e) => e.name.toLowerCase() === want.toLowerCase());
      if (!el) throw refusal("bad_param", `Элемента «${want}» нет в данных «${data.name}».`, `Элементы: ${data.elements.map((e) => e.name).join(", ") || "нет"}.`);
      if (!elements.includes(el.name)) elements.push(el.name);
    }
    if (!elements.length) elements = undefined;
  }
  const charts: ChartsMode = CHARTS_MODES.includes(args.charts as ChartsMode) ? (args.charts as ChartsMode) : "flagged";
  const name = typeof args.name === "string" && args.name.trim() ? args.name.trim() : undefined;
  const path = typeof args.path === "string" && args.path.trim() ? args.path.trim() : null;
  const body = { data_id: data.id, ...(name ? { name } : {}), ...(elements ? { elements } : {}), filter: filterJson(qaqcStore.get().filter), charts };
  return startRun(app, { kind: "create", body, name: name ?? `Отчёт QAQC — ${data.name}`, charts, path }, source);
}

async function openReport(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const r = await findReport(reportId(args));
  openReportTab(app, r.id);
  if (source !== "claude") return `Открыт отчёт QAQC «${r.name}»`;
  const full = await getReport(r.id);
  const totals = summaryText(full.summary);
  return `${reportLine(full)}; хеши: данные ${full.data_sha256.slice(0, 12)}…, входы ${full.inputs_sha256.slice(0, 12)}…${totals ? `. ${totals}` : ""}`;
}

async function saveReportFile(args: Record<string, unknown>): Promise<string> {
  if (args.kind === "pdf") throw refusal("not_available", "PDF отчёта QAQC появится на этапе Э22.", "Сохраните отчёт в XLSX (kind: xlsx).");
  const r = await getReport(reportId(args)).catch(() => findReport(reportId(args)));
  const path = await saveXlsx(r, typeof args.path === "string" ? args.path : null);
  return path ? `Отчёт QAQC «${r.name}» сохранён: ${path}` : "Отменено";
}

async function rebuildReport(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const r = await getReport(reportId(args)).catch(() => findReport(reportId(args)));
  const why = rebuildReason(r);
  if (why) throw refusal(r.stale === "source_deleted" ? "source_deleted" : "report_building", `${why}.`, "Сформируйте новый отчёт по текущим данным.");
  return startRun(app, { kind: "rebuild", reportId: r.id, name: r.name, charts: r.charts ?? "flagged" }, source);
}

async function renameReportCmd(args: Record<string, unknown>): Promise<string> {
  const r = await findReport(reportId(args));
  const name = typeof args.name === "string" ? args.name.trim() : "";
  if (!name) throw refusal("name_empty", "Название не может быть пустым.", "Введите название отчёта.");
  if (name === r.name) return `Отчёт QAQC уже называется «${name}»`;
  await renameReport(r.id, name, r.version);
  await loadReports();
  return `Отчёт QAQC «${r.name}» переименован: «${name}»`;
}

async function deleteReportCmd(args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const r = await findReport(reportId(args));
  if (source === "user" && !(await askConfirm({ title: "Удалить отчёт QAQC", message: `Отчёт «${r.name}» удаляется вместе с файлом XLSX, рисунками и копией записей. Данные QAQC не меняются.`, okLabel: "Удалить", danger: true })))
    return "Отменено";
  await deleteReport(r.id);
  if (reportsStore.get().selected === r.id) selectReport(null);
  await loadReports();
  return `Удалён отчёт QAQC «${r.name}»`;
}

function bindReports(app: App): void {
  const writable = needWritable(app);
  const withData = (ctx: CommandContext) => qaqcReason(ctx) ?? writable(ctx);
  app.registry.bind("qaqc.report", noted(app, (a, by) => reportQaqc(app, a, by)), withData);
  app.registry.bind("qaqc.report_open", noted(app, (a, by) => openReport(app, a, by)), (ctx) => (ctx.hasProject ? null : NEED_PROJECT));
  app.registry.bind("qaqc.report_save", noted(app, (a) => saveReportFile(a)), (ctx) => (ctx.hasProject ? null : NEED_PROJECT));
  app.registry.bind("qaqc.report_rebuild", noted(app, (a, by) => rebuildReport(app, a, by)), writable);
  app.registry.bind("qaqc.report_rename", noted(app, (a) => renameReportCmd(a)), writable);
  app.registry.bind("qaqc.report_delete", noted(app, (a, by) => deleteReportCmd(a, by)), writable);
}
