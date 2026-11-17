/**
 * The legend of the displays along holes over the 3D view (ТЗ 7.16; BF SceneView «downhole-legend»): every
 * display drawn, its layers with the colours of the classes or categories and the number of intervals in
 * each (of the parts loaded), the range of a scale. A click on a layer opens its form with that layer
 * selected (a draft — its open form).
 */
import { useStore } from "../../shell/useStore";
import { focusWindow } from "../../shell/windows";
import { formatNumber } from "../../viz/util/numbers";
import { displaysUi, type LegendDisplay } from "./displaysUi";
import { openDisplay } from "./open";
import css from "./Legend.module.css";

const nf = new Intl.NumberFormat("ru-RU");

function open(d: LegendDisplay, trackId: string): void {
  if (d.draftKey) focusWindow(d.draftKey);
  else if (d.chartId !== null) openDisplay(d.chartId, trackId);
}

export function Legend() {
  const legend = useStore(displaysUi, (s) => s.legend);
  if (!legend.length) return null;
  return (
    <div className={css.legend} aria-label="Легенда отображений по стволу" data-testid="downhole-legend">
      {legend.map((d) => (
        <div key={d.key} className={css.display}>
          <strong className={css.name} title={`${nf.format(d.holes)} скв., ${nf.format(d.intervals)} интервалов`}>
            {d.name}
            {d.draftKey ? <span className={css.muted}> · не сохранено</span> : null}
          </strong>
          {d.intervals === 0 && <span className={css.muted}>нет интервалов на скважинах: проверьте имена скважин в таблице и в устьях</span>}
          {d.entries.map((e) => (
            <div key={e.trackId} className={css.track}>
              <button type="button" className={css.layer} title="Открыть настройки этого слоя" onClick={() => open(d, e.trackId)}>
                {e.title}
                {e.unit ? `, ${e.unit}` : ""}
              </button>
              {e.items.slice(0, 12).map(([label, color], i) => (
                <span key={`${label}-${i}`} className={css.swatch} title={e.counts[i] !== undefined ? `${nf.format(e.counts[i])} интервалов` : undefined}>
                  <i style={{ background: color }} />
                  {label}
                  {e.counts[i] ? <span className={css.muted}> · {nf.format(e.counts[i])}</span> : null}
                </span>
              ))}
              {e.items.length > 12 && <span className={css.muted}>… ещё {e.items.length - 12}</span>}
              {e.range && e.items.length === 0 && (
                <span className={css.range}>
                  {e.gradient && <i style={{ background: e.gradient }} />}
                  {formatNumber(e.range[0])} … {formatNumber(e.range[1])}
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
