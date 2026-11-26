/**
 * The section «Карьер» of the data tree, its part «Сценарии» (ТЗ 8.11.5; ADR 0028 решения 23, 29; Д-35): the scenarios by name, «считается», «ошибка», «нет результата» and
 * «параметры изменены» marked, the badge of the graph after the name («устарел» with the chain of the causes, «источник удалён», «закреплён»); a click chooses the scenario (its
 * «Свойства»), a double click or Enter shows its pit-by-pit in the bottom tab (`pit.report`); the menu — the commands of the registry (run, «Перестроить», rename, delete — the question
 * «Влияние изменения» is the command's). Under an open scenario: its parameters (the model and its hash, the template K, the error of the angle) and its children — the DTMs of the shells
 * and the solids of the pit (what the graph built from the scenario; a click chooses the wireframe). The list is the core's (`pitStore.scenarios`, read again on `pit.changed`).
 * The shell puts `<PitTree />` into the section «Карьер».
 */
import { Fragment, useEffect, useMemo, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { getImpact, type Dependent } from "../lineage/api";
import { useSelect, usePulled, useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { projectStore } from "../projects/state";
import type { ScenarioRow } from "./answers";
import { loadScenario } from "./api";
import { NODE_KIND, rowTitle, scenarioActions, scenarioFacts, scenarioLabel, scenariosByName, selectScenarioRow } from "./scenarioModel";
import { loadScenarios, pitStore, selectScenario } from "./state";

/** The DTMs of the shells and the solids of the pit: the wireframes built from the scenario (`graph.record`, depth 1 of the impact). */
export const childrenOf = (nodes: readonly Dependent[]): Dependent[] => nodes.filter((n) => n.kind === "wireframe" && n.depth === 1);

/** The rows under an open scenario: the facts of its result and the wireframes built from it. */
export function ScenarioChildren({ row, level }: { row: ScenarioRow; level: number }) {
  const app = useApp();
  const key = `${row.id}:${row.version}:${row.has_result}:${row.job_id ?? ""}`;
  const detail = usePulled(() => loadScenario(row.id), `d:${key}`);
  const impact = usePulled(() => getImpact({ kind: NODE_KIND, id: row.id }), `i:${row.id}`);
  const facts = detail.data ? scenarioFacts(detail.data, row).filter((f) => ["model", "hash", "template"].includes(f.key)) : [];
  const wires = impact.data ? childrenOf(impact.data.nodes) : [];
  return (
    <>
      {facts.map((f) => (
        <TreeRow key={f.key} label={`${f.label}: ${f.value}`} level={level} title={`${f.label}: ${f.value}`} testId={`tree-pit-${row.id}-${f.key}`} />
      ))}
      {detail.error && <TreeRow label={`Не прочитано: ${detail.error}`} level={level} testId={`tree-pit-${row.id}-error`} />}
      {wires.map((w) => (
        <TreeRow
          key={`w${w.id}`}
          label={w.name || `Каркас №${w.id}`}
          level={level}
          title={`Каркас или ЦТМ «${w.name}», построенный из сценария. Щелчок — выбрать`}
          testId={`tree-pit-${row.id}-wireframe-${w.id}`}
          glyph={<Glyph icon="wireframe" />}
          onSelect={() => void app.run("object.select", { kind: "wireframe", id: w.id })}
        />
      ))}
      {impact.data && !wires.length && <TreeRow label="Каркасов и ЦТМ пока нет" level={level} testId={`tree-pit-${row.id}-none`} />}
    </>
  );
}

export function ScenarioRows({ level = 3 }: { level?: number }) {
  const app = useApp();
  const rows = useSelect(pitStore, (s) => s.scenarios);
  const loaded = useSelect(pitStore, (s) => s.scenariosLoaded);
  const selected = useSelect(app.store, (s) => s.selected as { kind: string; id: number } | null);
  const uid = useSelect(projectStore, (s) => s.current?.uid ?? null);
  const [open, setOpen] = useState<number[]>([]);
  const sorted = useMemo(() => scenariosByName(rows), [rows]);
  useEffect(() => {
    if (uid && !loaded) void loadScenarios();
  }, [uid, loaded]);
  useWatch(useMemo(() => sorted.map((r) => ({ kind: NODE_KIND, id: r.id })), [sorted])); // the states of the whole part in one request
  if (!sorted.length) return <div className={tree.none}>{loaded ? "Нет сценариев — «Новый сценарий» в ленте «Карьер»" : "Чтение сценариев…"}</div>;
  // the menu opens when the scenario is read: «Запустить» is judged by its readiness, which is not known before
  const menu = async (r: ScenarioRow, e: MouseEvent) => {
    e.preventDefault();
    const { clientX, clientY } = e;
    selectScenarioRow(app, r.id);
    await selectScenario(r.id);
    openMenu(clientX, clientY, scenarioActions(app, r));
  };
  const toggle = (id: number) => setOpen((o) => (o.includes(id) ? o.filter((x) => x !== id) : [...o, id]));
  return (
    <div role="group" aria-label="Сценарии карьера">
      {sorted.map((r) => {
        const on = open.includes(r.id);
        return (
          <Fragment key={r.id}>
            <TreeRow
              label={scenarioLabel(r)}
              ariaLabel={r.name}
              level={level}
              expanded={on}
              onToggle={() => toggle(r.id)}
              selected={selected?.kind === "pit_scenario" && selected.id === r.id}
              onSelect={() => selectScenarioRow(app, r.id)}
              onActivate={() => void app.run("pit.report", { scenario_id: r.id })}
              title={rowTitle(r)}
              testId={`tree-pit-scenario-${r.id}`}
              glyph={<Glyph icon="report" />}
              after={<LineageBadge node={{ kind: NODE_KIND, id: r.id }} hideCurrent fallback={r.graph.stale || r.graph.source_lost ? <span className={tree.count}>{r.graph.source_lost ? "источник удалён" : "устарел"}</span> : null} />}
              onContextMenu={(e) => void menu(r, e)}
            />
            {on && <ScenarioChildren row={r} level={level + 1} />}
          </Fragment>
        );
      })}
    </div>
  );
}

/** «Сценарии» with its scenarios: the part of the section «Карьер» that opens and closes by its arrow. */
export function PitTree() {
  const count = useSelect(pitStore, (s) => s.scenarios.length);
  const [open, setOpen] = useState(true);
  return (
    <>
      <TreeRow label="Сценарии" level={2} expanded={open} onToggle={() => setOpen((o) => !o)} after={<span className={tree.count}>{count}</span>} testId="tree-pit-scenarios" glyph={<Glyph icon="report" />} />
      {open && <ScenarioRows level={3} />}
    </>
  );
}
