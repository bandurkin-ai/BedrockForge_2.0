/**
 * The section «Параметры оценки» of the data tree (ТЗ 8.8.4; ADR 0024 §10): two groups — the rows (domain × field × method of one block
 * model) and the plans (rows in the order of the run), each by name. A row: «устарел» with its reason (a changed table, a changed
 * model of variogram, «источник удалён»); a double click or Enter opens it in the wizard; the menu: «Изменить», «Выполнить»,
 * «Удалить…». A plan: a double click opens the window «План оценки»; the menu: «Открыть», «Выполнить», «Удалить…» — the
 * commands of the registry. The lists are read by `state.ts` (the project, `geostat.changed`). The section itself, its title and
 * «+» — by the orchestrator (shell/treeSections.ts, shell/DataTree.tsx, shell/workspace.ts).
 */
import { useMemo, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { blockById } from "../blocks/state";
import { useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { METHOD_SHORT, METHOD_TITLES, type EstimationRow, type PlanRow } from "./api";
import { useSel } from "./hooks";
import { domainText, fieldsText, lastRunText, rowLine, stateText } from "./planView";
import { estimationStore } from "./state";

type Run = (id: string, args: Record<string, unknown>) => void | Promise<unknown>;
type Reason = (id: string) => string | null;

/** The rows by name, as the core lists them. */
export const rowsByName = (list: readonly EstimationRow[]): EstimationRow[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));
export const plansByName = (list: readonly PlanRow[]): PlanRow[] => [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));

const modelName = (id: number): string => blockById(id)?.name ?? `модель №${id}`;

/** The tooltip of a row: the method, the domain, the fields, the state, the last run, the way to open it. */
export function rowTitleText(r: EstimationRow): string {
  const parts = [`«${r.name}»: ${METHOD_TITLES[r.method]}`, `Модель «${modelName(r.model_id)}», домен: ${domainText(r)}, поля ${fieldsText(r)}`];
  const st = stateText(r);
  if (st) parts.push(st[0].toUpperCase() + st.slice(1));
  const last = lastRunText(r);
  parts.push(last ? `Последний запуск ${last}` : "Не выполнялась");
  if (r.plans.length) parts.push(`В планах: ${r.plans.length}`);
  parts.push("Двойной щелчок — изменить в мастере «Оценка содержаний»");
  return parts.join("\n");
}

/** The tooltip of a plan. */
export const planTitleText = (p: PlanRow): string => `План «${p.name}»: строк ${p.items.length}, модель «${modelName(p.model_id)}»\nДвойной щелчок — открыть в окне «План оценки»`;

/** The items of the menu of a row: each runs its command (unavailable — with the reason). */
export function rowMenu(r: Pick<EstimationRow, "id" | "source_lost">, reason: Reason, run: Run): MenuItem[] {
  return [
    { label: "Изменить", reason: reason("estimate.open"), run: () => void run("estimate.open", { id: r.id }) },
    { label: "Параметры…", reason: reason("estimate.params"), run: () => void run("estimate.params", { id: r.id }) },
    { label: "Выполнить", reason: reason("estimate.run") ?? (r.source_lost ? "Источник удалён: строку нельзя выполнить" : null), run: () => void run("estimate.run", { estimation_ids: String(r.id) }) },
    { label: "Удалить…", reason: reason("estimate.delete"), run: () => void run("estimate.delete", { id: r.id }) },
  ];
}

/** The items of the menu of a plan. */
export function planMenu(p: Pick<PlanRow, "id" | "items">, reason: Reason, run: Run): MenuItem[] {
  return [
    { label: "Открыть", reason: reason("estimate.plan"), run: () => void run("estimate.plan", { plan_id: p.id }) },
    { label: "Выполнить", reason: reason("estimate.run") ?? (p.items.length ? null : "В плане нет строк"), run: () => void run("estimate.run", { plan_id: p.id }) },
    { label: "Удалить…", reason: reason("estimate.plan_delete"), run: () => void run("estimate.plan_delete", { id: p.id }) },
  ];
}

export function EstimationTree() {
  const app = useApp();
  const { rows, plans, loaded } = useSel(estimationStore, (s) => s);
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState({ rows: true, plans: true });
  const sortedRows = useMemo(() => rowsByName(rows), [rows]);
  const sortedPlans = useMemo(() => plansByName(plans), [plans]);
  const run: Run = (id, args) => app.run(id, args);
  const reason: Reason = (id) => app.registry.reason(id);
  useWatch(rows.map((r) => ({ kind: "object:estimation", id: r.id }))); // the states of the whole section in one request (Д-35)
  if (!rows.length && !plans.length) return <div className={tree.none}>{loaded ? "Нет строк — «+» открывает мастер «Оценка содержаний»" : "Нет объектов"}</div>;
  const rowMenuAt = (r: EstimationRow, e: MouseEvent) => {
    e.preventDefault();
    setSelected(`row-${r.id}`);
    openMenu(e.clientX, e.clientY, rowMenu(r, reason, run));
  };
  const planMenuAt = (p: PlanRow, e: MouseEvent) => {
    e.preventDefault();
    setSelected(`plan-${p.id}`);
    openMenu(e.clientX, e.clientY, planMenu(p, reason, run));
  };
  return (
    <div role="group" aria-label="Параметры оценки">
      <TreeRow label="Строки" level={2} expanded={open.rows} onToggle={() => setOpen((o) => ({ ...o, rows: !o.rows }))} after={<span className={tree.count}>{rows.length}</span>} testId="tree-est-rows" />
      {open.rows &&
        sortedRows.map((r) => {
          return (
            <TreeRow
              key={r.id}
              label={r.name}
              ariaLabel={`${r.name}: ${rowLine(r)}`}
              level={3}
              selected={selected === `row-${r.id}`}
              onSelect={() => setSelected(`row-${r.id}`)}
              onActivate={() => void run("estimate.open", { id: r.id })}
              title={rowTitleText(r)}
              testId={`tree-est-row-${r.id}`}
              glyph={<Glyph icon={"blocks" as IconName} />}
              after={
                <>
                  <LineageBadge node={{ kind: "object:estimation", id: r.id }} />
                  <span className={tree.count}>{METHOD_SHORT[r.method]}</span>
                </>
              }
              onContextMenu={(e) => rowMenuAt(r, e)}
            />
          );
        })}
      <TreeRow label="Планы" level={2} expanded={open.plans} onToggle={() => setOpen((o) => ({ ...o, plans: !o.plans }))} after={<span className={tree.count}>{plans.length}</span>} testId="tree-est-plans" />
      {open.plans &&
        sortedPlans.map((p) => (
          <TreeRow
            key={p.id}
            label={p.name}
            ariaLabel={`План ${p.name}: строк ${p.items.length}`}
            level={3}
            selected={selected === `plan-${p.id}`}
            onSelect={() => setSelected(`plan-${p.id}`)}
            onActivate={() => void run("estimate.plan", { plan_id: p.id })}
            title={planTitleText(p)}
            testId={`tree-est-plan-${p.id}`}
            glyph={<Glyph icon={"list" as IconName} />}
            after={<span className={tree.count}>{p.items.length}</span>}
            onContextMenu={(e) => planMenuAt(p, e)}
          />
        ))}
    </div>
  );
}
