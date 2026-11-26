/**
 * The section «Проектные скважины» of the data tree (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29, № 103): the planned holes by name — a flat list, no branches;
 * the check box is «в 3D» (`planned.show`), a click chooses the hole (the layer draws it thicker; `planned.open`, `planned.evaluate`… without `id` take it),
 * a double click opens the window «Проектная скважина»; the menu — the commands of the registry, unavailable ones with the reason. The planned hole is
 * not a hole of a set: the section «Скважины» does not list it. «+» of the section — `planned.new` (by the orchestrator: `shell/treeSections.ts`).
 */
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { useSel } from "../estimation/hooks";
import type { PlannedRow } from "./plannedApi";
import { rowLine } from "./plannedModel";
import { choose, isShown, loadPlanned, plannedStore, plannedViewStore } from "./plannedState";

export const NODE_KIND = "object:planned_hole";

const icon = (name: string): IconName => name as IconName;

type Run = (id: string, args: Record<string, unknown>) => unknown;
type Reason = (id: string) => string | null;

/** The menu of a hole: the commands of the registry (the argument is the number of the hole). */
export function plannedMenu(r: Pick<PlannedRow, "id">, reason: Reason, run: Run, refresh: () => void): MenuItem[] {
  const cmd = (label: string, id: string, extra: Record<string, unknown> = {}): MenuItem => ({ label, reason: reason(id), run: () => void run(id, { id: r.id, ...extra }) });
  return [
    cmd("Открыть…", "planned.open"),
    cmd("Прогноз…", "planned.evaluate"),
    cmd("План / факт…", "planned.compare"),
    { separator: true, label: "" },
    cmd("Экспорт CSV…", "planned.export", { format: "csv" }),
    cmd("Экспорт DXF…", "planned.export", { format: "dxf" }),
    { separator: true, label: "" },
    { label: "Обновить список", run: refresh },
    cmd("Удалить…", "planned.delete"),
  ];
}

export function PlannedTree() {
  const app = useApp();
  const { rows, loaded, error } = useSel(plannedStore, (s) => s);
  const view = useSel(plannedViewStore, (s) => s);
  if (!rows.length) return <div className={tree.none}>{error ? `Не прочитано: ${error}` : loaded ? "Нет проектных скважин — «+» открывает окно «Проектная скважина»" : "Нет объектов"}</div>;
  const run: Run = (id, args) => app.run(id, args);
  const reason: Reason = (id) => app.registry.reason(id);
  return (
    <div role="group" aria-label="Проектные скважины" data-testid="tree-planned" data-holes={rows.length}>
      {rows.map((r) => (
        <TreeRow
          key={r.id}
          label={r.name}
          ariaLabel={`${r.name}: проектная скважина, длина ${r.meta.length} м`}
          level={1}
          selected={view.chosen === r.id}
          onSelect={() => choose(r.id)}
          onActivate={() => void run("planned.open", { id: r.id })}
          check={{ checked: isShown(view, r.id), label: `${r.name}: показать в 3D`, onChange: (on) => void run("planned.show", { id: r.id, visible: on }) }}
          title={`${rowLine(r)}\nГалочка — линия и метка цели в 3D и в разрезах; двойной щелчок — окно скважины`}
          testId={`tree-planned-${r.id}`}
          glyph={<Glyph icon={icon("planned")} />}
          after={<span className={tree.count}>{r.meta.target ? "цель" : r.meta.change ? "дуга" : "прямая"}</span>}
          onContextMenu={(e) => {
            e.preventDefault();
            choose(r.id);
            openMenu(e.clientX, e.clientY, plannedMenu(r, reason, run, () => void loadPlanned()));
          }}
        />
      ))}
    </div>
  );
}
