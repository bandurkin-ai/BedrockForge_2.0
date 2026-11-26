/**
 * The handlers of `pit.report` (the panel «Pit-by-pit» of the bottom dock) and `pit.show` (the blocks of the model up to a shell in the 3D) and what keeps the panel in step with the project
 * (ТЗ 8.11.4–8.11.5; ADR 0028 решения 18, 19, 29; docs/progress/e21-api.md §5, §7, §16). Both are unavailable while the scenario has no result — the hint is the contract's `pit_no_result`
 * («У сценария нет результата. Запустите расчёт сценария.»). `pit.show` is the display of Э17: the quick restrictor of the model `PIT <= n` (the field `<prefix>PIT` of the result) and the model
 * shown — as `blocks.quick` and `blocks.show` do; it is kept in the view of the project and is dropped by `blocks.quick` with `clear` (it replaces the restrictor the model had). The panel
 * follows `pit.changed` (a scenario run again or renamed is read again, a deleted one leaves) and another project clears it. One app at a time (the tests make several): the earlier binding goes.
 * `bindPitPanel(app)` is called by `bindPit` (`bind.ts`).
 */
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import { needProject, noted, refusal } from "../../shell/objectRun";
import { BLOCK_STYLES_KEY, revealOnShow, styleIn, styleOp } from "../blocks/display";
import { blockById } from "../blocks/state";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { num, obj } from "./answers";
import { asError, freshScenario, scenarioIdOf } from "./bind";
import type { Args } from "./forms";
import { openReport, pitPanelStore, resetPanel, scenarioChanged, showTab } from "./panelState";
import { reportText } from "./reportModel";
import { noResultReason, NO_RESULT, pitField, shellCount } from "./scenarioModel";
import { currentScenarioId, pitEvent, pitStore, scenarioById } from "./state";

/** The refusal of a scenario that has no result (409 `pit_no_result`), or of one that is running; null — it has one. */
export function noResultRefusal(row: Parameters<typeof noResultReason>[0]): CoreError | null {
  if (!row || row.has_result) return null;
  if (row.state === "running") return refusal("pit_running", "Сценарий считается: результата ещё нет.", "Дождитесь конца расчёта во вкладке «Задания» и повторите команду.");
  return refusal(NO_RESULT.code, NO_RESULT.message, NO_RESULT.hint);
}

/**
 * Why `pit.report` and `pit.show` cannot run now: the scenario they work on (the one of the tree or of the windows) has no result; null — they can. With no scenario at hand they are
 * available: a call with `scenario_id` (the assistant has no tree) names its own, a call without it is refused in words by the handler («Не задан сценарий карьера»).
 */
export function resultReason(app: App): string | null {
  const id = currentScenarioId(app);
  return id === null ? null : noResultReason(scenarioById(id));
}

/** `pit.report {scenario_id?}`: the report of the scenario is read, the bottom dock opens on «Pit-by-pit»; the answer is the text of the totals and of the NPV. */
export async function runReport(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  const none = noResultRefusal(scenarioById(id));
  if (none) throw none;
  showTab(app);
  const report = await openReport(id);
  if (!report) {
    const e = pitPanelStore.get().error;
    throw asError(e ?? { code: "report_not_shown", message: "Отчёт pit-by-pit не прочитан.", hint: "Повторите команду; если не получится — запустите расчёт сценария заново.", param: null });
  }
  return reportText(report);
}

const shellOf = (v: unknown): number | null => {
  const n = typeof v === "string" ? Number(v.trim().replace(",", ".")) : v;
  return typeof n === "number" && Number.isInteger(n) && n >= 1 ? n : null;
};

/** `pit.show {scenario_id?, shell}`: the blocks of the model with `PIT <= shell` in the 3D (the quick restrictor of the model, the model shown). */
export async function runShow(app: App, a: Args): Promise<string> {
  const id = scenarioIdOf(app, a);
  const shell = shellOf(a.shell);
  if (shell === null) throw refusal("bad_param", "Номер оболочки — целое число от 1.", "Укажите shell: номер оболочки 1…N (значение поля PIT); номера — в таблице pit-by-pit.");
  const none = noResultRefusal(scenarioById(id));
  if (none) throw none;
  const detail = await freshScenario(id);
  if (!detail.has_result) throw refusal(NO_RESULT.code, NO_RESULT.message, NO_RESULT.hint);
  const n = shellCount(detail);
  if (n > 0 && shell > n) throw refusal("bad_param", `Оболочки №${shell} нет: у сценария «${detail.name}» их ${n}.`, `Номера оболочек — от 1 до ${n}.`);
  const modelId = detail.params.model_id ?? num(obj(detail.meta?.model).id);
  const model = modelId === null ? undefined : blockById(modelId);
  if (!model) throw refusal("block_model_not_found", `Блочная модель сценария${modelId === null ? "" : ` №${modelId}`} не найдена в проекте.`, "Номера моделей — в разделе «Блочные модели» дерева; если модель удалена, результат сценария показать нельзя.");
  const field = pitField(detail);
  if (!model.fields.some((f) => f.name === field)) throw refusal("block_field_not_found", `В модели «${model.name}» нет поля ${field}.`, "Поле пишет расчёт сценария: запустите его заново (команда «Запустить») или «Перестроить».");
  const before = styleIn(viewStore.get(), model.id);
  if (!before.visible) revealOnShow.add(model.id);
  updateView(BLOCK_STYLES_KEY, styleOp(model.id, { visible: true, quick: { items: [{ field, op: ">=", value: 1 }, { field, op: "<=", value: shell }], join: "and", invert: false } }));
  return `В 3D показаны блоки модели «${model.name}» с ${field} ≤ ${shell}${n ? ` (оболочка ${shell} из ${n})` : ""}. Быстрый ограничитель модели заменён этим условием; снять — команда «Быстрый ограничитель» с clear.`;
}

/** `pit.changed {kind: "pit_scenario", id, deleted?}`: the scenario the panel shows is read again, or leaves the panel when it is deleted; another scenario is not touched. */
export function onPitPanelEvent(event: string, data: string): void {
  if (event !== "pit.changed" || !projectStore.get().current) return;
  const e = pitEvent(data);
  const shown = pitPanelStore.get().scenarioId;
  if (shown !== null && (e.id === null || e.id === shown)) scenarioChanged(shown, e.deleted);
}

let unbindPrevious: (() => void) | null = null;

/** The commands and the subscriptions; the function that takes them off is kept: another call (another app) takes the earlier one off first. */
export function bindPitPanel(app: App): void {
  const previous = unbindPrevious;
  unbindPrevious = null;
  previous?.();
  const r = app.registry;
  const available = (ctx: Parameters<typeof needProject>[0]) => needProject(ctx) ?? resultReason(app);
  r.bind("pit.report", noted(app, (a) => runReport(app, a)), available);
  r.bind("pit.show", noted(app, (a) => runShow(app, a)), available);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  let ended = pitStore.get().run.seq;
  const offs = [
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetPanel();
    }),
    // a scenario run again or renamed is read again; a deleted one leaves the panel
    onCoreEvent(onPitPanelEvent),
    // a scenario that is chosen in the tree and is gone from the list (deleted here or elsewhere) is not chosen any more
    pitStore.subscribe(() => {
      const sel = app.store.get().selected as { kind: string; id: number } | null;
      if (sel?.kind === "pit_scenario" && pitStore.get().scenariosLoaded && !scenarioById(sel.id)) app.store.set({ selected: null });
    }),
    // a run that ends: the panel shows its report when it shows nothing or the same scenario (the dock is not opened: «Pit-by-pit» of the ribbon does that)
    pitStore.subscribe(() => {
      const run = pitStore.get().run;
      if (run.phase !== "done" || run.seq === ended || run.scenarioId === null) return;
      ended = run.seq;
      const shown = pitPanelStore.get().scenarioId;
      if (shown === null || shown === run.scenarioId) void openReport(run.scenarioId);
    }),
  ];
  unbindPrevious = () => offs.forEach((off) => off());
}
