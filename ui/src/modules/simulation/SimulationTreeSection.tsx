/**
 * The section «Моделирование» of the data tree (ТЗ 8.8.1; ADR 0024 §9): the rows of the simulations (SGS, SIS, co-SGS) of the project by
 * name. A row: the kind and the realizations of the last run («5 реализаций»), «устарел» with its reason (a changed table, a changed model
 * of variogram, «источник удалён»), «не выполнялось»; a double click or Enter opens it in the wizard; the menu: «Изменить», «Выполнить»,
 * «Сводка реализаций…», «Постобработка…», «Удалить…» — the commands of the registry. The list is read by `state.ts` (the project,
 * `geostat.changed`). The section itself, its title and «+» — by the orchestrator (shell/treeSections.ts, shell/DataTree.tsx,
 * shell/workspace.ts).
 */
import { useMemo, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { blockById } from "../blocks/state";
import { useSel } from "../estimation/hooks";
import { useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { KIND_SHORT, KIND_TITLES, type SimulationRow } from "./api";
import { rowsByName, simulationStore } from "./state";

type Run = (id: string, args: Record<string, unknown>) => void | Promise<unknown>;
type Reason = (id: string) => string | null;

const modelName = (id: number): string => blockById(id)?.name ?? `модель №${id}`;

/** «Состояние» of a row in words: stale (with the reason), source lost; "" — current. */
export function stateText(r: Pick<SimulationRow, "stale" | "stale_reason" | "source_lost">): string {
  if (r.source_lost) return "источник удалён";
  if (r.stale) return r.stale_reason ? `устарел: ${r.stale_reason}` : "устарел";
  return "";
}

/** «5 реализаций · 200 узлов» / «не выполнялось». */
export function lastRunText(r: Pick<SimulationRow, "last_run">): string {
  if (!r.last_run) return "не выполнялось";
  const n = r.last_run.realizations;
  const word = n % 10 === 1 && n % 100 !== 11 ? "реализация" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "реализации" : "реализаций";
  return `${n.toLocaleString("ru-RU")} ${word} · ${r.last_run.nodes.toLocaleString("ru-RU")} узлов`;
}

/** The tooltip of a row: the kind, the model and the domain, the field, the state, the last run, the way to open it. */
export function rowTitleText(r: SimulationRow): string {
  const parts = [`«${r.name}»: ${KIND_TITLES[r.kind]}`, `Модель «${modelName(r.model_id)}»${r.label ? `, домен «${r.label}»` : ""}, поле ${r.field}; ${r.n_real} реализаций, зерно ${r.seed}`];
  const st = stateText(r);
  if (st) parts.push(st[0].toUpperCase() + st.slice(1));
  parts.push(`Последний запуск: ${lastRunText(r)}`);
  parts.push("Двойной щелчок — изменить в мастере «Моделирование»");
  return parts.join("\n");
}

/** The items of the menu of a row: each runs its command (unavailable — with the reason). */
export function rowMenu(r: Pick<SimulationRow, "id" | "source_lost" | "last_run">, reason: Reason, run: Run): MenuItem[] {
  return [
    { label: "Изменить", reason: reason("simulate.open"), run: () => void run("simulate.open", { id: r.id }) },
    { label: "Выполнить", reason: reason("simulate.run") ?? (r.source_lost ? "Источник удалён: строку нельзя выполнить" : null), run: () => void run("simulate.run", { id: r.id }) },
    { label: "Сводка реализаций…", reason: reason("simulate.summary"), run: () => void run("simulate.summary", { id: r.id }) },
    { label: "Постобработка…", reason: reason("simulate.post") ?? (r.last_run ? null : "Нет реализаций: сначала выполните моделирование"), run: () => void run("simulate.post", { id: r.id }) },
    { label: "Удалить…", reason: reason("simulate.delete"), run: () => void run("simulate.delete", { id: r.id }) },
  ];
}

export function SimulationTree() {
  const app = useApp();
  const { rows, loaded } = useSel(simulationStore, (s) => s);
  const [selected, setSelected] = useState<number | null>(null);
  const sorted = useMemo(() => rowsByName(rows), [rows]);
  const run: Run = (id, args) => app.run(id, args);
  const reason: Reason = (id) => app.registry.reason(id);
  useWatch(rows.map((r) => ({ kind: "object:simulation", id: r.id }))); // the states of the whole section in one request (Д-35)
  if (!rows.length) return <div className={tree.none}>{loaded ? "Нет строк — «+» открывает мастер «Моделирование»" : "Нет объектов"}</div>;
  const menuAt = (r: SimulationRow, e: MouseEvent) => {
    e.preventDefault();
    setSelected(r.id);
    openMenu(e.clientX, e.clientY, rowMenu(r, reason, run));
  };
  return (
    <div role="group" aria-label="Моделирование">
      {sorted.map((r) => (
        <TreeRow
          key={r.id}
          label={r.name}
          ariaLabel={`${r.name}: ${KIND_SHORT[r.kind]}, ${lastRunText(r)}${stateText(r) ? `, ${stateText(r)}` : ""}`}
          level={2}
          selected={selected === r.id}
          onSelect={() => setSelected(r.id)}
          onActivate={() => void run("simulate.open", { id: r.id })}
          title={rowTitleText(r)}
          testId={`tree-sim-row-${r.id}`}
          glyph={<Glyph icon={"domains" as IconName} />}
          after={
            <>
              <LineageBadge node={{ kind: "object:simulation", id: r.id }} />
              <span className={tree.count}>{KIND_SHORT[r.kind]}</span>
            </>
          }
          onContextMenu={(e) => menuAt(r, e)}
        />
      ))}
    </div>
  );
}
