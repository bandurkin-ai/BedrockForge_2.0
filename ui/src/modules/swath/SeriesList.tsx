/**
 * The series of a swath plot (ТЗ 7.30 «Ряды»: до 12 в любом сочетании; перенос `SeriesCard` BF): a card per series —
 * colour, «линия», «столбики», up, down, remove, its form; «Добавить ряд» — a block model or the samples of a
 * set. The 13th series is not added: the button is off and says why (Т7.30-1).
 */
import { useState } from "react";
import { Icon } from "../../shell/Icon";
import type { SeriesAnswer, SwathSources } from "./api";
import { addSeries, blockSeries, MAX_SERIES, moveSeries, removeSeries, replaceSeries, sampleSeries, type SwathSettings } from "./model";
import { SeriesForm } from "./SeriesForm";
import css from "./Swath.module.css";

export function SeriesList({ settings, sources, answers, onChange }: { settings: SwathSettings; sources: SwathSources | null; answers?: readonly SeriesAnswer[]; onChange: (next: SwathSettings) => void }) {
  const [menu, setMenu] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const series = settings.series;
  const full = series.length >= MAX_SERIES;
  const models = sources?.models ?? [];
  const sets = sources?.sets ?? [];
  const add = (kind: "blocks" | "samples", id: number) => {
    setMenu(false);
    let next;
    if (kind === "blocks") {
      const m = models.find((x) => x.id === id);
      const field = m ? (m.grades.find((g) => m.numbers.some((n) => n.name === g)) ?? m.numbers.find((n) => n.name !== m.density)?.name ?? null) : null;
      next = blockSeries(series, id, field, m?.density ?? null);
    } else next = sampleSeries(series, id, null, null);
    const r = addSeries(settings, next);
    setNote(r.added ? null : `Не больше ${MAX_SERIES} рядов.`);
    if (r.added) onChange(r.settings);
  };
  return (
    <fieldset>
      <legend>Ряды · {series.length} из {MAX_SERIES}</legend>
      {series.map((s, i) => (
        <div key={i} className={css.series} data-testid={`swath-series-${i}`}>
          <div className={css.seriesHead}>
            <input type="color" value={s.color} aria-label={`Цвет ряда ${i + 1}`} title="Цвет ряда" onChange={(e) => onChange(replaceSeries(settings, i, { ...s, color: e.target.value }))} />
            <strong className={css.grow}>
              Ряд {i + 1} · {s.kind === "blocks" ? "модель" : "пробы"}
            </strong>
            <button type="button" className={s.line ? css.on : undefined} aria-pressed={s.line} title="Линия средних" aria-label={`Линия ряда ${i + 1}`} onClick={() => onChange(replaceSeries(settings, i, { ...s, line: !s.line }))}>
              <Icon name="chart" size={13} />
            </button>
            <button
              type="button"
              className={s.bars ? css.on : undefined}
              aria-pressed={s.bars}
              title={s.kind === "blocks" ? "Столбики: объём (вес) блоков в полосе" : "Столбики: число проб в полосе"}
              aria-label={`Столбики ряда ${i + 1}`}
              onClick={() => onChange(replaceSeries(settings, i, { ...s, bars: !s.bars }))}
            >
              <Icon name="histogram" size={13} />
            </button>
            <button type="button" disabled={i === 0} title="Выше" aria-label={`Ряд ${i + 1} выше`} onClick={() => onChange(moveSeries(settings, i, -1))}>
              <Icon name="chevronUp" size={13} />
            </button>
            <button type="button" disabled={i === series.length - 1} title="Ниже" aria-label={`Ряд ${i + 1} ниже`} onClick={() => onChange(moveSeries(settings, i, 1))}>
              <Icon name="chevronDown" size={13} />
            </button>
            <button type="button" title="Убрать ряд" aria-label={`Убрать ряд ${i + 1}`} onClick={() => onChange(removeSeries(settings, i))}>
              <Icon name="trash" size={13} />
            </button>
          </div>
          <SeriesForm s={s} index={i} all={series} sources={sources} answer={answers?.find((a) => a.index === i)} onChange={(next) => onChange(replaceSeries(settings, i, next))} />
        </div>
      ))}
      <div className={css.add}>
        <button
          type="button"
          disabled={full || (!models.length && !sets.length)}
          aria-expanded={menu}
          title={full ? `Не больше ${MAX_SERIES} рядов` : !models.length && !sets.length ? "В проекте нет наборов скважин и блочных моделей" : "Поле блочной модели или проб набора"}
          onClick={() => setMenu((m) => !m)}
        >
          + Добавить ряд
        </button>
        {menu && (
          <div className={css.menu} role="menu">
            {models.map((m) => (
              <button key={`b${m.id}`} type="button" role="menuitem" onClick={() => add("blocks", m.id)}>
                {m.name} · модель
              </button>
            ))}
            {sets.map((x) => (
              <button key={`s${x.id}`} type="button" role="menuitem" onClick={() => add("samples", x.id)}>
                {x.name} · пробы
              </button>
            ))}
          </div>
        )}
        {(note ?? (full ? `Рядов ${MAX_SERIES} — больше не добавить.` : null)) && <p className={css.muted}>{note ?? `Рядов ${MAX_SERIES} — больше не добавить.`}</p>}
      </div>
    </fieldset>
  );
}
