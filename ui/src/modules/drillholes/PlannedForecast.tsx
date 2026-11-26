/**
 * The tab «Прогноз» of the window «Проектная скважина» (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 28): the hole is cut by the bodies of an implicit model or by
 * closed wireframes (as «Модель на скважины» cuts the holes of a set) — the table of the forecast intervals (FROM, TO, the body; a gap between the bodies
 * is «вне тел», never a code of 0) with the length of every body — and the nearest existing holes (at most 5) of the set at the target (without a
 * target — at the end of the hole): the distance to the hole itself, not to its stations, and the depth along it. The core reads the STORED hole, so the
 * window asks to save first. The command is `planned.evaluate`.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { solids } from "../wireframes/state";
import { loadSets, type SetRow } from "../composites/api";
import { useLatest, useSel } from "../estimation/hooks";
import { implicitStore } from "../implicit/state";
import mod from "./Planned.module.css";
import { codeText, lengthText, xyzLine } from "./plannedModel";
import { askReason, checkSetOf, evaluateBody, plannedCheck, plannedStore, plannedWin, setCheckSet, setSource, type SourceKind } from "./plannedState";

const SOURCES: readonly { id: SourceKind; title: string; hint: string }[] = [
  { id: "none", title: "Только ближайшие скважины", hint: "Без прогноза по телам: расстояние до существующих скважин набора" },
  { id: "model", title: "Тела неявной модели", hint: "Тела групп модели по порядку групп: первая — самая молодая" },
  { id: "meshes", title: "Замкнутые каркасы", hint: "Тела каркасов по порядку списка" },
];

const fixed = (v: number): string => v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function PlannedForecast() {
  const app = useApp();
  const w = useSel(plannedWin, (x) => x);
  const c = useSel(plannedCheck, (x) => x);
  const models = useSel(implicitStore, (x) => x.rows).filter((r) => r.stats !== null || r.outputs > 0);
  const wires = useSel(sceneObjects, (x) => x.wireframes);
  const closed = solids(wires).filter((x) => x.stats?.closed !== false);
  const run = useSel(plannedCheck, (x) => x.evaluate);
  const row = useSel(plannedStore, (x) => (w.id === null ? undefined : x.rows.find((r) => r.id === w.id)));
  const a = run.answer;
  const src = c.source;
  const setId = checkSetOf(c, w.form);
  const body = evaluateBody(src, setId);
  const sets = useLatest<SetRow[]>(loadSets, [], () => undefined);
  const reason = askReason() ?? (typeof body === "string" ? body : run.phase === "loading" ? "Идёт расчёт" : null);
  // the command takes the request from its arguments: the source and the set of the tab go with the id (a refusal stands under the button)
  const go = () => {
    const args: Record<string, unknown> = { id: w.id };
    if (src.kind === "model" && src.modelId !== null) args.model_id = src.modelId;
    else if (src.kind === "meshes" && src.meshIds.length) args.mesh_ids = src.meshIds.join(";");
    if (setId !== null) args.set_id = setId;
    void app.run("planned.evaluate", args).catch(() => undefined);
  };
  const toggleMesh = (id: number, on: boolean) => setSource({ meshIds: on ? [...src.meshIds, id] : src.meshIds.filter((m) => m !== id) });
  return (
    <div data-testid="pl-forecast">
      <fieldset className={css.group}>
        <legend>Чем прогнозировать</legend>
        <div className={mod.sources} role="radiogroup" aria-label="Источник тел прогноза">
          {SOURCES.map((s) => (
            <label key={s.id} title={s.hint}>
              <input type="radio" name="pl-source" checked={src.kind === s.id} data-testid={`pl-source-${s.id}`} onChange={() => setSource({ kind: s.id })} />
              {s.title}
            </label>
          ))}
        </div>
        {src.kind === "model" && (
          <label className={css.formRow}>
            <span>Неявная модель</span>
            <select className={css.input} value={src.modelId ?? ""} aria-label="Неявная модель" data-testid="pl-model" onChange={(e) => setSource({ modelId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{models.length ? "— выберите —" : "нет построенных моделей"}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {src.kind === "meshes" && (
          <div className={mod.meshes} data-testid="pl-meshes">
            {closed.length === 0 && <span className={css.note}>В проекте нет замкнутых каркасов.</span>}
            {closed.map((m) => (
              <label key={m.id}>
                <input type="checkbox" checked={src.meshIds.includes(m.id)} data-testid={`pl-mesh-${m.id}`} onChange={(e) => toggleMesh(m.id, e.target.checked)} />
                {m.name}
              </label>
            ))}
          </div>
        )}
        <label className={css.formRow}>
          <span>Набор для ближайших</span>
          <select className={css.input} value={setId ?? ""} aria-label="Набор для ближайших скважин" data-testid="pl-eval-set" onChange={(e) => setCheckSet(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{sets ? "— не искать ближайшие —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <span className={css.note}>Ближайшие скважины — до 5, у цели (без цели — у забоя); набор по умолчанию — тот, что сохранён со скважиной на вкладке «Параметры».</span>
        <div className={css.row}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Посчитать прогнозные интервалы и ближайшие скважины"} data-testid="pl-evaluate" onClick={go}>
            Прогноз
          </button>
          {run.phase === "loading" && (
            <span className={css.note} role="status" data-testid="pl-evaluate-busy">
              Считаю…
            </span>
          )}
          {reason && run.phase !== "loading" && (
            <span className={css.note} data-testid="pl-evaluate-reason">
              {reason}
            </span>
          )}
        </div>
        {run.error && (
          <p className={css.error} role="alert" data-testid="pl-evaluate-error">
            {run.error}
          </p>
        )}
      </fieldset>
      {a && (
        <div className={mod.page} data-testid="pl-evaluate-answer" style={{ padding: 0, marginTop: 10 }}>
          <dl className={mod.facts}>
            <div className={mod.fact}>
              <dt>Длина скважины</dt>
              <dd>{lengthText(a.length ?? row?.meta.length ?? null)}</dd>
            </div>
            {a.inside !== null && (
              <div className={mod.fact}>
                <dt>Внутри тел</dt>
                <dd data-testid="pl-inside">{lengthText(a.inside)}</dd>
              </div>
            )}
            <div className={mod.fact} title="Точка, у которой считаются ближайшие скважины: цель, а без цели — забой">
              <dt>Точка у цели</dt>
              <dd>
                {xyzLine(a.target.point)}
                {a.target.depth !== null ? `, глубина ${lengthText(a.target.depth)}` : ""}
              </dd>
            </div>
          </dl>
          {a.intervals && (
            <fieldset className={css.group}>
              <legend>Прогнозные интервалы</legend>
              <div className={css.tableWrap}>
                <table className={css.table} data-testid="pl-intervals">
                  <thead>
                    <tr>
                      <th>От, м</th>
                      <th>До, м</th>
                      <th>Длина, м</th>
                      <th>Тело</th>
                    </tr>
                  </thead>
                  <tbody>
                    {a.intervals.map((i, k) => (
                      <tr key={k} data-code={i.code ?? ""}>
                        <td className={mod.num}>{fixed(i.from)}</td>
                        <td className={mod.num}>{fixed(i.to)}</td>
                        <td className={mod.num}>{fixed(i.to - i.from)}</td>
                        <td className={i.code === null ? mod.codeNone : undefined}>{codeText(i.code)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {a.codes && a.codes.length > 0 && (
                <p className={css.note} data-testid="pl-codes">
                  В телах: {a.codes.map((x) => `${x.code} — ${lengthText(x.length)}`).join("; ")}.
                </p>
              )}
            </fieldset>
          )}
          {a.nearest && (
            <fieldset className={css.group}>
              <legend>Ближайшие скважины</legend>
              {a.nearest.length === 0 ? (
                <p className={css.note} data-testid="pl-nearest-none">
                  В наборе нет скважин с траекторией.
                </p>
              ) : (
                <div className={css.tableWrap}>
                  <table className={css.table} data-testid="pl-nearest">
                    <thead>
                      <tr>
                        <th>Скважина</th>
                        <th>Расстояние, м</th>
                        <th>Глубина по ней, м</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.nearest.map((n) => (
                        <tr key={n.hole_id}>
                          <td>{n.hole}</td>
                          <td className={mod.num}>{fixed(n.distance)}</td>
                          <td className={mod.num}>{fixed(n.depth)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </fieldset>
          )}
          {a.journal.map((t, k) => (
            <p key={k} className={css.note} data-testid="pl-journal">
              {t}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
