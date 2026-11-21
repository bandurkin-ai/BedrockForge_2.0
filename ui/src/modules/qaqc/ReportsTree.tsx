/**
 * The section «Отчёты» of the data tree (ADR 0020 §11): the QAQC reports of the project by name, «строится» and
 * «устарел» marked; a double click or Enter opens it in «QAQC › Отчёт» (`qaqc.report_open`); the menu —
 * «Открыть», «Сохранить XLSX…», «PDF» (not available: Э22), «Перестроить», «Переименовать…», «Удалить…» — the same
 * commands as the tab (`reportActions`). The name is asked in our own window.
 */
import { useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import type { QaqcReport } from "./api";
import { hasXlsx, PDF_REASON, rebuildReason, reportsByName, reportsStore, reportStateTitle, staleTitle } from "./reportRun";

type Run = (id: string, args: Record<string, unknown>) => void;

/** Asks the new name in our own window and renames the report. */
export async function askRename(r: Pick<QaqcReport, "id" | "name">, run: Run): Promise<void> {
  const name = await askText({ title: "Переименовать отчёт QAQC", label: "Название", initial: r.name, okLabel: "Переименовать" });
  if (name !== null && name.trim() && name.trim() !== r.name) run("qaqc.report_rename", { report_id: r.id, name: name.trim() });
}

/** The actions of a report (the tree menu and the buttons of the tab); `reason` — that of the command. */
export function reportActions(r: QaqcReport, reason: (id: string) => string | null, run: Run): MenuItem[] {
  const building = r.state === "building" ? "Отчёт строится" : null;
  const notBuilt = r.state === "failed" ? "Отчёт не построен: перестройте его" : null;
  return [
    { label: "Открыть", reason: reason("qaqc.report_open"), run: () => run("qaqc.report_open", { report_id: r.id }) },
    { label: hasXlsx(r) ? "Сохранить XLSX…" : "Сохранить XLSX… (файл будет записан)", reason: reason("qaqc.report_save") ?? building ?? notBuilt, run: () => run("qaqc.report_save", { report_id: r.id, kind: "xlsx" }) },
    { label: "PDF", reason: PDF_REASON },
    { label: "Перестроить", reason: reason("qaqc.report_rebuild") ?? rebuildReason(r), run: () => run("qaqc.report_rebuild", { report_id: r.id }) },
    { label: "Переименовать…", reason: reason("qaqc.report_rename"), run: () => void askRename(r, run) },
    { label: "Удалить…", reason: reason("qaqc.report_delete") ?? building, run: () => run("qaqc.report_delete", { report_id: r.id }) },
  ];
}

/** The label of a row: the name, «строится», «ошибка», «устарел». */
export function reportLabel(r: Pick<QaqcReport, "name" | "state" | "stale">): string {
  const marks = [r.state !== "ready" ? reportStateTitle(r.state) : "", r.stale ? "устарел" : ""].filter(Boolean);
  return marks.length ? `${r.name} · ${marks.join(", ")}` : r.name;
}

export function ReportRows() {
  const app = useApp();
  const get = () => reportsStore.get();
  const { list, error } = useSyncExternalStore(reportsStore.subscribe, get, get);
  const run: Run = (id, args) => void app.run(id, args);
  if (error && !list) return <div role="note">{error}</div>;
  if (!list?.length) return null;
  const menu = (r: QaqcReport, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, reportActions(r, (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label="Отчёты QAQC">
      {reportsByName(list).map((r) => (
        <TreeRow
          key={r.id}
          label={reportLabel(r)}
          ariaLabel={r.name}
          level={2}
          onActivate={() => run("qaqc.report_open", { report_id: r.id })}
          title={`Отчёт QAQC «${r.name}» по данным «${r.data_name}»: ${reportStateTitle(r.state)}${r.stale ? `; ${staleTitle(r.stale)}` : ""}; двойной щелчок — вкладка «QAQC › Отчёт»`}
          testId={`tree-qaqc-report-${r.id}`}
          glyph={<Glyph icon="report" />}
          onContextMenu={(e) => menu(r, e)}
        />
      ))}
    </div>
  );
}

/** The section «Отчёты» of the data tree. */
export function ReportsTree() {
  return (
    <>
      <ReportRows />
    </>
  );
}
