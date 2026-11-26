/**
 * The window «Сравнить сценарии карьера» (ТЗ 8.11.5; ADR 0028 решения 27, 29; docs/progress/e21-api.md §11): two scenarios of the project that have a result (A and B), «Сравнить» —
 * the command `pit.compare` (the same for Ctrl+K and the assistant); then the heads of both, the parameters that differ, the shells by RF side by side with the difference A − B
 * (a shell only one has is «—», not 0), NPV of the best and the worst case, the blocks only A / only B has, the banner «Сетки различаются» and the two NPV curves. The comparison is
 * synchronous and writes nothing: it works in a project open for reading. Floating, not modal.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { closeWindow } from "../../shell/windows";
import { CompareChart } from "./CompareChart";
import { Heads, Notes, OnlyBlocks, ParamsDiff, ShellsTable, Totals } from "./CompareParts";
import { useSel } from "./PitParts";
import mod from "./Pit.module.css";
import res from "./Results.module.css";
import type { ShellKey } from "./resultsAnswers";
import { METRICS, STATUS_TEXT } from "./resultsModel";
import { chooseCompare, resultsStore, setCompareMetric } from "./resultsState";
import { pitStore } from "./state";

export function CompareWindow({ winId }: { winId: string }) {
  const app = useApp();
  const st = useSel(resultsStore, (s) => s.compare);
  const rows = useSel(pitStore, (s) => s.scenarios);
  const loaded = useSel(pitStore, (s) => s.scenariosLoaded);
  const ready = rows.filter((r) => r.has_result);
  const busy = st.phase === "loading";
  const reason = app.registry.reason("pit.compare") ?? (st.a === null || st.b === null ? "Выберите оба сценария: A и B" : busy ? "Сравнение идёт" : null);
  const run = () => st.a !== null && st.b !== null && void app.run("pit.compare", { a: st.a, b: st.b });
  const ans = st.answer;
  const only = ans ? { a: ans.shells.filter((s) => s.status === "only_a").length, b: ans.shells.filter((s) => s.status === "only_b").length } : { a: 0, b: 0 };

  const pick = (side: "a" | "b", title: string) => (
    <label>
      <span>{title}</span>
      <select className={res.select} value={st[side] ?? ""} disabled={busy} aria-label={title} data-testid={`pit-cmp-${side}`} onChange={(e) => chooseCompare(side, e.target.value ? Number(e.target.value) : null)}>
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
    <div className={mod.window} data-testid="pit-cmp-window">
      <div className={res.bar}>
        {pick("a", "Сценарий A")}
        {pick("b", "Сценарий B")}
        <label>
          <span>Показатель таблицы и графика</span>
          <select className={res.select} value={st.metric} aria-label="Показатель" data-testid="pit-cmp-metric" onChange={(e) => setCompareMetric(e.target.value as ShellKey)}>
            {METRICS.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}, {m.unit}
              </option>
            ))}
          </select>
        </label>
        <div className={res.actions}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Сравнить сценарии: оболочки по RF, NPV, параметры и блоки"} data-testid="pit-cmp-run" onClick={run}>
            Сравнить
          </button>
        </div>
      </div>
      <div className={res.main}>
        {st.error && (
          <p className={css.error} role="alert" data-testid="pit-cmp-error" data-code={st.error.code}>
            {st.error.message} {st.error.hint}
          </p>
        )}
        {loaded && !ready.length && (
          <p className={res.empty} data-testid="pit-cmp-no-results">
            В проекте нет сценариев с результатом. Запустите расчёт сценария, чтобы сравнить его с другим.
          </p>
        )}
        {st.phase === "idle" && ready.length > 0 && <p className={res.empty}>Выберите два сценария и нажмите «Сравнить»: оболочки совмещаются по RF, параметры и блоки сверяются. Можно сравнить сценарий с самим собой: все разности будут нулевыми.</p>}
        {busy && (
          <p className={res.empty} role="status" data-testid="pit-cmp-loading">
            Сравниваю сценарии…
          </p>
        )}
        {ans && (
          <>
            <Notes answer={ans} />
            <Heads answer={ans} />
            <p className={css.note} data-testid="pit-cmp-summary">
              Оболочек по RF: {ans.shells.length.toLocaleString("ru-RU")}
              {only.a + only.b > 0 ? `; ${STATUS_TEXT.only_a}: ${only.a}, ${STATUS_TEXT.only_b}: ${only.b} (у них нет разности — это пропуск, не 0)` : ""}.
            </p>
            <ParamsDiff answer={ans} />
            <ShellsTable answer={ans} metric={st.metric} />
            <Totals answer={ans} />
            <OnlyBlocks answer={ans} />
            <CompareChart answer={ans} metric={st.metric} onMetric={setCompareMetric} />
          </>
        )}
      </div>
      <div className={res.footer}>
        <span className={res.grow} />
        <button type="button" className={css.btn} data-testid="pit-cmp-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
