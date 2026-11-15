import { MARKER_TITLES, type MarkerShape, type Track, type TrackSide } from "../../../charts/stripLog";
import { defaultLabel, viewKind, widthOf, type LayerCtx, type ViewKind } from "../context";
import { NumField, Row, Section, Segmented, SliderField, type SegOption } from "../ui";

const KINDS: Record<ViewKind, SegOption<ViewKind>> = {
  bars: { value: "bars", label: "Столбцы", icon: "▮▮", title: "Столбцы: отдельный столбец на каждый интервал" },
  steps: { value: "steps", label: "Ступени", icon: "▟", title: "Ступени: сомкнутый ступенчатый контур с заливкой" },
  line: { value: "line", label: "Линия", icon: "╱╲", title: "Линия: ломаная по серединам интервалов" },
  points: { value: "points", label: "Точки", icon: "•••", title: "Точки: символ на каждый интервал" },
  tube: { value: "tube", label: "Трубка", icon: "◉", title: "Трубка: цвет по самому стволу скважины" },
  ribbon: { value: "ribbon", label: "Лента", icon: "▌", title: "Лента: цветная полоса рядом со стволом" },
  text: { value: "text", label: "Текст", icon: "Т", title: "Текст: только подпись значения рядом со стволом" },
  event: { value: "event", label: "Значки", icon: "◆", title: "Значок на глубине события" },
};
const SIDES: SegOption<TrackSide>[] = [
  { value: "left", label: "Слева", icon: "◀", title: "Слева от ствола на экране" },
  { value: "right", label: "Справа", icon: "▶", title: "Справа от ствола на экране" },
  { value: "both", label: "В обе", icon: "◀▶", title: "Зеркально в обе стороны от ствола" },
];
const KIND_WORD: Record<ViewKind, string> = { bars: "столбцы", steps: "ступени", line: "линия", points: "точки", tube: "трубка", ribbon: "лента", text: "текст", event: "значки" };

export function kindsFor(ctx: Pick<LayerCtx, "kind">): ViewKind[] {
  return ctx.kind === "event" ? ["event"] : ctx.kind === "number" ? ["bars", "steps", "line", "points", "tube", "ribbon", "text"] : ["tube", "ribbon", "text"];
}

/** The change of a layer that makes it another kind of mark. */
export function kindPatch(t: Track, kind: ViewKind, numeric: boolean): Partial<Track> {
  if (kind === "tube" || kind === "ribbon") {
    return {
      type: t.type === "color_text" ? "color_text" : "color", form: kind === "tube" ? "tube" : "panel",
      colorBy: numeric && (!t.colorBy || t.colorBy === "single") ? "scale" : t.colorBy,
    };
  }
  if (kind === "text") return { type: "text", label: t.label ? { ...t.label, on: true } : defaultLabel(t.field) };
  if (kind === "event") return { type: "event" };
  return { type: kind, colorBy: t.colorBy ?? "single" };
}

/** How the layer is drawn: the kind of mark, its side, width, offset, opacity and what the kind adds. */
export function ViewSection({ ctx, tubePx, onTube }: { ctx: LayerCtx; tubePx: number; onTube: (px: number) => void }) {
  const { track: t, patch, panelWidth } = ctx;
  const kind = viewKind(t, ctx.tube);
  const beside = kind !== "tube" && kind !== "event";
  const graph = kind === "bars" || kind === "steps" || kind === "line" || kind === "points";
  const width = widthOf(t, panelWidth);
  const side = typeof t.split === "number" ? "both" : t.side ?? "right";
  const maxW = Math.max(60, Math.round(panelWidth * 6));
  const summary = `${KIND_WORD[kind]}${beside ? ` · ${side === "left" ? "слева" : side === "right" ? "справа" : "в обе стороны"} · ${String(Number(width.toPrecision(3))).replace(".", ",")} м` : ""}${(t.opacity ?? 1) < 1 ? ` · ${Math.round((t.opacity ?? 1) * 100)} %` : ""}`;
  const radius = t.radius ?? { by: "fixed" as const, min: 2, max: tubePx, log: t.log };
  return (
    <Section id="view" title="Вид" summary={summary} open>
      <Segmented value={kind} label="Вид слоя" options={kindsFor(ctx).map((k) => KINDS[k])} onChange={(k) => patch(kindPatch(t, k, ctx.numeric))} />
      {beside && (
        <>
          <Row label="Сторона" title="С какой стороны ствола на экране рисовать слой">
            <Segmented value={side} label="Сторона" options={SIDES} disabled={typeof t.split === "number"} onChange={(s) => patch({ side: s })} />
          </Row>
          <SliderField label="Ширина" title="Ширина колонки слоя в метрах сцены" value={Number(width.toPrecision(3))} min={0.5} max={maxW} step={0.5} unit="м" onChange={(v) => patch({ widthM: Math.max(0.1, v) }, "widthM")} />
          <Row label="" title="Ширина по умолчанию: около двадцатой части типичной скважины">
            <button type="button" className="small-btn" disabled={!t.widthM} onClick={() => patch({ widthM: undefined, width: 90 })}>авто ({panelWidth} м)</button>
          </Row>
          <Row label="Отступ" title="Расстояние от ствола до слоя. Авто: вплотную за предыдущим слоем с той же стороны">
            <label className="check"><input type="checkbox" checked={typeof t.offsetM !== "number"} onChange={(e) => patch({ offsetM: e.target.checked ? undefined : Number((panelWidth * 0.15).toPrecision(2)) })} /> авто</label>
            {typeof t.offsetM === "number" && <NumField value={t.offsetM} label="Отступ от ствола, м" unit="м" min={0} onChange={(v) => patch({ offsetM: v ?? 0 }, "offsetM")} />}
          </Row>
        </>
      )}
      {kind === "tube" && (
        <>
          <Row label="Толщина" title="Толщина трубки на экране: постоянная или по значению поля">
            <Segmented value={ctx.numeric ? radius.by : "fixed"} label="Толщина трубки" disabled={!ctx.numeric} options={[
              { value: "fixed", label: "Постоянная", title: "Одна толщина по всему стволу" },
              { value: "value", label: "По значению", title: "Чем выше значение, тем толще трубка (несколько ступеней толщины)" },
            ]} onChange={(by) => patch({ radius: { ...radius, by, min: by === "value" ? Math.min(radius.min, 3) : radius.min, max: by === "value" ? Math.max(radius.max, 12) : radius.max } })} />
          </Row>
          {radius.by === "value" && ctx.numeric ? (
            <Row label="От – до" title="Толщина трубки для минимума и максимума шкалы, px">
              <NumField value={radius.min} label="Толщина для минимума, px" unit="px" min={1} max={40} onChange={(v) => patch({ radius: { ...radius, min: v ?? 2 } }, "rmin")} />
              <span className="muted">–</span>
              <NumField value={radius.max} label="Толщина для максимума, px" unit="px" min={1} max={40} onChange={(v) => patch({ radius: { ...radius, max: v ?? 12 } }, "rmax")} />
              <label className="check" title="Толщина растёт по логарифму значения"><input type="checkbox" checked={radius.log} onChange={(e) => patch({ radius: { ...radius, log: e.target.checked } })} /> лог</label>
            </Row>
          ) : (
            <SliderField label="Толщина" title="Толщина трубки на экране, px" value={t.radius?.by === "fixed" ? t.radius.max : tubePx} min={2} max={24} step={1} unit="px"
              onChange={(v) => (t.radius ? patch({ radius: { ...radius, by: "fixed", max: v } }, "rfix") : onTube(v))} />
          )}
        </>
      )}
      {kind === "bars" && <SliderField label="Зазор" title="Часть интервала, остающаяся пустой между столбцами" value={Math.round((t.gap ?? 0) * 100)} min={0} max={80} step={5} unit="%" onChange={(v) => patch({ gap: v / 100 }, "gap")} />}
      {kind === "steps" && <label className="check" title="Заливать площадь под ступенчатым контуром"><input type="checkbox" checked={t.fill !== false} onChange={(e) => patch({ fill: e.target.checked })} /> заливка</label>}
      {kind === "line" && <label className="check" title="Точка в середине каждого интервала"><input type="checkbox" checked={!!t.dots} onChange={(e) => patch({ dots: e.target.checked })} /> точки на линии</label>}
      {(kind === "points" || (kind === "line" && t.dots)) && <SliderField label="Размер точки" title="Размер точки на экране, px" value={t.pointSize ?? (kind === "points" ? 7 : 5)} min={2} max={24} step={1} unit="px" onChange={(v) => patch({ pointSize: v }, "psize")} />}
      {kind === "event" && (
        <>
          <Row label="Значок" title="Значок события">
            <Segmented value={t.marker ?? "diamond"} label="Значок" options={(Object.keys(MARKER_TITLES) as MarkerShape[]).map((m) => ({ value: m, label: "", icon: MARKER_ICONS[m], title: MARKER_TITLES[m] }))} onChange={(m) => patch({ marker: m })} />
          </Row>
          <SliderField label="Размер" title="Размер значка на экране, px" value={t.size ?? 12} min={4} max={40} step={1} unit="px" onChange={(v) => patch({ size: v }, "msize")} />
        </>
      )}
      <SliderField label="Прозрачность" title="0 % — слой непрозрачен" value={Math.round((1 - (t.opacity ?? 1)) * 100)} min={0} max={95} step={5} unit="%" onChange={(v) => patch({ opacity: 1 - v / 100 }, "opacity")} />
      {beside && kind !== "text" && <label className="check" title="Тонкие линии на границах интервалов"><input type="checkbox" checked={t.boundaries} onChange={(e) => patch({ boundaries: e.target.checked })} /> границы интервалов</label>}
      {graph && (
        <Row label="Разделение" title="Порог: значения ниже порога рисуются влево от ствола, выше — вправо">
          <label className="check"><input type="checkbox" checked={typeof t.split === "number"} onChange={(e) => patch({ split: e.target.checked ? Number(((ctx.range?.[0] ?? 0) + ((ctx.range?.[1] ?? 1) - (ctx.range?.[0] ?? 0)) / 2).toPrecision(2)) : null })} /> по порогу</label>
          {typeof t.split === "number" && <NumField value={t.split} label="Порог разделения" onChange={(v) => patch({ split: v ?? 0 }, "split")} />}
        </Row>
      )}
    </Section>
  );
}

export const MARKER_ICONS: Record<MarkerShape, string> = { circle: "●", square: "■", diamond: "◆", triangle: "▲", cross: "✚", star: "★" };
