/**
 * The window «Сравнить отчёты» (ТЗ 7.35 Д-37, ТД37-1/2; ADR 0027 решение 26; вопрос № 109): two reports of the project to choose (A and B), «Сравнить» — the command
 * `resources.compare` (the same for Ctrl+K and the assistant); then the notes («Сетки различаются» is a banner), the versions of the inputs of both reports
 * (Д-35), the table «A | B | разность» of T, ḡ and M by the groups and the cut-offs (a row only in one report is marked, its difference is «—», not 0) and
 * the two curves on one chart. The comparison is synchronous and writes nothing: it works in a project open for reading. Floating, not modal.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { closeWindow } from "../../shell/windows";
import { CompareChart } from "./CompareChart";
import { elementOf, onlyCount } from "./compareModel";
import { CompareTable, Head, Notes } from "./CompareParts";
import { chooseReport, compareStore, setElement, setMetric, setOnlyDiff, syncCompare } from "./compareState";
import mod from "./Compare.module.css";
import { useSel } from "./ReportParts";
import { loadReports, resourcesStore } from "./state";

export function CompareWindow({ winId }: { winId: string }) {
  const app = useApp();
  const st = useSel(compareStore, (s) => s);
  const reports = useSel(resourcesStore, (s) => s.reports);
  const loaded = useSel(resourcesStore, (s) => s.reportsLoaded);
  useEffect(() => {
    syncCompare();
    void loadReports();
  }, []);
  const ready = reports.filter((r) => r.state === "ready");
  const busy = st.phase === "loading";
  const reason = app.registry.reason("resources.compare") ?? (st.a === null || st.b === null ? "Выберите оба отчёта: A и B" : busy ? "Сравнение идёт" : null);
  const run = () => st.a !== null && st.b !== null && void app.run("resources.compare", { report_a: st.a, report_b: st.b });
  const ans = st.answer;
  const element = ans ? elementOf(ans, st.element) : null;
  const only = ans ? onlyCount(ans) : { a: 0, b: 0 };

  const pick = (side: "a" | "b", title: string) => (
    <label>
      <span>{title}</span>
      <select className={mod.select} value={st[side] ?? ""} disabled={busy} aria-label={title} data-testid={`cmp-${side}`} onChange={(e) => chooseReport(side, e.target.value ? Number(e.target.value) : null)}>
        <option value="">— выберите —</option>
        {ready.map((r) => (
          <option key={r.id} value={r.id}>
            №{r.id} · {r.name} · {r.model.name}
            {r.graph.stale ? " (устарел)" : ""}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className={mod.window} data-testid="cmp-window">
      <div className={mod.bar}>
        {pick("a", "Отчёт A")}
        {pick("b", "Отчёт B")}
        {ans && ans.elements.length > 1 && (
          <label>
            <span>Элемент таблицы</span>
            <select className={mod.select} value={element ?? ""} aria-label="Элемент таблицы" data-testid="cmp-element" onChange={(e) => setElement(e.target.value)}>
              {ans.elements.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className={mod.check}>
          <input type="checkbox" checked={st.onlyDiff} data-testid="cmp-only-diff" onChange={(e) => setOnlyDiff(e.target.checked)} />
          <span>Только различия</span>
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Сравнить отчёты по строкам: разности тоннажа, содержания и металла"} data-testid="cmp-run" onClick={run}>
            Сравнить
          </button>
        </div>
      </div>
      <div className={mod.main}>
        {st.error && (
          <p className={css.error} role="alert" data-testid="cmp-error" data-code={st.error.code}>
            {st.error.message} {st.error.hint}
          </p>
        )}
        {loaded && !ready.length && (
          <p className={mod.empty} data-testid="cmp-no-reports">
            В проекте нет готовых отчётов по ресурсам. Постройте и сохраните отчёты, чтобы сравнить их.
          </p>
        )}
        {st.phase === "idle" && ready.length > 0 && <p className={mod.empty}>Выберите два отчёта и нажмите «Сравнить»: строки совмещаются по группам и бортам, сетки и входы отчётов сверяются.</p>}
        {busy && (
          <p className={mod.empty} role="status" data-testid="cmp-loading">
            Сравниваю отчёты…
          </p>
        )}
        {ans && (
          <>
            <Notes answer={ans} />
            <Head answer={ans} />
            <p className={css.note} data-testid="cmp-summary">
              Строк: {ans.rows.length.toLocaleString("ru-RU")}
              {only.a + only.b > 0 ? `; только в A: ${only.a}, только в B: ${only.b} (у них нет разности — это пропуск, не 0)` : ""}.
            </p>
            <CompareTable answer={ans} element={element} onlyDiff={st.onlyDiff} />
            <CompareChart answer={ans} element={element} metric={st.metric} onElement={setElement} onMetric={setMetric} />
          </>
        )}
      </div>
      <div className={mod.footer}>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="cmp-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
