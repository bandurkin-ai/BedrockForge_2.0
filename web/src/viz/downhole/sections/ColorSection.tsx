import { useMemo } from "react";
import type { GradientStretch, Track, TrackGradient } from "../../../charts/stripLog";
import { CategoryEditor } from "../CategoryEditor";
import { classBounds, classesFromBounds } from "../classify";
import { ClassEditor } from "../ClassEditor";
import { viewKind, type LayerCtx } from "../context";
import { GRADIENTS, gradientCss, gradientHex } from "../gradients";
import { axisPosition, buildHistogram } from "../histogram";
import { HistogramPlot } from "../HistogramPlot";
import { StyleBar, type StyleLib } from "../StyleBar";
import { applyPalette, paletteBody, paletteFits, type StyleRec } from "../styles";
import { ColorField, NumField, parseNum, Row, Section, Segmented, showNum } from "../ui";
import { NO_DATA_COLOR } from "../../downholeGeometry";

type Mode = NonNullable<Track["colorBy"]>;
const DEFAULT_GRADIENT: TrackGradient = { name: "classic", reverse: false, stretch: "linear", min: "", max: "" };

export const colorMode = (t: Track, numeric: boolean): Mode =>
  t.colorBy ?? (numeric && (t.type === "color" || t.type === "color_text") ? "scale" : "single");

/** Colour of the layer: one colour, grade classes on a histogram, a continuous scale, another field; categories of a text field. */
export function ColorSection({ ctx, lib, used, onSavePalette, onUpdatePalette }: {
  ctx: LayerCtx;
  lib: StyleLib;
  /** colours already used in the display */
  used: string[];
  onSavePalette: (name: string) => Promise<void>;
  onUpdatePalette: () => Promise<void>;
}) {
  const { track: t, patch, numeric, values, range } = ctx;
  const mode = colorMode(t, numeric);
  const kind = viewKind(t, ctx.tube);
  const g = t.gradient ?? { ...DEFAULT_GRADIENT, stretch: (t.log ? "log" : "linear") as GradientStretch };
  const setG = (p: Partial<TrackGradient>, key?: string) => patch({ gradient: { ...g, ...p } }, key);
  const lo = parseNum(g.min) ?? range?.[0] ?? null;
  const hi = parseNum(g.max) ?? range?.[1] ?? null;
  const hist = useMemo(() => (values && mode === "scale" ? buildHistogram(values, 48, g.stretch === "log") : null), [values, mode, g.stretch]);
  const palettes = lib.styles.filter((s) => s.level === "palette" && paletteFits(s, numeric));
  const own = !numeric || mode === "field";
  const others = (ctx.table?.columns ?? []).filter((c) => c.name !== t.field);
  const text = (v: number | null) => (v === null ? "" : String(Number(v.toPrecision(6))));

  const summary = !numeric
    ? `категории${t.hidden?.length ? ` · скрыто ${t.hidden.length}` : ""}`
    : mode === "single" ? "один цвет"
      : mode === "classes" ? `градации: ${(t.classes ?? []).length}${t.palette ? ` · ${t.palette.name}` : ""}`
        : mode === "scale" ? `шкала ${showNum(lo, 3)} – ${showNum(hi, 3)}`
          : `по полю ${t.colorField ?? "—"}`;

  const setMode = (m: Mode) => {
    if (m === "classes" && !(t.classes?.length)) {
      const bounds = values ? classBounds(values.values, t.log ? "log" : "quantile", 5) : [];
      patch({ colorBy: m, classes: classesFromBounds(bounds.length ? bounds : [1]) });
    } else if (m === "field") {
      patch({ colorBy: m, colorField: t.colorField ?? others.find((c) => c.kind === "text")?.name ?? others[0]?.name });
    } else {
      patch({ colorBy: m });
    }
  };
  const applyStyle = (s: StyleRec) => {
    const next = applyPalette(t, s, false);
    patch({ colorBy: next.colorBy, classes: next.classes, gradient: next.gradient, catColors: next.catColors, hidden: next.hidden, catOrder: next.catOrder, palette: next.palette });
  };
  const canSave = paletteBody(t, numeric) !== null;

  return (
    <Section id="color" title="Цвет" summary={summary} open>
      {numeric && (
        <Segmented value={mode} label="Цвет слоя" options={[
          ...(kind === "tube" || kind === "ribbon" ? [] : [{ value: "single" as Mode, label: "Один", title: "Один цвет для всех значений" }]),
          { value: "classes", label: "Градации", title: "Цвет по классам содержаний: границы задаются на гистограмме" },
          { value: "scale", label: "Шкала", title: "Непрерывная шкала: цвет плавно меняется со значением" },
          { value: "field", label: "По полю", title: "Цвет по значению другого поля той же таблицы (например, столбцы AU цветом литологии)" },
        ]} onChange={setMode} />
      )}
      {(mode === "classes" || mode === "scale" || !numeric) && mode !== "field" && (
        <StyleBar
          level="palette" lib={lib} options={palettes} current={t.palette} fieldName={`${t.field} раскраска`}
          onApply={applyStyle}
          onSaveAs={canSave ? onSavePalette : () => Promise.reject(new Error("У слоя один цвет: сохранять нечего"))}
          onUpdate={onUpdatePalette}
          onLink={(linked) => t.palette && patch({ palette: { ...t.palette, linked, modified: false } })}
          onClear={() => patch({ palette: undefined })}
        />
      )}
      {numeric && mode === "single" && (
        <Row label="Цвет" title="Цвет слоя"><ColorField value={t.color} label="Цвет слоя" extra={used} onChange={(c) => patch({ color: c }, "color")} /></Row>
      )}
      {numeric && mode === "classes" && (
        <ClassEditor
          values={values} classes={t.classes ?? []} log={t.log} noData={t.noDataColor ?? NO_DATA_COLOR}
          onChange={(classes, key) => patch({ classes }, key)} onNoData={(c) => patch({ noDataColor: c }, "nodata")}
        />
      )}
      {numeric && mode === "scale" && (
        <>
          <div className="dh-gradients" role="radiogroup" aria-label="Градиент">
            {GRADIENTS.map((x) => (
              <button key={x.name} type="button" role="radio" aria-checked={g.name === x.name} className={`dh-gradient${g.name === x.name ? " active" : ""}`} title={x.title} onClick={() => setG({ name: x.name })}>
                <i style={{ background: gradientCss(x.name, g.reverse) }} />{x.title}
              </button>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={g.reverse} onChange={(e) => setG({ reverse: e.target.checked })} /> обратный порядок цветов</label>
          <Row label="Растяжка" title="Как значение переводится в место на градиенте">
            <Segmented value={g.stretch} label="Растяжка шкалы" options={[
              { value: "linear", label: "Линейная", title: "Равномерно между границами" },
              { value: "log", label: "Логарифм", title: "По логарифму значения" },
              { value: "quantile", label: "Квантили", title: "По доле проб: каждый цвет занимает равное число проб" },
            ]} onChange={(stretch) => setG({ stretch })} />
          </Row>
          {hist && lo !== null && hi !== null && g.stretch !== "quantile" && (
            <HistogramPlot
              hist={hist} label="Гистограмма значений с границами шкалы" showCumulative={false} strip={gradientCss(g.name, g.reverse)}
              colorAt={(v) => gradientHex(g.name, (axisPosition(hist, v) - axisPosition(hist, lo)) / (axisPosition(hist, hi) - axisPosition(hist, lo) || 1), g.reverse)}
              handles={[lo, hi]}
              onMove={(i, v) => (i === 0 ? v < hi && setG({ min: text(v) }, "gmin") : v > lo && setG({ max: text(v) }, "gmax"))}
            />
          )}
          <Row label="От – до" title="Границы цветовой шкалы. Пусто — как в разделе «Шкала»">
            <NumField value={parseNum(g.min)} label="Цветовая шкала от" placeholder={range ? showNum(range[0], 3) : "авто"} onChange={(v) => setG({ min: text(v) }, "gmin")} />
            <span className="muted">–</span>
            <NumField value={parseNum(g.max)} label="Цветовая шкала до" placeholder={range ? showNum(range[1], 3) : "авто"} onChange={(v) => setG({ max: text(v) }, "gmax")} />
            <button type="button" className="small-btn" disabled={!g.min && !g.max} title="Вернуть границы раздела «Шкала»" onClick={() => setG({ min: "", max: "" })}>как шкала</button>
          </Row>
        </>
      )}
      {numeric && mode === "field" && (
        <>
          <Row label="Поле цвета" title="Поле той же таблицы, по которому красится слой: текст — по категориям, число — по непрерывной шкале">
            <select value={t.colorField ?? ""} aria-label="Поле цвета" onChange={(e) => patch({ colorField: e.target.value })}>
              {others.map((c) => <option key={c.name} value={c.name}>{c.name}{c.kind === "text" ? " (текст)" : c.unit ? `, ${c.unit}` : ""}</option>)}
            </select>
          </Row>
          {!others.length && <span className="muted small">В таблице нет других полей.</span>}
          {t.colorField && others.find((c) => c.name === t.colorField)?.kind === "text" && (
            <CategoryEditor data={ctx.data} track={{ ...t, field: t.colorField, hidden: undefined, catOrder: undefined }} patch={(p, key) => patch({ catColors: p.catColors ?? t.catColors }, key)} />
          )}
        </>
      )}
      {!numeric && ctx.kind === "event" && ctx.column?.kind === "number" && (
        <Row label="Цвет" title="Цвет значка"><ColorField value={t.color} label="Цвет значка" extra={used} onChange={(c) => patch({ color: c }, "color")} /></Row>
      )}
      {!numeric && !(ctx.kind === "event" && ctx.column?.kind === "number") && (
        t.type === "text" && !own ? null : <CategoryEditor data={ctx.data} track={t} patch={patch} markers={t.type === "event"} />
      )}
    </Section>
  );
}
