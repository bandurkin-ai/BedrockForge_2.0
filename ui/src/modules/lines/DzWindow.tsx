/**
 * The window «Карта разностей» (ADR 0014 §5; ТЗ 7.35 Д-22): the cloud, the surface to compare it with — a DTM or
 * a wireframe of `GET /wireframes` (by default the one compared last, else the last DTM built from the cloud) —
 * and «Рассчитать»: the command `cloud.dz` with both runs the job, the window follows it (`dzJobs`) and shows
 * its result — СКО, P95, the largest |dz| and the points outside the surface. Once it is done the cloud has the
 * colour mode «Отклонение от ЦТМ» and «Свойства» tell the same (the core sends `objects.changed`).
 */
import { useEffect, useState } from "react";
import { Facts, useRun } from "../../shell/PropsParts";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { cloudLayers } from "../dtm/ground";
import { jobsStore } from "../jobs/state";
import { sceneObjects } from "../scene/objects";
import { defaultSurface, dzFacts, dzJobs, dzRecord, dzResult, dzSummary, jobText, surfaceOptions, type JobRef } from "./dz";

export function DzWindow({ winId, cloudId }: { winId: string; cloudId?: number }) {
  const files = useStore(sceneObjects, (o) => o.files);
  const wireframes = useStore(sceneObjects, (o) => o.wireframes);
  const jobs = useStore(jobsStore, (s) => s.jobs);
  const clouds = cloudLayers(files);
  const [chosen, setChosen] = useState<number | null>(() => cloudId ?? (clouds.length === 1 ? clouds[0].id : null));
  const cloud = clouds.find((l) => l.id === chosen) ?? null;
  const [surface, setSurface] = useState<number | null>(() => defaultSurface(cloud, sceneObjects.get().wireframes));
  const [mine, setMine] = useState<JobRef | null>(null);
  const { run, error } = useRun();
  const options = surfaceOptions(wireframes);
  const picked = options.find((o) => o.id === surface) ?? null;
  const job = mine ? jobs.find((j) => j.source === mine.source && j.id === mine.id) : undefined;
  const busy = !!mine && (!job || job.status === "queued" || job.status === "running");
  const result = job?.status === "done" ? dzResult(job.result) : null;
  const state = jobText(job);
  const why = !cloud ? "Выберите облако точек." : !picked ? "Выберите каркас или ЦТМ." : null;

  useEffect(() => {
    if (surface !== null && wireframes.some((w) => w.id === surface)) return;
    const d = defaultSurface(cloud, wireframes); // the lists came after the window opened, or the surface went
    if (d !== surface) setSurface(d);
  }, [wireframes, cloud, surface]);
  const pickCloud = (id: number | null) => {
    setChosen(id);
    setMine(null);
    setSurface(defaultSurface(clouds.find((l) => l.id === id) ?? null, wireframes));
  };
  const start = async () => {
    if (!cloud || !picked) return;
    setMine(null);
    if (await run("cloud.dz", { cloud_id: cloud.id, wireframe_id: picked.id })) setMine(dzJobs.get()[String(cloud.id)] ?? null);
  };
  const dtmList = options.filter((o) => o.dtm);
  const solidList = options.filter((o) => !o.dtm);
  const option = (o: (typeof options)[number]) => (
    <option key={o.id} value={o.id}>
      {o.name} ({count(o.triangles)} треуг.)
    </option>
  );

  return (
    <div className={css.win} data-testid="cloud-dz">
      <div className={css.winBody}>
        {clouds.length ? (
          <label className={css.formRow}>
            <span>Облако точек</span>
            <select className={css.input} aria-label="Облако точек" value={chosen ?? 0} onChange={(e) => pickCloud(Number(e.target.value) || null)}>
              {chosen === null && <option value={0}>— выберите облако —</option>}
              {clouds.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({count(l.point_count)} точек)
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className={css.note}>В проекте нет облаков точек: загрузите LAS или таблицу XYZ («Главная › Загрузка › Облако точек»).</p>
        )}
        {options.length ? (
          <label className={css.formRow}>
            <span>Поверхность</span>
            <select className={css.input} aria-label="Каркас или ЦТМ" value={surface ?? 0} onChange={(e) => setSurface(Number(e.target.value) || null)}>
              {picked === null && <option value={0}>— выберите каркас или ЦТМ —</option>}
              {dtmList.length > 0 && <optgroup label="ЦТМ">{dtmList.map(option)}</optgroup>}
              {solidList.length > 0 && <optgroup label="Каркасы">{solidList.map(option)}</optgroup>}
            </select>
          </label>
        ) : (
          <p className={css.note}>В проекте нет каркасов и ЦТМ: постройте ЦТМ из облака («Моделирование › Поверхности › Построить ЦТМ из облака») — карта разностей посчитается сразу.</p>
        )}
        {cloud && !mine && <p className={css.note} data-testid="cloud-dz-now">{dzSummary(dzRecord(cloud))}</p>}
        <p className={css.note}>dz = Z точки − высота поверхности под ней (над ней); у точки вне поверхности отклонения нет — нулём оно не считается. Прежние отклонения облака заменяются.</p>
        {state && (
          <p className={job?.status === "failed" ? css.error : css.note} role={job?.status === "failed" ? "alert" : undefined} data-testid="cloud-dz-state">
            {state}
          </p>
        )}
        {result && (
          <fieldset className={css.group} data-testid="cloud-dz-result">
            <legend>Итог{picked ? `: относительно «${picked.name}»` : ""}</legend>
            <Facts facts={dzFacts(result)} />
          </fieldset>
        )}
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{why ?? "Расчёт идёт как задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
        <button type="button" className={css.primary} disabled={!!why || busy} onClick={() => void start()}>
          Рассчитать
        </button>
      </div>
    </div>
  );
}
