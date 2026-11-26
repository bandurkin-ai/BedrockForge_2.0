/**
 * The window «Каркас карьера» (ТЗ 8.11.5, вкладка «Карьер» → «Результаты» → «Каркас карьера»; ADR 0028 решения 26, 29): one shell of the scenario and the name of the solid,
 * «Построить» — the command `pit.solid` (the same for Ctrl+K and the assistant): the closed solid of the pit between the relief and the surface of the shell as a wireframe of the
 * project. The result: the volume of the solid, the volume of the blocks of the shell, the difference in per cent (shown, not an error), the parts, the method, the warnings. A solid that
 * is not closed is `pit_solid_failed`: told in Russian with the hint. Floating, not modal.
 */
import { useMemo } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { closeWindow } from "../../shell/windows";
import { fmtCount } from "../blocks/report/format";
import { useSel } from "./PitParts";
import mod from "./Pit.module.css";
import res from "./Results.module.css";
import { chooseSolidShell, resultsStore, setSolidName } from "./resultsState";
import { DASH, defaultShell, methodText, rfText, shellsOf, val } from "./resultsModel";
import { buildReason, JobLine, ScenarioPick, WarningList } from "./ResultsParts";
import { pitStore } from "./state";

export function SolidWindow({ winId }: { winId: string }) {
  const app = useApp();
  const id = useSel(pitStore, (s) => s.currentId);
  const scenario = useSel(pitStore, (s) => (s.scenario && s.scenario.id === s.currentId ? s.scenario : null));
  const run = useSel(resultsStore, (s) => s.solid);
  const chosen = useSel(resultsStore, (s) => s.solidShell);
  const name = useSel(resultsStore, (s) => s.solidName);
  const shells = useMemo(() => shellsOf(scenario), [scenario]);
  const shell = chosen && chosen.scenarioId === id && shells.some((s) => s.k === chosen.shell) ? chosen.shell : defaultShell(shells);
  const busy = run.phase === "running";
  const reason = buildReason(app, id, scenario, busy) ?? (shell === null ? "Выберите оболочку" : null);
  const build = () => id !== null && shell !== null && void app.run("pit.solid", { scenario_id: id, shell, ...(name.trim() ? { name: name.trim() } : {}) });
  const result = run.result && run.scenarioId === id ? run.result : null;

  return (
    <div className={mod.window} data-testid="pit-solid-window">
      <div className={res.bar}>
        <ScenarioPick id={id} disabled={busy} testId="pit-solid-scenario" />
        <label>
          <span>Оболочка</span>
          <select className={res.select} value={shell ?? ""} disabled={busy || shells.length === 0} aria-label="Оболочка" data-testid="pit-solid-shell" onChange={(e) => id !== null && chooseSolidShell(id, e.target.value ? Number(e.target.value) : null)}>
            {shells.length === 0 && <option value="">— нет оболочек —</option>}
            {shells.map((s) => (
              <option key={s.k} value={s.k}>
                {s.k} · RF {rfText(s.rf)} · блоков {s.blocks === null ? DASH : fmtCount(s.blocks)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Имя каркаса</span>
          <input className={css.input} value={name} placeholder="по умолчанию — имя сценария и RF" disabled={busy} aria-label="Имя каркаса" data-testid="pit-solid-name" onChange={(e) => setSolidName(e.target.value)} />
        </label>
      </div>
      <div className={res.main}>
        {id === null && <p className={res.empty}>Выберите сценарий карьера с результатом и оболочку: каркас строится между рельефом и её поверхностью.</p>}
        {scenario && !scenario.has_result && (
          <p className={res.empty} data-testid="pit-solid-no-result">
            У сценария «{scenario.name}» нет результата. Запустите расчёт: тогда можно построить каркас карьера.
          </p>
        )}
        <JobLine run={run} testId="pit-solid" />
        {result && (
          <div data-testid="pit-solid-result">
            <h5 className={res.section}>
              Каркас «{result.name}»{result.replaced ? " (заменён на месте)" : ""}
              {result.folder ? `, папка «${result.folder}»` : ""}
            </h5>
            <WarningList warnings={result.warnings} testId="pit-solid-warnings" />
            <dl className={res.kv}>
              <dt>Оболочка</dt>
              <dd data-testid="pit-solid-r-shell">
                {result.shell ?? DASH} (RF {rfText(result.rf)})
              </dd>
              <dt>Объём каркаса, м³</dt>
              <dd data-testid="pit-solid-r-volume">{val(result.volume)}</dd>
              <dt>Объём блоков оболочки, м³</dt>
              <dd data-testid="pit-solid-r-blocks">{val(result.volume_blocks)}</dd>
              <dt title="Объём блоков считается по сетке центров, поэтому небольшое расхождение — норма: оно показывается, а не считается ошибкой">Расхождение объёмов, %</dt>
              <dd data-testid="pit-solid-r-diff">{val(result.volume_diff_pct)}</dd>
              <dt>Частей</dt>
              <dd data-testid="pit-solid-r-parts">{result.parts === null ? DASH : fmtCount(result.parts)}</dd>
              <dt>Замкнут</dt>
              <dd data-testid="pit-solid-r-closed">{result.closed ? "да" : "нет"}</dd>
              <dt>Метод</dt>
              <dd data-testid="pit-solid-r-method">{methodText(result.method)}</dd>
            </dl>
          </div>
        )}
      </div>
      <div className={res.footer}>
        <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Построить замкнутый каркас карьера между рельефом и поверхностью оболочки"} data-testid="pit-solid-run" onClick={build}>
          Построить каркас
        </button>
        <span className={res.grow} />
        <button type="button" className={css.btn} data-testid="pit-solid-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
