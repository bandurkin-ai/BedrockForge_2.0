/**
 * The part «Отчёты по ресурсам» of the section «Отчёты» of the data tree (ТЗ 8.10; ADR 0027 решение 14, Д-35): the saved reports by name, «строится» and «ошибка» marked, the
 * badge of the graph after the name («устарел» with the chain of the causes, «источник удалён», «закреплён»); a click chooses the report (its «Свойства»), a double click or
 * Enter shows it in the bottom tab «Ресурсы» (`resources.open`); the menu — the commands of the registry: show, save XLSX / CSV, rebuild, rename, delete (the question
 * «Влияние изменения» is asked by the command). The list is the core's (`resourcesStore.reports`, read again on `resources.changed`). The shell puts the part into the section:
 * `<ResourceReportsTree />` after the QAQC reports.
 */
import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { projectStore } from "../projects/state";
import type { ReportListRow } from "./answers";
import { fmt } from "./format";
import { NODE_KIND, reportActions, reportLabel, reportsByName, selectReport } from "./reportActions";
import { useSel } from "./ReportParts";
import { loadReports, resourcesStore } from "./state";

/** The tooltip of a row: the model, the total of the first cut-off, the state. */
export function rowTitle(r: ReportListRow): string {
  const t = r.totals;
  const total = t ? `; итог: ${fmt(t.tonnes)} т${t.blocks ? `, блоков ${t.blocks}` : ""}` : "";
  const state = r.state === "building" ? "; строится" : r.state === "error" ? "; не построен" : "";
  return `Отчёт по ресурсам «${r.name}» по модели «${r.model.name}»${total}${state}. Щелчок — свойства, двойной щелчок — показать в панели «Ресурсы»`;
}

export function ResourceReportRows({ level = 3 }: { level?: number }) {
  const app = useApp();
  const reports = useSel(resourcesStore, (s) => s.reports);
  const loaded = useSel(resourcesStore, (s) => s.reportsLoaded);
  const selected = useSel(app.store, (s) => s.selected as { kind: string; id: number } | null);
  const uid = useSel(projectStore, (s) => s.current?.uid ?? null);
  const rows = useMemo(() => reportsByName(reports), [reports]);
  useEffect(() => {
    if (uid && !loaded) void loadReports();
  }, [uid, loaded]);
  useWatch(useMemo(() => rows.map((r) => ({ kind: NODE_KIND, id: r.id })), [rows])); // the states of the whole part in one request
  if (!rows.length) return <div className={tree.none}>{loaded ? "Нет отчётов по ресурсам — «Отчёт по ресурсам» в ленте «Блочные модели»" : "Чтение отчётов…"}</div>;
  const menu = (r: ReportListRow, e: MouseEvent) => {
    e.preventDefault();
    selectReport(app, r.id);
    openMenu(e.clientX, e.clientY, reportActions(app, r));
  };
  return (
    <div role="group" aria-label="Отчёты по ресурсам">
      {rows.map((r) => (
        <TreeRow
          key={r.id}
          label={reportLabel(r)}
          ariaLabel={r.name}
          level={level}
          selected={selected?.kind === "resource_report" && selected.id === r.id}
          onSelect={() => selectReport(app, r.id)}
          onActivate={() => void app.run("resources.open", { report_id: r.id })}
          title={rowTitle(r)}
          testId={`tree-resource-report-${r.id}`}
          glyph={<Glyph icon="report" />}
          after={<LineageBadge node={{ kind: NODE_KIND, id: r.id }} hideCurrent fallback={r.graph.stale || r.graph.source_lost ? <span className={tree.count}>{r.graph.source_lost ? "источник удалён" : "устарел"}</span> : null} />}
          onContextMenu={(e) => menu(r, e)}
        />
      ))}
    </div>
  );
}

/** «Отчёты по ресурсам» with its reports: a part of the section «Отчёты» that opens and closes by its arrow. */
export function ResourceReportsTree() {
  const reports = useSel(resourcesStore, (s) => s.reports);
  const [open, setOpen] = useState(true);
  return (
    <>
      <TreeRow label="Отчёты по ресурсам" level={2} expanded={open} onToggle={() => setOpen((o) => !o)} after={<span className={tree.count}>{reports.length}</span>} testId="tree-resource-reports" glyph={<Glyph icon="report" />} />
      {open && <ResourceReportRows level={3} />}
    </>
  );
}
