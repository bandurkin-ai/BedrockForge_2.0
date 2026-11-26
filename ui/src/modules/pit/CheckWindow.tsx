/**
 * The window «Проверка шаблона карьера» (ТЗ 8.11.2, 8.11.5, вкладка «Карьер» → «Расчёт» → «Проверить шаблон»; ADR 0028 решения 4, 9–12, 29): the result of `pit.check` — the number
 * of levels K (auto or set), the tolerance, for every domain the offsets of the template and the angle error (max and mean), the warning «увеличьте K» when the error is above
 * the tolerance, the table of the sectors with φ_эфф there and the chart of φ_эфф(az) at 360 azimuths. The check writes nothing; «Проверить заново» runs the command through
 * the registry (the log, the notice, the question of the assistant). A refusal of the core (`variable_grid`…) is told in Russian with its hint. Floating, not modal.
 */
import { useRef, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { seriesColor } from "../../charts/core/palette";
import { domainTitle, exceeds, phiCsv, sectorRows, type CheckDomain, type CheckResult } from "./checkModel";
import { fmt, fmtCount, NO_VALUE, secondsText, warningText } from "./format";
import mod from "./Pit.module.css";
import { PhiChart } from "./PhiChart";
import { useSel } from "./PitParts";
import { pitStore } from "./state";

const deg = (v: number | null): string => (v === null ? NO_VALUE : `${fmt(v)}°`);

/** The table of the domains: the sectors, the offsets of the template, the angle error against the tolerance. */
export function DomainsTable({ result }: { result: CheckResult }) {
  return (
    <div className={css.tableWrap} data-testid="pit-check-domains">
      <table className={css.table}>
        <thead>
          <tr>
            <th>Домен</th>
            <th>Секторов</th>
            <th>Смещений</th>
            <th title="Наибольшее отклонение φ_эфф от заданного угла по азимутам">Ошибка угла, max</th>
            <th>Ошибка угла, среднее</th>
            <th>Допуск</th>
          </tr>
        </thead>
        <tbody>
          {result.domains.map((d, i) => {
            const over = exceeds(d, result.tolerance);
            return (
              <tr key={i} data-testid={`pit-check-domain-${i}`} data-over={over}>
                <td>
                  <span className={mod.swatch} style={{ background: seriesColor(i) }} aria-hidden="true" />
                  {domainTitle(d)}
                </td>
                <td>{d.table.length}</td>
                <td>{d.offsets === null ? NO_VALUE : fmtCount(d.offsets)}</td>
                <td>{deg(d.errorMax)}</td>
                <td>{deg(d.errorMean)}</td>
                <td className={over ? css.warn : undefined}>{over ? "больше допуска" : "в допуске"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The sectors of a domain: the angle of the table, φ_эфф at its azimuth and the deviation. */
export function SectorsTable({ domain }: { domain: CheckDomain }) {
  const rows = sectorRows(domain);
  return (
    <div className={css.tableWrap} data-testid="pit-check-sectors">
      <table className={css.table}>
        <thead>
          <tr>
            <th>Азимут</th>
            <th>Угол по таблице</th>
            <th>φ_эфф</th>
            <th>Отклонение</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{deg(r.az)}</td>
              <td>{deg(r.angle)}</td>
              <td>{deg(r.phi)}</td>
              <td>{r.delta === null ? NO_VALUE : `${r.delta > 0 ? "+" : ""}${fmt(r.delta)}°`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The summary of the check: the model, K, the levels, the tolerance, the offsets, the time. */
export function summaryLine(r: CheckResult): string {
  const parts = [
    r.model?.name ? `модель «${r.model.name}»` : null,
    `K = ${r.k === null ? NO_VALUE : r.k}${r.kAuto ? " (подобран автоматически)" : ""}`,
    r.levels !== null && r.levels !== r.k ? `уровней в шаблоне ${r.levels}` : null,
    `допуск ${deg(r.tolerance)}`,
    r.seconds === null ? null : `${secondsText(r.seconds)} с`,
  ];
  return parts.filter((p): p is string => p !== null).join(" · ");
}

export function CheckWindow({ winId }: { winId: string }) {
  const app = useApp();
  const check = useSel(pitStore, (s) => s.check);
  const scenario = useSel(pitStore, (s) => s.scenario);
  const svg = useRef<SVGSVGElement | null>(null);
  const [picked, setPicked] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("pit.check");
  const id = check.scenarioId ?? scenario?.id ?? null;
  const running = check.phase === "running";
  const r = check.result;
  const domain = r ? (r.domains[Math.min(picked, r.domains.length - 1)] ?? null) : null;

  const again = async () => {
    setNote(null);
    try {
      const res = await app.registry.execute("pit.check", id === null ? {} : { scenario_id: id }, "user");
      if (!res.ok) setNote(res.result);
    } catch (e) {
      setNote(errorText(e));
    }
  };

  return (
    <div className={mod.window} data-testid="pit-check-window">
      <div className={mod.body}>
        <p className={mod.head} data-testid="pit-check-head">
          {check.scenarioName ? `Сценарий «${check.scenarioName}»` : scenario ? `Сценарий «${scenario.name}»` : "Сценарий не выбран"}
        </p>
        {check.phase === "idle" && <p className={css.note} data-testid="pit-check-idle">Шаблон ещё не проверялся: нажмите «Проверить заново».</p>}
        {running && (
          <p className={css.note} role="status" data-testid="pit-check-running">
            Проверяется шаблон дуг по углам бортов…
          </p>
        )}
        {check.phase === "failed" && check.error && (
          <p className={css.error} role="alert" data-testid="pit-check-error">
            {check.error.message} {check.error.hint}
          </p>
        )}
        {r && check.phase === "done" && (
          <>
            <p className={mod.ready} data-testid="pit-check-summary">
              {summaryLine(r)}
            </p>
            {r.warnings.map((w, i) => (
              <p key={i} className={css.warn} role="alert" data-testid={`pit-check-warning-${w.code}`}>
                {warningText(w)}
              </p>
            ))}
            {!r.warnings.length && r.domains.every((d) => !exceeds(d, r.tolerance)) && (
              <p className={css.note} data-testid="pit-check-ok">
                Ошибка угла во всех доменах в допуске {deg(r.tolerance)}.
              </p>
            )}
            <DomainsTable result={r} />
            <div className={mod.chartBox} data-testid="pit-check-chart" data-domains={r.domains.length}>
              <div className={mod.legend} aria-label="Домены">
                {r.domains.map((d, i) => (
                  <span key={i} className={mod.legendItem}>
                    <span className={mod.swatch} style={{ background: seriesColor(i) }} aria-hidden="true" />
                    {domainTitle(d)}
                  </span>
                ))}
                <span className={css.note}>линия — φ_эфф шаблона; кружки — углы таблицы с допуском</span>
              </div>
              <PhiChart ref={svg} result={r} />
              <ChartToolbar svgRef={svg} fileName={`${check.scenarioName || "Карьер"} — φ_эфф`} csv={() => phiCsv(r)} csvLabel="CSV φ_эфф" />
            </div>
            {domain && (
              <>
                {r.domains.length > 1 && (
                  <label className={css.field}>
                    <span>Секторы домена</span>
                    <select className={css.input} value={Math.min(picked, r.domains.length - 1)} aria-label="Домен для таблицы секторов" data-testid="pit-check-pick" onChange={(e) => setPicked(Number(e.target.value))}>
                      {r.domains.map((d, i) => (
                        <option key={i} value={i}>
                          {domainTitle(d)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <SectorsTable domain={domain} />
              </>
            )}
          </>
        )}
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason} title={reason ?? "Проверить сохранённые углы бортов сценария заново"} data-testid="pit-check-again" onClick={() => void again()}>
          Проверить заново
        </button>
        <button type="button" className={css.btn} disabled={id === null} title="Изменить углы, домены, K и допуск" data-testid="pit-check-slopes" onClick={() => void app.run("pit.slopes", id === null ? {} : { scenario_id: id })}>
          Углы бортов…
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
