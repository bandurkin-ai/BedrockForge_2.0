/**
 * The window «Построение ЦТМ» (ТЗ 7.20, 7.35 Д-07, Д-22; ADR 0011 §3, §5, ADR 0014 §5): the sources — layers of
 * points and lines, point clouds (classes, step of the thinning; `CloudSources.tsx`) and the collars of drillhole
 * sets — the boundary, the longest edge, the name, «Сразу в раздел ЦТМ» → the job `model.dtm`, seen in «Задания»;
 * the surface appears in the tree when it is done. `cloudId` — the cloud the window opens with
 * (`terrain.from_points`); a single cloud goes to `POST /wireframes/from-points` (`requestOf`).
 */
import { useMemo, useState } from "react";
import { api } from "../../shell/api";
import { useApp } from "../../shell/AppContext";
import { showJobs } from "../../shell/objectRun";
import { errorText, toggleId } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { layerCounts, shownLayers } from "../lines/tree";
import { sceneObjects } from "../scene/objects";
import { tablesStore } from "../tables/state";
import { boundaryLayers, buildBody, initialForm, sourcesText, type BuildForm } from "./build";
import { requestOf } from "./cloud";
import { CloudSources } from "./CloudSources";

export function BuildDtmWindow({ winId, cloudId }: { winId: string; cloudId?: number }) {
  const app = useApp();
  const files = useStore(sceneObjects, (o) => o.files);
  const sets = useStore(tablesStore, (s) => s.sets);
  const [form, setForm] = useState<BuildForm>(() => {
    const selected = app.store.get().selected;
    return initialForm(sceneObjects.get().files, cloudId ?? (selected?.kind === "lines" ? selected.id : null));
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const patch = (p: Partial<BuildForm>) => setForm((f) => ({ ...f, ...p }));
  const layers = useMemo(() => new Set(form.layerIds), [form.layerIds]);
  const outlines = boundaryLayers(files);
  const outline = outlines.find((l) => l.id === form.boundaryLayer);
  const built = buildBody(form, files);
  const clouds = files.flatMap((f) => f.layers).filter((l) => !!l.cloud);
  const lineFiles = files.map((f) => ({ ...f, layers: f.layers.filter((l) => !l.cloud) })).filter((f) => f.layers.length);
  const cloudOnly = !!built.body && requestOf(built.body).path === "/wireframes/from-points";

  const start = async () => {
    if (!built.body) return;
    setBusy(true);
    try {
      const { path, body } = requestOf(built.body);
      await api.post(path, body);
      showJobs(app);
      await refreshJobs();
      closeWindow(winId);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <div className={css.win}>
      <div className={css.winBody}>
        <label className={css.formRow}>
          <span>Название</span>
          <input className={css.input} value={form.name} placeholder="ЦТМ: по названиям источников" aria-label="Название ЦТМ" maxLength={400} onChange={(e) => patch({ name: e.target.value })} />
        </label>
        <fieldset className={css.group}>
          <legend>Источники: точки и линии</legend>
          {lineFiles.length === 0 && sets.length === 0 && (
            <p className={css.note}>{clouds.length ? "Слоёв точек и линий и наборов скважин нет: источник — облако точек ниже." : "В проекте нет ни слоёв точек и линий, ни облаков, ни наборов скважин: загрузите рельеф («Главная › Загрузка › Линии» или «Облако точек»)."}</p>
          )}
          <div className={css.layers} data-testid="dtm-sources">
            {lineFiles.map((f) => {
              const { layers: rows, rest } = shownLayers(f); // a DXF may hold thousands of layers
              return (
                <div key={f.id} style={{ display: "contents" }}>
                  <span className={css.file}>
                    {f.name}
                    <span className={css.count}>{count(f.layers.length)}</span>
                  </span>
                  {rows.map((l) => (
                    <label key={l.id} className={`${css.check} ${css.layer}`}>
                      <input type="checkbox" checked={layers.has(l.id)} aria-label={`Слой «${l.name}»`} onChange={(e) => patch({ layerIds: toggleId(form.layerIds, l.id, e.target.checked) })} />
                      {l.name}
                      <span className={css.count}>
                        {layerCounts(l)}
                        {l.flat ? ", без высот" : ""}
                      </span>
                    </label>
                  ))}
                  {rest > 0 && <span className={`${css.note} ${css.layer}`}>… и ещё слоёв: {count(rest)}</span>}
                </div>
              );
            })}
            {sets.map((s) => (
              <label key={`s${s.id}`} className={css.check}>
                <input type="checkbox" checked={form.setIds.includes(s.id)} aria-label={`Устья набора «${s.name}»`} onChange={(e) => patch({ setIds: toggleId(form.setIds, s.id, e.target.checked) })} />
                Устья набора «{s.name}»<span className={css.count}>{count(s.holes)} скв.</span>
              </label>
            ))}
          </div>
          <label className={css.check} title="Отрезки линий становятся рёбрами поверхности: бровки и подошвы не срезаются">
            <input type="checkbox" checked={form.breaklines} onChange={(e) => patch({ breaklines: e.target.checked })} />
            Линии — структурные (их отрезки — рёбра поверхности)
          </label>
        </fieldset>
        <CloudSources clouds={clouds} picks={form.clouds} onChange={(next) => patch({ clouds: next })} />
        <p className={css.note} data-testid="dtm-sources-total">
          {sourcesText(form, files, sets)}. Вершина без высоты пропускается — нулём она не считается.
        </p>
        <fieldset className={css.group}>
          <legend>Граница</legend>
          <label className={css.formRow}>
            <span>Слой с контуром</span>
            <select className={css.input} value={form.boundaryLayer ?? 0} aria-label="Слой границы" onChange={(e) => patch({ boundaryLayer: Number(e.target.value) || null, boundaryLine: "" })}>
              <option value={0}>— без границы —</option>
              {outlines.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} (замкнутых линий: {l.closed_count})
                </option>
              ))}
            </select>
          </label>
          {outline && (
            <>
              <label className={css.formRow}>
                <span>Номер линии границы</span>
                <input className={css.input} inputMode="numeric" value={form.boundaryLine} placeholder={outline.closed_count > 1 ? `от 1 до ${outline.line_count}` : "единственная замкнутая"} aria-label="Номер линии границы" onChange={(e) => patch({ boundaryLine: e.target.value })} />
              </label>
              <p className={css.note}>Номер линии — как в таблице атрибутов слоя: щёлкните линию в 3D-окне, её номер покажут «Свойства».</p>
              <label className={css.check} title="Оцифрованный контур часто лежит на Z = 0: без галочки он берёт высоты поверхности">
                <input type="checkbox" checked={form.boundaryZ} onChange={(e) => patch({ boundaryZ: e.target.checked })} />
                Учитывать высоты границы
              </label>
            </>
          )}
          {!outlines.length && <p className={css.note}>Слоёв с замкнутой линией нет: без границы поверхность покрывает всю область точек.</p>}
        </fieldset>
        <label className={css.formRow}>
          <span>Наибольшая длина ребра, м</span>
          <input className={css.input} inputMode="decimal" value={form.maxEdge} placeholder={cloudOnly ? "по облаку: 5 шагов ткани фильтра" : "без ограничения"} aria-label="Наибольшая длина ребра, м" onChange={(e) => patch({ maxEdge: e.target.value })} />
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={form.toDtm} onChange={(e) => patch({ toDtm: e.target.checked })} />
          Сразу в раздел «ЦТМ»
        </label>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{built.error ?? "Построение идёт как задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={!built.body || busy} onClick={() => void start()}>
          Построить
        </button>
      </div>
    </div>
  );
}
