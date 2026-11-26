/**
 * «Свойства» of a pit scenario (ТЗ 8.11.5; ADR 0028 решения 20, 23, 29; Д-35): the name and the badge of the graph, what the result was made of (the model and its hash, the solver, the template
 * K and the error of the angle, the error of the objective, the time, the warnings — `scenarioFacts`), the state («параметры изменены, нужен запуск», «считается», the error of the run), the actions
 * — «Pit-by-pit», «Запустить», «Перестроить» (`lineage.rebuild`: the same params, the result is written over), the shell in the 3D (`pit.show`), «Переименовать…», «Удалить…» (the question
 * «Влияние изменения» is the command's) — and the graph's block: «Закрепить», «Происхождение», «Зависимые» (`LineageProps`). Every action is a command of the registry; an unavailable one says why.
 * The shell shows it for the selected `pit_scenario`.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { useSelect } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { LineageProps } from "../lineage/LineageProps";
import { projectStore } from "../projects/state";
import { list, num, obj, type ScenarioDetail } from "./answers";
import { loadScenario } from "./api";
import { formatNumber } from "./reportModel";
import { NODE_KIND, scenarioActions, scenarioFacts } from "./scenarioModel";
import { cancelRun } from "./bind";
import { pitStore } from "./state";

export function ScenarioProps({ id }: { id: number }) {
  const app = useApp();
  const row = useSelect(pitStore, (s) => s.scenarios.find((r) => r.id === id));
  const uid = useSelect(projectStore, (s) => s.current?.uid ?? null);
  // what «Запустить» depends on: the readiness read of the scenario, the scenario of the windows, the run that goes, the error of the read — the buttons follow them
  useSelect(pitStore, (s) => s.validations[id]);
  useSelect(pitStore, (s) => s.scenario);
  useSelect(pitStore, (s) => s.loadError);
  const running = useSelect(pitStore, (s) => s.run.phase === "running" && s.run.scenarioId === id);
  const [detail, setDetail] = useState<ScenarioDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shell, setShell] = useState<string>("");
  const [cancelError, setCancelError] = useState<string | null>(null);
  const version = row ? `${row.version}:${row.has_result}:${row.params_changed}:${row.state}:${row.job_id ?? ""}` : "";
  useEffect(() => {
    let alive = true;
    loadScenario(id).then(
      (d) => alive && (setDetail(d), setError(null)),
      (e: unknown) => alive && setError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [id, version, uid]);
  const have = detail && detail.id === id ? detail : null;
  const actions = row ? scenarioActions(app, row) : [];
  const byLabel = new Map(actions.map((a) => [a.label, a]));
  const btn = (label: string, testId: string, text = label) => {
    const a = byLabel.get(label);
    const why = a?.reason ?? (row ? null : "Сценария нет в списке: обновите раздел «Карьер»");
    return (
      <button type="button" className={css.btn} disabled={!a || !!why} title={why ? `${text}\nНедоступно: ${why}` : text} data-testid={testId} onClick={() => a?.run?.()}>
        {label}
      </button>
    );
  };
  const shells = list(have?.meta?.shells).map(obj).flatMap((s) => (num(s.k) === null ? [] : [{ k: num(s.k) as number, rf: num(s.rf) }]));
  const chosen = shells.some((s) => String(s.k) === shell) ? shell : String(shells[shells.length - 1]?.k ?? "");
  const show = byLabel.get("Pit-by-pit");
  const showWhy = show?.reason ?? null;
  return (
    <div className={css.stack} data-testid="props-pit-scenario">
      <fieldset className={css.group}>
        <legend>Сценарий карьера</legend>
        <div className={css.row} data-testid="props-pit-name">
          <strong>{row?.name ?? have?.name ?? `№${id}`}</strong>
          <LineageBadge node={{ kind: NODE_KIND, id }} />
        </div>
        {row?.state === "running" && <p className={css.note}>Сценарий считается: действия станут доступны после конца задания.</p>}
        {(have?.error ?? null) && (
          <p className={css.error} data-testid="props-pit-error">
            Расчёт не выполнен: {have?.error?.message} {have?.error?.hint}
          </p>
        )}
        {row && !row.has_result && row.state !== "running" && (
          <p className={css.note} data-testid="props-pit-noresult">
            Результата нет: задайте цены, породы и углы и запустите расчёт.
          </p>
        )}
        {row?.params_changed && (
          <p className={css.warn} data-testid="props-pit-changed">
            Параметры изменены: результат устарел, нужен новый запуск.
          </p>
        )}
        {have ? (
          <dl className={css.facts} data-testid="props-pit-facts">
            {scenarioFacts(have, row).map((f) => (
              <div key={f.key} data-testid={`props-pit-${f.key}`} style={{ display: "contents" }}>
                <dt>{f.label}</dt>
                <dd style={{ whiteSpace: "pre-line", overflowWrap: "anywhere" }}>{f.value}</dd>
              </div>
            ))}
          </dl>
        ) : error ? (
          <p className={css.error}>{error}</p>
        ) : (
          <p className={css.note}>Чтение сценария…</p>
        )}
        <div className={css.row}>
          {btn("Pit-by-pit", "props-pit-report", "Показать таблицу и график pit-by-pit в нижней панели")}
          {btn("Запустить", "props-pit-run", "Запустить оптимизацию карьера")}
          {btn("Перестроить", "props-pit-rebuild", "Перестроить сценарий: те же параметры, результат записывается поверх")}
          {running && (
            <button type="button" className={css.btn} title="Остановить расчёт между этапами: ничего не будет записано" data-testid="props-pit-cancel" onClick={() => void cancelRun().catch((e: unknown) => setCancelError(errorText(e)))}>
              Отменить расчёт
            </button>
          )}
        </div>
        {cancelError && (
          <p className={css.error} role="alert" data-testid="props-pit-cancel-error">
            {cancelError}
          </p>
        )}
        {shells.length > 0 && (
          <div className={css.row}>
            <label className={css.row}>
              Оболочка
              <select className={css.input} style={{ width: "auto" }} value={chosen} aria-label="Оболочка для показа в 3D" data-testid="props-pit-shell" onChange={(e) => setShell(e.target.value)}>
                {shells.map((s) => (
                  <option key={s.k} value={String(s.k)}>
                    {s.k} · RF {formatNumber(s.rf, 2)}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className={css.btn} disabled={!show || !!showWhy} title={showWhy ? `Недоступно: ${showWhy}` : "Показать в 3D блоки модели с PIT не больше номера оболочки"} data-testid="props-pit-show" onClick={() => void app.run("pit.show", { scenario_id: id, shell: Number(chosen) })}>
              Показать в 3D
            </button>
          </div>
        )}
        <div className={css.row}>
          {btn("Переименовать…", "props-pit-rename")}
          {btn("Удалить…", "props-pit-delete")}
        </div>
      </fieldset>
      <LineageProps node={{ kind: NODE_KIND, id }} />
    </div>
  );
}
