/**
 * The tab «Карта опоры» of the window «Проверка по скважинам» (ТЗ 7.35 Д-32 п. 2): the surfaces of the model with the share of the area beyond
 * D_оп (what «Свойства» of the surface say too), the threshold, and «Показать карту в 3D» — the surface painted by the distance of its
 * vertices to the nearest data point. D_оп moves — the vertices are painted again at once (the milliseconds of the pass are shown).
 */
import { useEffect } from "react";
import { num } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import mod from "./Check.module.css";
import { numText } from "./checkModel";
import { useSel } from "./hooks";
import { applyThreshold, chooseSurface, hideMap, openSupport, setThresholdText, showMap, supportStore, surfaceOf, thresholdOf } from "./SupportState";
import { percent, RAMP_CSS, supportFacts } from "./supportModel";

export function SupportPanel({ modelId }: { modelId: number | null }) {
  const s = useSel(supportStore, (x) => x);
  useEffect(() => {
    const cur = supportStore.get();
    if (modelId !== null && (cur.modelId !== modelId || cur.phase === "idle")) void openSupport(modelId);
  }, [modelId]);
  const a = s.answer;
  const chosen = surfaceOf(s.surfaceId);
  const t = thresholdOf(s);
  const bad = t !== null && Number.isNaN(t);
  const threshold = a?.threshold ?? 0;
  const showing = s.shownId !== null;
  return (
    <>
      {s.error && (
        <p className={css.error} role="alert" data-testid="sup-error">
          {s.error}
        </p>
      )}
      {s.phase === "loading" && !a && (
        <p className={mod.empty} role="status" data-testid="sup-loading">
          Считаю расстояния до точек данных…
        </p>
      )}
      {a && (
        <>
          <div className={mod.bar} style={{ padding: 0, border: 0 }}>
            <label title="Поверхность, дальше этого расстояния от точек данных которой считается экстраполяцией. Пусто — 2·D_max данных.">
              <span>Порог D_оп, м</span>
              <input
                className={mod.select}
                value={s.thresholdText}
                placeholder={`по умолчанию ${numText(a.d_max * 2)} (2·D_max)`}
                aria-label="Порог D_оп, м"
                aria-invalid={bad}
                data-testid="sup-threshold"
                onChange={(e) => setThresholdText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void applyThreshold()}
                onBlur={() => void applyThreshold()}
              />
            </label>
            <label>
              <span>Поверхность</span>
              <select className={mod.select} value={s.surfaceId ?? ""} aria-label="Поверхность" data-testid="sup-surface" onChange={(e) => chooseSurface(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— выберите —</option>
                {a.surfaces.map((x) => (
                  <option key={x.wireframe_id} value={x.wireframe_id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <div className={mod.actions}>
              <button
                type="button"
                className={css.primary}
                disabled={s.painting || s.surfaceId === null || bad}
                title="Покрасить поверхность в 3D-окне по расстоянию от вершины до ближайшей точки данных"
                data-testid="sup-show"
                onClick={() => void showMap()}
              >
                {showing && s.shownId === s.surfaceId ? "Обновить карту" : "Показать карту в 3D"}
              </button>
              {showing && (
                <button type="button" className={css.btn} data-testid="sup-hide" onClick={() => hideMap()}>
                  Убрать из 3D
                </button>
              )}
            </div>
          </div>
          {a.model.stale && (
            <p className={mod.banner} role="status">
              Модель устарела{a.model.stale_reason ? `: ${a.model.stale_reason}` : ""}. Карта показывает прежние поверхности.
            </p>
          )}
          {a.warnings.map((w) => (
            <p key={w.code + w.message} className={mod.banner} role="status">
              {w.message} {w.hint}
            </p>
          ))}
          <p className={mod.info} data-testid="sup-info">
            D_max данных {numText(a.d_max)} м · порог D_оп {numText(a.threshold)} м{a.recorded ? " · доля за порогом записана в «Свойства» модели" : ""}
          </p>
          <div className={mod.tableWrap}>
            <table className={mod.table} data-testid="sup-table">
              <thead>
                <tr>
                  <th scope="col">Поверхность</th>
                  <th scope="col" className={mod.num} title="Вершин поверхности">
                    Вершин
                  </th>
                  <th scope="col" className={mod.num}>
                    Площадь, м²
                  </th>
                  <th scope="col" className={mod.num} title="Часть площади поверхности, у вершин которой расстояние до данных больше порога D_оп">
                    За порогом
                  </th>
                  <th scope="col" className={mod.num}>
                    Наибольшее, м
                  </th>
                </tr>
              </thead>
              <tbody>
                {a.surfaces.map((x) => (
                  <tr key={x.wireframe_id} data-picked={x.wireframe_id === s.surfaceId} data-testid={`sup-row-${x.wireframe_id}`} onClick={() => chooseSurface(x.wireframe_id)}>
                    <td>{x.name}</td>
                    <td className={mod.num}>{num(x.vertices, 0)}</td>
                    <td className={mod.num}>{x.area === null ? "—" : num(x.area, 0)}</td>
                    <td className={mod.num} data-testid={`sup-share-${x.wireframe_id}`}>
                      {percent(x.share_beyond)}
                    </td>
                    <td className={mod.num}>{numText(x.max_distance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!a.surfaces.length && <p className={mod.empty}>У модели нет поверхностей: постройте её командой «Обновить».</p>}
          </div>
          {chosen && (
            <dl className={mod.facts} data-testid="sup-facts">
              {supportFacts(chosen, threshold).map((f) => (
                <div key={f.label} className={mod.fact}>
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {s.note && (
            <p className={css.note} role="status" data-testid="sup-note">
              {s.note}
            </p>
          )}
          {showing && (
            <div className={mod.legend} data-testid="sup-legend">
              <span>0</span>
              <span className={mod.ramp} style={{ background: RAMP_CSS }} aria-hidden="true" />
              <span>2·D_оп</span>
              <span>
                расстояние до ближайшей точки данных; D_оп = {numText(threshold)} м{s.counts ? `; вершин за порогом ${num(s.counts.beyond, 0)} из ${num(s.counts.vertices, 0)}` : ""}
                {s.counts && s.counts.noData ? `, без данных ${num(s.counts.noData, 0)}` : ""}
                {s.repaintMs !== null ? `; перекраска ${num(s.repaintMs, 1)} мс` : ""}
              </span>
            </div>
          )}
        </>
      )}
    </>
  );
}
