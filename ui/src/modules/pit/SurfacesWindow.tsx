/**
 * The window «Поверхности оболочек карьера» (ТЗ 8.11.5, вкладка «Карьер» → «Результаты» → «Поверхности»; ADR 0028 решения 25, 29): the shells of the scenario with their RF
 * (all of them are picked at first), «Построить» — the command `pit.surfaces` (the same for Ctrl+K and the assistant): the DTMs of the picked shells (the lowest mined level of
 * every column, else the relief) as wireframes of the project in the folder of the scenario; the course (the stage, the progress) and the result below; a second run replaces
 * the DTMs in place. Floating, not modal.
 */
import { useMemo } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { closeWindow } from "../../shell/windows";
import { fmtCount } from "../blocks/report/format";
import { useSel } from "./PitParts";
import mod from "./Pit.module.css";
import res from "./Results.module.css";
import { DASH, pickedOf, rfText, shellsOf, shellsText, val } from "./resultsModel";
import { pickShells, resultsStore } from "./resultsState";
import { buildReason, JobLine, ScenarioPick, WarningList } from "./ResultsParts";
import { pitStore } from "./state";

export function SurfacesWindow({ winId }: { winId: string }) {
  const app = useApp();
  const id = useSel(pitStore, (s) => s.currentId);
  const scenario = useSel(pitStore, (s) => (s.scenario && s.scenario.id === s.currentId ? s.scenario : null));
  const run = useSel(resultsStore, (s) => s.surfaces);
  const pickedRaw = useSel(resultsStore, (s) => s.picked);
  const shells = useMemo(() => shellsOf(scenario), [scenario]);
  const picked = pickedRaw && pickedRaw.scenarioId === id ? pickedRaw.shells : null;
  const checked = pickedOf(shells, picked);
  const busy = run.phase === "running";
  const reason = buildReason(app, id, scenario, busy) ?? (checked.length === 0 ? "Выберите хотя бы одну оболочку" : null);
  const toggle = (k: number, on: boolean) => id !== null && pickShells(id, shells.map((s) => s.k).filter((x) => (x === k ? on : checked.includes(x))));
  const build = () => id !== null && void app.run("pit.surfaces", { scenario_id: id, shells: shellsText(checked) });
  const result = run.result && run.scenarioId === id ? run.result : null;

  return (
    <div className={mod.window} data-testid="pit-surf-window">
      <div className={res.bar}>
        <ScenarioPick id={id} disabled={busy} testId="pit-surf-scenario" />
      </div>
      <div className={res.main}>
        {id === null && <p className={res.empty}>Выберите сценарий карьера с результатом: поверхности строятся по его оболочкам.</p>}
        {scenario && !scenario.has_result && (
          <p className={res.empty} data-testid="pit-surf-no-result">
            У сценария «{scenario.name}» нет результата. Запустите расчёт: тогда здесь появятся его оболочки.
          </p>
        )}
        {shells.length > 0 && (
          <>
            <div className={res.tools}>
              <button type="button" className={css.btn} disabled={busy} data-testid="pit-surf-all" onClick={() => id !== null && pickShells(id, null)}>
                Выбрать все
              </button>
              <button type="button" className={css.btn} disabled={busy} data-testid="pit-surf-none" onClick={() => id !== null && pickShells(id, [])}>
                Снять все
              </button>
              <span className={css.note} data-testid="pit-surf-count">
                Выбрано оболочек: {fmtCount(checked.length)} из {fmtCount(shells.length)}
              </span>
            </div>
            <div className={res.shells} data-testid="pit-surf-shells">
              <table>
                <thead>
                  <tr>
                    <th>Оболочка</th>
                    <th className={res.num}>RF</th>
                    <th className={res.num}>Блоков</th>
                    <th className={res.num}>Руда, т</th>
                    <th className={res.num}>Пустая, т</th>
                  </tr>
                </thead>
                <tbody>
                  {shells.map((s) => (
                    <tr key={s.k} data-testid={`pit-surf-shell-${s.k}`}>
                      <td>
                        <label>
                          <input type="checkbox" checked={checked.includes(s.k)} disabled={busy} aria-label={`Оболочка ${s.k}`} data-testid={`pit-surf-pick-${s.k}`} onChange={(e) => toggle(s.k, e.target.checked)} />
                          {s.k}
                        </label>
                      </td>
                      <td className={res.num}>{rfText(s.rf)}</td>
                      <td className={res.num}>{s.blocks === null ? DASH : fmtCount(s.blocks)}</td>
                      <td className={res.num}>{val(s.ore_t)}</td>
                      <td className={res.num}>{val(s.waste_t)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <JobLine run={run} testId="pit-surf" />
        {result && (
          <div data-testid="pit-surf-result">
            <h5 className={res.section}>
              Построено ЦТМ: {fmtCount(result.wireframes.length)}
              {result.folder ? `, папка «${result.folder}»` : ""}
            </h5>
            <WarningList warnings={result.warnings} testId="pit-surf-warnings" />
            <div className={css.tableWrap}>
              <table className={css.table}>
                <thead>
                  <tr>
                    <th>Оболочка</th>
                    <th>RF</th>
                    <th>Каркас</th>
                    <th>Узлов</th>
                    <th>Треугольников</th>
                  </tr>
                </thead>
                <tbody>
                  {result.wireframes.map((w) => (
                    <tr key={w.shell} data-testid={`pit-surf-wire-${w.shell}`}>
                      <td>{w.shell}</td>
                      <td>{rfText(w.rf)}</td>
                      <td>
                        {w.name}
                        {w.replaced ? " (заменён)" : ""}
                      </td>
                      <td>{w.nodes === null ? DASH : fmtCount(w.nodes)}</td>
                      <td>{w.triangles === null ? DASH : fmtCount(w.triangles)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
      <div className={res.footer}>
        <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Построить ЦТМ выбранных оболочек: каркасы проекта в папке сценария"} data-testid="pit-surf-run" onClick={build}>
          Построить поверхности
        </button>
        <span className={res.grow} />
        <button type="button" className={css.btn} data-testid="pit-surf-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
