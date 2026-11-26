/**
 * What the tree and «Свойства» say of a pit scenario (ТЗ 8.11.5; ADR 0028 решения 20, 23, 29; docs/progress/e21-api.md §5, §6, §12): the label of a row, the facts of the result (the model and
 * its hash, the solver, the template K and the error of the angle, the error of the objective, the time, the warnings), the state («параметры изменены, нужен запуск»), and the menu — every item
 * is a command of the registry; one that cannot run says why. A gap is «—», never 0. Pure of React.
 */
import type { App } from "../../shell/app";
import type { MenuItem } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import type { Selected } from "../../shell/selection";
import { list, num, obj, readWarnings, str, type ScenarioDetail, type ScenarioRow } from "./answers";
import { warningText } from "./format";
import { DASH, formatNumber } from "./reportModel";

export const NODE_KIND = "object:pit_scenario";

/** The kind of the tree for `selected` (the shell's `ObjectKind` gets `pit_scenario` — by the orchestrator). */
export const SELECT_KIND = "pit_scenario";

/** The words of the contract `pit_no_result` (409): what a scenario without a result is told. */
export const NO_RESULT = { code: "pit_no_result", message: "У сценария нет результата.", hint: "Запустите расчёт сценария." } as const;
export const NO_RESULT_TEXT = `${NO_RESULT.message} ${NO_RESULT.hint}`;

export type Run = (id: string, args: Record<string, unknown>) => void;

/** The scenarios in the order of the tree: by name, the number breaks a tie. */
export const scenariosByName = <T extends Pick<ScenarioRow, "id" | "name">>(rows: readonly T[]): T[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);

/** The state of a row in words; empty — a result that is current. «Устарел» is the badge of the graph (Д-35), after the name. */
export function stateTitle(r: Pick<ScenarioRow, "state" | "has_result" | "params_changed">): string {
  if (r.state === "running") return "считается";
  if (r.state === "error") return "ошибка";
  if (!r.has_result) return "нет результата";
  return r.params_changed ? "параметры изменены" : "";
}

export const scenarioLabel = (r: Pick<ScenarioRow, "name" | "state" | "has_result" | "params_changed">): string => {
  const s = stateTitle(r);
  return s ? `${r.name} · ${s}` : r.name;
};

/** The tooltip of a row: the model, the shells, the value of the last shell, the state. */
export function rowTitle(r: ScenarioRow): string {
  const value = r.value === null ? "" : `; ценность последней оболочки ${formatNumber(r.value, 0)} $`;
  const shells = r.shells === null ? "" : `, оболочек ${r.shells}`;
  const state = stateTitle(r);
  return `Сценарий карьера «${r.name}» по модели «${r.model.name}»${shells}${value}${state ? `; ${state}` : ""}. Щелчок — свойства, двойной щелчок — pit-by-pit в нижней панели`;
}

/** Why a scenario cannot be rebuilt now; null — it can. */
export function rebuildReason(r: Pick<ScenarioRow, "state" | "has_result" | "graph">): string | null {
  if (r.graph.source_lost) return "Источник сценария удалён (модель, поле или рельеф): перестроить нельзя, результат остаётся";
  if (r.state === "running") return "Сценарий считается";
  if (!r.has_result) return "У сценария нет результата: запустите его командой «Запустить»";
  return null;
}

/** Why the report and the 3D of a shell are not available: the scenario has no result (the words of `pit_no_result`). */
export const noResultReason = (r: Pick<ScenarioRow, "state" | "has_result"> | undefined): string | null => (r && !r.has_result ? (r.state === "running" ? "Сценарий считается: результата ещё нет" : NO_RESULT_TEXT) : null);

// ---- the facts of a scenario ----

export interface Fact {
  key: string;
  label: string;
  value: string;
}

const short = (hash: string | null): string => (hash ? hash.slice(0, 12) : DASH);

const SOLVERS: Record<string, string> = { hpf: "HPF", ortools: "OR-Tools", auto: "HPF (авто)" };

const degrees = (v: number | null): string => (v === null ? DASH : `${formatNumber(v, 2)}°`);

/** The model of the result: the name and the hash of its cells (`cells_sha`: if the model changed, the result is «устарел»). */
function modelFact(meta: Record<string, unknown>, row?: Pick<ScenarioRow, "model">): string {
  const m = obj(meta.model);
  const name = str(m.name) ?? row?.model.name ?? DASH;
  return `«${name}»${str(m.cells_sha) ? `, хеш ${short(str(m.cells_sha))}` : ""}`;
}

/** The names of the fields of the result: «PIT» and «ORE» with the prefix of the scenario. */
export function pitField(d: Pick<ScenarioDetail, "meta" | "params">): string {
  const fields = list(obj(d.meta?.outputs).fields).filter((f): f is string => typeof f === "string");
  return fields.find((f) => f.endsWith("PIT")) ?? `${d.params.prefix}PIT`;
}

/** The number of the shells of the result (0 — no result). */
export const shellCount = (d: Pick<ScenarioDetail, "meta">): number => list(d.meta?.shells).length;

/** The facts of a scenario for «Свойства» and the tree: what the result was made of and how. A scenario with no result has the facts of its params only. */
export function scenarioFacts(d: ScenarioDetail, row?: ScenarioRow): Fact[] {
  const meta = d.meta ?? {};
  const out: Fact[] = [];
  const add = (key: string, label: string, value: string) => out.push({ key, label, value });
  add("model", "Модель", d.meta ? modelFact(meta, row) : row ? `«${row.model.name}»` : DASH);
  add("hash", "Параметры, хеш", short(d.params_hash));
  if (!d.meta) return out;
  const solver = str(meta.solver);
  if (solver) add("solver", "Решатель", SOLVERS[solver] ?? solver);
  const shells = list(meta.shells).map(obj);
  if (shells.length) {
    const rf = (i: number) => formatNumber(num(shells[i].rf), 2);
    add("shells", "Оболочки", `${shells.length}, RF ${rf(0)} … ${rf(shells.length - 1)}`);
  }
  const tpl = obj(meta.template);
  const k = num(tpl.k);
  if (k !== null) {
    const errs = list(tpl.domains).map((x) => obj(obj(x).error));
    const max = errs.map((e) => num(e.max)).filter((v): v is number => v !== null);
    add("template", "Шаблон", `K = ${k}${tpl.k_auto === true ? " (подобран автоматически)" : ""}, ошибка угла ${max.length ? `до ${degrees(Math.max(...max))}` : DASH}`);
  }
  const oe = num(meta.objective_error);
  if (oe !== null) add("objective", "Ошибка цели", `не больше ${formatNumber(oe, 2)} $ (округление до целых, q = ${num(meta.q) === null ? DASH : String(num(meta.q)).replace(".", ",")})`);
  const t = obj(meta.totals);
  if (num(t.ore_t) !== null || num(t.cf) !== null) {
    add("totals", "Итог (RF = 1)", `руда ${formatNumber(num(t.ore_t), 0)} т, пустая ${formatNumber(num(t.waste_t), 0)} т, вскрыша ${num(t.strip) === null ? "не определена" : formatNumber(num(t.strip), 2)}, CF ${formatNumber(num(t.cf), 0)} $`);
  }
  const total = num(obj(meta.seconds).total);
  if (total !== null) add("seconds", "Время расчёта", `${formatNumber(total, 1)} с`);
  const warnings = readWarnings(meta.warnings);
  if (warnings.length) add("warnings", "Предупреждения", warnings.map(warningText).join("\n"));
  return out;
}

// ---- the actions ----

/** The scenario is chosen: its «Свойства» show it (the selection of the shell is the object of the tree, `kind` — its own). */
export function selectScenarioRow(app: App, id: number): void {
  app.store.set({ selected: { kind: SELECT_KIND, id } as unknown as Selected });
  const w = app.store.get().workspace;
  if (!(w.right && w.rightTab === "properties")) void app.run("panel.set", { panel: "properties", open: true });
}

/** Asks the new name in our own window and renames the scenario. */
export async function askRename(r: Pick<ScenarioRow, "id" | "name">, run: Run): Promise<void> {
  const name = await askText({ title: "Переименовать сценарий карьера", label: "Название", initial: r.name, okLabel: "Переименовать" });
  if (name !== null && name.trim() && name.trim() !== r.name) run("pit.rename", { scenario_id: r.id, name: name.trim() });
}

/** The items of the menu of a scenario (the tree) and the buttons of its properties. */
export function scenarioActions(app: App, r: ScenarioRow): MenuItem[] {
  const reason = (id: string) => app.registry.reason(id);
  const run: Run = (id, args) => void app.run(id, args);
  const busy = r.state === "running" ? "Сценарий считается" : null;
  const result = noResultReason(r);
  const open = (command: string) => ({ reason: reason(command) ?? busy, run: () => run(command, { scenario_id: r.id }) });
  return [
    { label: "Pit-by-pit", reason: reason("pit.report") ?? result, run: () => run("pit.report", { scenario_id: r.id }) },
    { label: "Запустить", ...open("pit.run") },
    { label: "Проверить шаблон", ...open("pit.check") },
    { label: "Экономика…", ...open("pit.economics") },
    { label: "Породы…", ...open("pit.rocks") },
    { label: "Углы бортов…", ...open("pit.slopes") },
    { label: "Рельеф и ограничитель…", ...open("pit.relief") },
    { label: "Календарь…", ...open("pit.calendar") },
    { label: "Перестроить", reason: reason("lineage.rebuild") ?? rebuildReason(r), run: () => run("lineage.rebuild", { kind: NODE_KIND, id: r.id }) },
    { label: "Переименовать…", reason: reason("pit.rename") ?? busy, run: () => void askRename(r, run) },
    { label: "Удалить…", reason: reason("pit.delete") ?? busy, run: () => run("pit.delete", { scenario_id: r.id }) },
  ];
}
