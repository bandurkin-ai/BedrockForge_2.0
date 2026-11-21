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
import { noted, refusal } from "../../shell/objectRun";
import { setPanel } from "../../shell/workspace";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { calc, listReports, type CalcCounts, type QaqcData } from "./api";
import { DUP_KINDS, DUP_TITLES, QAQC_SECTIONS, SECTION_TITLES, type DupKind, type QaqcSection } from "./commands";
import { countLabel, filterFromArgs, filterSummary } from "./filters";
import { int, pct, share } from "./format";
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
    if (cur) void loadDatas();
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
    else if (kind === "qaqc_report") reportsChanged();
  });
}
