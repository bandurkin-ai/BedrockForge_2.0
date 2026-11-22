/**
 * The command `stats.open` (ADR 0019 §7, §9; ТЗ 7.29): opens the bottom tab «Статистика» on an analysis, with the
 * set, the table and the component of the arguments (absent — kept; another set — its first table and
 * component). From the assistant the analysis is computed
 * at once with the forms of the tab and the command answers with its short text (a refusal of the core — with its
 * hint: more than 20 000 intervals — composite). An edit of the data or another project drops the results.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { noted, refusal } from "../../shell/objectRun";
import { setPanel } from "../../shell/workspace";
import { chartReason, chartSet } from "../charts/bind";
import { invalidateFields } from "../charts/api";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { STATS_BUTTONS, type StatsAnalysis } from "./commands";
import { analysisTitle, bootstrapText, declusText, isTabAnalysis, nscoreText } from "./forms";
import { localText, mapText, variogramText } from "./formsVario";
import { clearRuns, initialStats, runAnalysis, setAnalysis, setContext, statsDataChanged, statsStore } from "./state";

const text = (v: unknown): string | null | undefined => (v === undefined ? undefined : v === null || v === "" ? null : String(v));

/** Opens the tab on the analysis of the arguments; the assistant gets the result of the calculation. */
export async function openStats(app: App, args: Record<string, unknown>, source: CommandSource): Promise<string> {
  const analysis = args.analysis as StatsAnalysis;
  const stage = STATS_BUTTONS.find((b) => b.analysis === analysis)?.stage;
  if (stage || !isTabAnalysis(analysis)) throw refusal("not_available", stage ? `«${analysisTitle(analysis)}» появится на этапе ${stage}.` : `Анализ «${String(analysis)}» неизвестен.`, "Вкладка считает declus — декластеризацию, nscore — нормальные оценки, bootstrap — бутстрап, variogram — вариограмму, map — карту вариограммы, local — карту неопределённости.");
  const set = chartSet(args);
  setContext({ ...(typeof args.set_id === "number" ? { setId: args.set_id } : {}), ...(args.table !== undefined ? { table: text(args.table) } : {}), ...(args.component !== undefined ? { component: text(args.component) } : {}) });
  setAnalysis(analysis);
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "stats") }));
  if (source !== "claude") return `Открыта вкладка «Статистика» · ${analysisTitle(analysis)} · ${set.name}`;
  if (analysis === "declus") return declusText(await runAnalysis("declus"));
  if (analysis === "nscore") return nscoreText(await runAnalysis("nscore"));
  if (analysis === "variogram") {
    const r = await runAnalysis("variogram");
    return r.data ? variogramText(r.data) : "Вариограмма: нет данных.";
  }
  if (analysis === "map") return mapText(await runAnalysis("map"));
  if (analysis === "local") return localText(await runAnalysis("local"));
  return bootstrapText(await runAnalysis("bootstrap"));
}

export function bindStats(app: App): void {
  app.registry.bind("stats.open", noted(app, (a, source) => openStats(app, a, source)), chartReason);
  // another project: the tab starts again (the forms stay — they are of the person, not of the data)
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    const { forms, analysis } = statsStore.get();
    statsStore.set({ ...initialStats(), forms, analysis, rev: statsStore.get().rev + 1 });
    clearRuns();
  });
  onCoreEvent((event, data) => {
    if (event !== "drillholes.changed" && event !== "objects.changed") return;
    let setId: number | undefined;
    try {
      const d = JSON.parse(data) as { set_id?: unknown; kind?: unknown };
      if (event === "objects.changed" && d.kind === "chart") return; // a saved chart: the data are the same
      if (event === "drillholes.changed" && typeof d.set_id === "number") setId = d.set_id;
    } catch {
      /* a malformed event: as an edit of every set */
    }
    invalidateFields(setId); // whichever listener comes first, the tab reads the fields again
    statsDataChanged(setId);
  });
}
