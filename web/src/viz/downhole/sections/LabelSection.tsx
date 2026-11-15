import type { TrackLabel } from "../../../charts/stripLog";
import { defaultLabel, labelsOn, type LayerCtx } from "../context";
import { ColorField, NumField, Row, Section, Segmented, SliderField } from "../ui";

/** Text beside the intervals: a template with fields, number of digits, which values, size, colour and place. */
export function LabelSection({ ctx, displayLabels }: { ctx: LayerCtx; displayLabels: boolean | undefined }) {
  const { track: t, patch } = ctx;
  const on = labelsOn(t, displayLabels);
  const label: TrackLabel = t.label ?? { ...defaultLabel(t.field), on };
  // a colour column that wrote its text the old way keeps only the colour: the label settings write the text now
  const set = (p: Partial<TrackLabel>, key?: string) => patch({ label: { ...label, ...p }, ...(t.type === "color_text" ? { type: "color" as const } : {}) }, key);
  const fields = ["from", "to", "len", ...(ctx.table?.columns.map((c) => c.name) ?? [])];
  const summary = on ? `${label.template} · ${label.size} px${typeof label.min === "number" ? ` · ≥ ${String(label.min).replace(".", ",")}` : ""}` : "выключены";
  return (
    <Section id="label" title="Подписи" summary={summary}>
      <label className="check" title="Писать значения рядом с интервалами"><input type="checkbox" checked={on} disabled={t.type === "text"} onChange={(e) => set({ on: e.target.checked })} /> показывать подписи</label>
      {on && (
        <>
          <Row label="Шаблон" title="Текст подписи. Поля — в фигурных скобках: {AU} г/т; глубины {from}, {to}; длина {len}">
            <input value={label.template} aria-label="Шаблон подписи" onChange={(e) => set({ template: e.target.value }, "tpl")} />
            <select value="" aria-label="Вставить поле" title="Вставить поле в шаблон" onChange={(e) => e.target.value && set({ template: `${label.template}${label.template && !/\s$/.test(label.template) ? " " : ""}{${e.target.value}}` })}>
              <option value="">+ поле</option>
              {fields.map((f) => <option key={f} value={f}>{f === "from" ? "from — глубина от" : f === "to" ? "to — глубина до" : f === "len" ? "len — длина" : f}</option>)}
            </select>
          </Row>
          <Row label="Знаков" title="Число знаков после запятой у чисел">
            <select value={label.decimals === null ? "" : String(label.decimals)} aria-label="Число знаков" onChange={(e) => set({ decimals: e.target.value === "" ? null : Number(e.target.value) })}>
              <option value="">как в данных</option>
              {[0, 1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Row>
          {ctx.numeric && (
            <Row label="Только ≥" title="Подписывать только значения не ниже этого (например, рудные интервалы). Пусто — все">
              <NumField value={label.min ?? null} label="Подписывать значения от" placeholder="все" onChange={(v) => set({ min: v ?? undefined }, "lmin")} />
            </Row>
          )}
          <SliderField label="Размер" title="Размер текста на экране, px" value={label.size} min={8} max={28} step={1} unit="px" onChange={(v) => set({ size: v }, "lsize")} />
          <Row label="Цвет" title="Цвет текста: как у класса (цвет интервала) или свой">
            <Segmented value={label.color === "class" ? "class" : "own"} label="Цвет подписи" options={[
              { value: "class", label: "Как у класса", title: "Цвет интервала: класса, категории или шкалы" },
              { value: "own", label: "Свой", title: "Один цвет для всех подписей" },
            ]} onChange={(m) => set({ color: m === "class" ? "class" : "#f3f6fb" })} />
            {label.color !== "class" && <ColorField value={label.color} label="Цвет подписи" onChange={(c) => set({ color: c }, "lcolor")} />}
          </Row>
          {t.type !== "event" && (
            <Row label="Положение" title="Где писать: у внешнего конца столбца или у самого ствола">
              <Segmented value={label.place} label="Положение подписи" options={[
                { value: "outside", label: "У столбца", title: "У внешнего конца столбца (ленты)" },
                { value: "trace", label: "У ствола", title: "У самого ствола скважины" },
              ]} onChange={(place) => set({ place })} />
            </Row>
          )}
          {t.type !== "event" && (
            <Row label="Не больше" title="Наибольшее число подписей слоя. Если интервалов больше, остаются подписи самых высоких значений">
              <NumField value={label.limit ?? null} label="Наибольшее число подписей" placeholder="2500" min={1} max={2500} onChange={(v) => set({ limit: v ? Math.round(v) : undefined }, "llimit")} />
            </Row>
          )}
          <span className="muted small">Подпись видна, когда её интервал на экране не короче 18 px: приблизьте вид.</span>
        </>
      )}
    </Section>
  );
}
