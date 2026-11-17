/** Commands audit.run (confirm) and audit.export (ТЗ 7.12); the result follows the active set and its edits. */
import { bridge } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import { setPanel } from "../../shell/workspace";
import { onCoreEvent, onJobFinished, refresh as refreshJobs } from "../jobs/state";
import { tablesStore } from "../tables/state";
import { auditStore, loadAudit, runAudit } from "./state";

export function bindAudit(app: App): void {
  let setId: number | null = null;
  tablesStore.subscribe(() => {
    const id = tablesStore.get().setId;
    if (id === setId) return;
    setId = id;
    void loadAudit(id);
  });
  onJobFinished((job) => {
    if (job.kind === "audit.run" || job.kind === "import.drillholes") void loadAudit();
  });
  onCoreEvent((event) => {
    if (event === "drillholes.changed" && auditStore.get().result) void loadAudit(); // «устарел»
  });
  const needSet = (ctx: { hasSet: boolean }) => (ctx.hasSet ? null : "Нет активного набора скважин");
  app.registry.bind(
    "audit.run",
    async () => {
      const r = await runAudit();
      app.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "audit") }));
      await refreshJobs();
      return `Аудит запущен: задание №${r.id}`;
    },
    needSet,
  );
  app.registry.bind(
    "audit.export",
    async () => {
      const s = auditStore.get();
      if (!s.result || s.setId === null) throw new Error("Аудит набора ещё не запускался");
      const set = tablesStore.get().sets.find((x) => x.id === s.setId);
      const path = await bridge()?.pickSave(`Аудит — ${set?.name ?? "набор"}.xlsx`, "xlsx");
      if (!path) return "Отменено";
      try {
        const r = await api.post<{ source: string; id: number }>(`/audit/sets/${s.setId}/export`, { path });
        await refreshJobs();
        return `Отчёт Excel: задание №${r.id}`;
      } catch (e) {
        throw new Error(e instanceof CoreError ? e.full : String(e));
      }
    },
    needSet,
  );
}
