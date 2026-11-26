/**
 * «Свойства» of an implicit model (ТЗ 8.9.4; ADR 0026 «Команды реестра»): the name with the commands of the model (`implicit.update`, `implicit.domains`,
 * `implicit.to_wireframe`, `implicit.delete` — the registry), «Устарела: причина» / the error of the build / «считается», the settings (the kernel, η, the step h,
 * the patches, the data), the anisotropy with the ellipsoid in 3D (`viz/ellipsoid`, `implicit.show`), the numbers of the last build (points, patches, the
 * residual, cond, the time; by groups), the data points of the groups in 3D, the outputs and the journal of the last build. The origin and the dependents of the
 * model — the panel of the graph under it (`shell/PropertiesPanel.tsx`). A number the core did not give is «—», never 0.
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Check, Facts, useRun } from "../../shell/PropsParts";
import { num } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { loadJob } from "../blocks/build/api";
import { findTrend, trendsStore } from "../trends/state";
import { staleLine } from "./run";
import { implicitStore, loadDetails } from "./state";
import { anisotropyFacts, anisotropyOf, drawable, groupStats, journalOfResult, resultFacts, settingsFacts, trendAngles } from "./propsModel";
import { stateText } from "./treeModel";
import { outputKindText } from "./stackFacts";
import { StackProps } from "./StackProps";
import { implicitViewStore, KIND_COLORS, KIND_TITLES, KIND_NAMES } from "./viewState";
import { entryText } from "./showCommand";
import { useSel } from "../estimation/hooks";
import { useRegistry } from "./hooks";
import { str } from "../estimation/api";

const dash = "—";

/** The journal of the last build: the result of its job (`last_run.job`); null — not read (the job is gone, or not read yet). */
function useJournal(meta: Record<string, unknown>, version: number): { lines: string[] | null; failed: boolean } {
  const job = typeof (meta.last_run as { job?: unknown } | null)?.job === "number" ? ((meta.last_run as { job: number }).job) : null;
  const [state, setState] = useState<{ lines: string[] | null; failed: boolean }>({ lines: null, failed: false });
  useEffect(() => {
    let live = true;
    setState({ lines: null, failed: false });
    if (job === null) return;
    loadJob({ source: "project", id: job }).then(
      (j) => live && setState({ lines: journalOfResult(j.result), failed: false }),
      () => live && setState({ lines: null, failed: true }),
    );
    return () => {
      live = false;
    };
  }, [job, version]);
  return state;
}

export function ImplicitProps({ id }: { id: number }) {
  const app = useApp();
  useRegistry(app.registry);
  const row = useSel(implicitStore, (s) => s.rows.find((m) => m.id === id));
  const info = useSel(implicitStore, (s) => s.details[id]);
  const view = useSel(implicitViewStore, (s) => s);
  useSel(trendsStore, (s) => s.trends); // the angles of a trend of the model follow the trends
  const { run, error } = useRun();
  const version = row?.version;
  const fresh = info !== undefined && info.version === version && info.state === row?.state && info.stale === row?.stale;
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    if (version !== undefined && !fresh) loadDetails(id).then(() => setFailed(null), (e: unknown) => setFailed(e instanceof Error ? e.message : String(e)));
  }, [id, version, fresh, row?.state, row?.stale]);
  const meta = info?.meta ?? {};
  const journal = useJournal(meta, info?.version ?? 0);
  const anis = useMemo(() => anisotropyOf(meta, trendAngles), [info, view]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!row) return null;
  const name = info?.name ?? row.name;
  const reason = (cmd: string) => app.registry.reason(cmd);
  const stale = staleLine(info ?? row);
  const err = info?.error ?? null;
  const errGroup = err && err.group !== null ? (row.group_list.find((g) => g.id === err.group)?.name ?? `№${err.group}`) : null;
  const settings = info ? settingsFacts(meta) : [];
  const result = info ? resultFacts(meta) : [];
  const groups = info ? groupStats(info) : [];
  const trend = anis.trendId !== null ? findTrend(anis.trendId) : undefined;
  const ellipsoid = drawable(anis);
  const ellipsoidReason = anis.source === "none" ? "У модели нет анизотропии" : !ellipsoid ? "Оси заданы структурным трендом или тренд не прочитан" : null;
  const building = row.state === "building";
  const cmd = (label: string, id2: string, args: Record<string, unknown> = {}, lock: string | null = null) => {
    const why = reason(id2) ?? lock;
    return (
      <button type="button" className={css.btn} disabled={why !== null} title={why ? `${label}\nНедоступно: ${why}` : label} onClick={() => void run(id2, { model_id: id, ...args })} data-testid={`props-implicit-${id2.split(".")[1]}`}>
        {label}
      </button>
    );
  };
  return (
    <section className={css.panel} aria-label={`Свойства: ${name}`} data-testid="props-implicit" data-state={failed ? "failed" : fresh ? "ready" : "loading"} data-model-state={row.state}>
      <header className={css.head}>
        <span className={css.kind}>Неявная модель</span>
        <h4 className={css.name} title={name}>
          {name}
        </h4>
      </header>
      <div className={css.row}>
        {cmd("Обновить модель", "implicit.update", {}, building ? "Модель сейчас строится" : null)}
        {cmd("Домены…", "implicit.domains")}
        {cmd("В каркас…", "implicit.to_wireframe", {}, row.stats ? null : "Модель ещё не построена")}
        {cmd("Удалить…", "implicit.delete")}
      </div>
      {building && (
        <p className={css.note} role="status" data-testid="props-implicit-building">
          Модель считается: итоги появятся по окончании задания («Задания»).
        </p>
      )}
      {stale && (
        <p className={css.warn} role="status" data-testid="props-implicit-stale">
          {stateText(row) ? `${stateText(row)[0].toUpperCase()}${stateText(row).slice(1)}. ` : ""}
          {stale}
        </p>
      )}
      {err && (
        <p className={css.error} role="alert" data-testid="props-implicit-error">
          {err.message} {err.hint}
          {errGroup ? ` (группа «${errGroup}»)` : ""}
        </p>
      )}
      <fieldset className={css.group} data-testid="props-implicit-settings">
        <legend>Параметры</legend>
        {settings.length ? <Facts facts={settings} /> : <p className={css.note}>{failed ? `Не прочитано: ${failed}` : "Читается…"}</p>}
      </fieldset>
      <fieldset className={css.group} data-testid="props-implicit-anisotropy" data-source={anis.source}>
        <legend>Анизотропия</legend>
        <Facts facts={anisotropyFacts(anis, trend?.name)} />
        <Check checked={ellipsoid && view.ellipsoids.includes(id)} disabled={ellipsoidReason !== null && !view.ellipsoids.includes(id)} title={ellipsoidReason ?? "Эллипсоид анизотропии в 3D (главная ось — четверть размера модели)"} onCommit={(on) => run("implicit.show", { model_id: id, what: "ellipsoid", visible: on })}>
          Эллипсоид в 3D
        </Check>
        {ellipsoidReason && <p className={css.note}>{ellipsoidReason}.</p>}
      </fieldset>
      <fieldset className={css.group} data-testid="props-implicit-result" data-built={result.length ? "yes" : "no"}>
        <legend>Последняя сборка</legend>
        {result.length ? <Facts facts={result} /> : <p className={css.note}>{fresh ? "Модель ещё не построена: «Обновить модель»." : "Читается…"}</p>}
        {groups.length > 0 && (
          <div className={css.tableWrap}>
            <table className={css.table} data-testid="props-implicit-groups">
              <thead>
                <tr>
                  <th>Группа</th>
                  <th>Точек</th>
                  <th>Участков</th>
                  <th title="Наибольшая невязка в данных, м">Невязка</th>
                  <th>cond</th>
                  <th>η</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <tr key={g.group ?? "rest"}>
                    <td>{g.name}</td>
                    <td>{g.points}</td>
                    <td>{g.patches}</td>
                    <td>{g.resid}</td>
                    <td>{g.cond}</td>
                    <td>{g.eta}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </fieldset>
      {info && <StackProps id={id} meta={meta} outputs={info.outputs} run={run} reason={(c) => reason(c)} />}
      <fieldset className={css.group} data-testid="props-implicit-points">
        <legend>Точки данных в 3D</legend>
        {row.group_list.length ? (
          row.group_list.map((g) => {
            const key = g.id === null ? "" : `${id}:${g.id}`;
            const shown = key !== "" && view.points.includes(key);
            const entry = view.entries[key];
            return (
              <div key={g.id ?? g.name}>
                <Check checked={shown} disabled={g.id === null} onCommit={(on) => run("implicit.show", { model_id: id, group_id: g.id, what: "points", visible: on })}>
                  {g.name || `группа ${g.id}`}
                </Check>
                {shown && entry && (
                  <p className={entry.status === "error" ? css.error : css.note} data-testid={`props-implicit-points-${g.id}`}>
                    {entry.status === "loading" ? "Читаются…" : entry.status === "error" ? `Не прочитано: ${entry.error}` : entryText(entry)}
                  </p>
                )}
              </div>
            );
          })
        ) : (
          <p className={css.note}>В модели нет групп: добавьте домены.</p>
        )}
        <p className={css.note} aria-label="Цвета точек">
          {KIND_NAMES.map((k) => (
            <span key={k} style={{ marginRight: 8 }}>
              <span aria-hidden style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: KIND_COLORS[k], marginRight: 3 }} />
              {KIND_TITLES[k]}
            </span>
          ))}
        </p>
      </fieldset>
      {info && info.outputs.length > 0 && (
        <fieldset className={css.group} data-testid="props-implicit-outputs">
          <legend>Выходы</legend>
          <div className={css.tableWrap}>
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Каркас</th>
                  <th>Вид</th>
                  <th title="м³">Объём</th>
                  <th title="м²">Площадь</th>
                  <th>Треугольников</th>
                </tr>
              </thead>
              <tbody>
                {info.outputs.map((o) => (
                  <tr key={o.wireframe_id ?? o.line_layer_id ?? o.name} title={o.region ? `${o.name}\nОбласть крыла разлома — пространство, не порода: в сумму объёмов пород не входит` : o.common ? `${o.name}\nОбщее тело системы жил повторяет объёмы жил: в сумму не входит` : o.name}>
                    <td>{o.name}</td>
                    <td>{outputKindText(o)}</td>
                    <td>{o.kind === "line" ? (o.length !== null ? `${num(o.length, 1)} м` : dash) : o.volume !== null ? num(o.volume, 0) : dash}</td>
                    <td>{o.area !== null ? num(o.area, 0) : dash}</td>
                    <td>{o.triangles !== null ? num(o.triangles, 0) : o.lines !== null ? `${num(o.lines, 0)} лин.` : dash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </fieldset>
      )}
      <fieldset className={css.group} data-testid="props-implicit-journal">
        <legend>Журнал последней сборки</legend>
        {journal.lines && journal.lines.length > 0 ? (
          <ul className={css.list}>
            {journal.lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        ) : (
          <p className={css.note}>{result.length ? (journal.failed ? "Журнал задания недоступен (задание удалено из списка)." : journal.lines ? "Журнал пуст." : "Читается…") : "Сборки не было."}</p>
        )}
        {str(info?.meta.algo) && <p className={css.note}>Алгоритм {str(info?.meta.algo)}.</p>}
      </fieldset>
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
