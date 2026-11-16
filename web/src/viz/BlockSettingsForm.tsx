import { useEffect, useMemo, useState } from "react";
import { blockMeta, getBlockValues, getChart, putStyle, type BlockAttribute, type Dataset, type StyleRecord } from "../api";
import { defaultClasses, withDefaults, type StripLogSettings } from "../charts/stripLog";
import { askConfirm, askText } from "../shell/dialogs";
import { unitTitle } from "../shell/BlockModelPanel";
import {
  categoryColor, NO_FILTER, OP_TITLES, opNeedsValue, opsFor, paletteFitsBlock, paletteOf, percentileRange, sampleValues, withPalette,
  type BlockCondition, type BlockOp, type BlockStyle,
} from "./blockStyle";
import { ClassEditor } from "./downhole/ClassEditor";
import { GRADIENTS, gradientCss } from "./downhole/gradients";
import type { FieldValues } from "./downhole/histogram";
import { StyleSwatch } from "./downhole/StyleGallery";
import { paletteBody, resolveTracks, type PaletteBody, type StyleRec } from "./downhole/styles";
import { Popover } from "./downhole/ui";

const clamp = (v: string, lo: number, hi: number, fallback: number) => {
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && v.trim() !== "" ? Math.max(lo, Math.min(hi, n)) : fallback;
};

/** A colouring that can be taken for a field: a palette of the library, or a layer of a drillhole display. */
interface Source { key: string; name: string; id: number | null; body: PaletteBody }

/**
 * The settings of a block model (the gear in the tree): the restriction (which blocks are shown),
 * the colouring by a field (taken from the drillholes or the library, or set here and saved), and
 * how the model is drawn (outer shell or all blocks, the cut in a section, outlines). Applied at once.
 */
export function BlockSettingsForm({ dataset, style, onChange, projectId, styles, onStyles, displays, say }: {
  dataset: Dataset;
  style: BlockStyle;
  onChange: (next: BlockStyle) => void;
  projectId: number;
  /** the library of styles of the project */
  styles: StyleRecord[];
  onStyles: (next: StyleRecord[]) => void;
  /** saved displays of data along the holes: their colourings can be taken for the blocks */
  displays: { id: number; name: string; setName: string }[];
  say: (text: string, error?: boolean) => void;
}) {
  const meta = blockMeta(dataset);
  const attr = meta.attributes.find((a) => a.key === style.attribute) ?? null;
  const set = (patch: Partial<BlockStyle>) => onChange({ ...style, ...patch });
  /** A change of the colouring made by hand: it is no longer the palette it came from. */
  const paint = (patch: Partial<BlockStyle>) => onChange({ ...style, ...patch, palette: undefined });
  const filter = style.filter;
  const setFilter = (patch: Partial<typeof filter>) => set({ filter: { ...filter, ...patch } });
  const setCondition = (i: number, patch: Partial<BlockCondition>) =>
    setFilter({ conditions: filter.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  // Values of the colour field, for the histogram and the automatic range of the classes.
  const [values, setValues] = useState<{ key: string; all: Float32Array } | null>(null);
  const valuesKey = attr?.kind === "number" ? `${dataset.id}:${attr.key}:${JSON.stringify(attr.stats)}` : null;
  useEffect(() => {
    if (!valuesKey || !attr) return setValues(null);
    let alive = true;
    getBlockValues(dataset.id, attr.key).then((all) => alive && setValues({ key: valuesKey, all })).catch(() => alive && setValues(null));
    return () => {
      alive = false;
    };
  }, [valuesKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = values && values.key === valuesKey ? values.all : null;
  const sample = useMemo<FieldValues | null>(() => {
    if (!ready) return null;
    const sorted = sampleValues(ready);
    return { values: sorted, lengths: new Float64Array(sorted.length).fill(1), rows: sorted.length };
  }, [ready]);

  // Colourings of the saved drillhole displays (loaded when the list is first opened).
  const [fromHoles, setFromHoles] = useState<{ name: string; settings: StripLogSettings }[] | null>(null);
  const displayKey = displays.map((d) => d.id).join();
  useEffect(() => {
    let alive = true;
    setFromHoles(null);
    void Promise.all(displays.map((d) => getChart(d.id).then((c) => ({ name: `${d.setName} · ${d.name}`, settings: withDefaults(c.settings as Partial<StripLogSettings>) })).catch(() => null)))
      .then((list) => alive && setFromHoles(list.filter((x): x is { name: string; settings: StripLogSettings } => x !== null)));
    return () => {
      alive = false;
    };
  }, [displayKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const library = styles as StyleRec[];
  const sources = useMemo(() => {
    if (!attr) return { holes: [] as Source[], lib: [] as Source[] };
    const holes: Source[] = [];
    for (const d of fromHoles ?? []) {
      // Layers linked to a style of the library take their colouring from it.
      for (const t of resolveTracks(d.settings, library).tracks) {
        const body = paletteBody(t, attr.kind === "number");
        if (paletteFitsBlock(body, attr)) holes.push({ key: `h${holes.length}`, id: null, name: `${d.name} · ${t.field}`, body });
      }
    }
    const lib = library.filter((s) => s.level === "palette" && paletteFitsBlock(s.body as PaletteBody, attr))
      .map((s): Source => ({ key: `s${s.id}`, id: s.id, name: s.name, body: s.body as PaletteBody }));
    return { holes, lib };
  }, [attr, fromHoles, library]);

  const save = async () => {
    if (!attr) return;
    const name = await askText({ title: "Раскраска: сохранить в библиотеку", message: "Название в библиотеке проекта. Раскраску можно будет взять для скважин и других моделей", value: style.palette?.name ?? attr.name });
    if (!name) return;
    if (library.some((s) => s.level === "palette" && s.name === name)
      && !(await askConfirm({ title: "Такое название уже есть", message: `Раскраска «${name}» уже есть в библиотеке. Заменить?`, ok: "Заменить", danger: true }))) return;
    try {
      const rec = await putStyle(projectId, {
        name, level: "palette", data_kind: attr.kind === "number" ? "number" : "text",
        body: paletteOf(style, attr) as unknown as Record<string, unknown>,
        default_for: library.find((s) => s.level === "palette" && s.name === name)?.default_for ?? [],
      });
      onStyles([...styles.filter((s) => s.id !== rec.id), rec]);
      set({ palette: { id: rec.id, name: rec.name } });
      say(`Раскраска «${rec.name}» сохранена в библиотеке проекта`);
    } catch (e) {
      say((e as Error).message, true);
    }
  };

  const pickAttribute = (key: string) => {
    const a = meta.attributes.find((x) => x.key === key);
    onChange({
      ...style, attribute: a ? a.key : null, min: "", max: "", hidden: [], catColors: {}, classes: [], colorBy: "scale",
      log: a?.unit === "g/t" || a?.unit === "ppm" || a?.unit === "ppb", palette: undefined,
    });
  };
  const toClasses = () => {
    if (style.classes.length) return paint({ colorBy: "classes" });
    const range = ready ? percentileRange(ready, style.log) : attr?.stats.min != null && attr.stats.max != null ? [attr.stats.min, attr.stats.max] as [number, number] : null;
    paint({ colorBy: "classes", classes: defaultClasses(range, style.log) });
  };
  const fieldOf = (key: string): BlockAttribute | undefined => meta.attributes.find((a) => a.key === key);
  const firstField = meta.attributes.find((a) => a.kind === "number") ?? meta.attributes[0];

  return (
    <form className="hist-settings downhole-form dh-form block-form" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Ограничитель</legend>
        <label className="check" title="Показывать только блоки, которые проходят условия">
          <input type="checkbox" checked={filter.on} onChange={(e) => setFilter({ on: e.target.checked, conditions: e.target.checked && !filter.conditions.length && firstField ? [{ field: firstField.key, op: firstField.kind === "number" ? "gt" : "eq", value: "" }] : filter.conditions })} />
          {" "}показывать только блоки по условиям
        </label>
        {filter.conditions.length > 0 && (
          <div className="block-conditions" role="table" aria-label="Условия ограничителя">
            <div className="block-condition head muted small" role="row"><span>Поле</span><span>Оператор</span><span>Значение</span><span /></div>
            {filter.conditions.map((c, i) => {
              const a = fieldOf(c.field);
              const ops = opsFor(a?.kind);
              return (
                <div key={i} className="block-condition" role="row">
                  <select value={c.field} disabled={!filter.on} aria-label={`Поле условия ${i + 1}`} onChange={(e) => {
                    const next = fieldOf(e.target.value);
                    setCondition(i, { field: e.target.value, value: "", op: opsFor(next?.kind).includes(c.op) ? c.op : "eq" });
                  }}>
                    {!a && <option value={c.field}>(нет поля)</option>}
                    {meta.attributes.map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}
                  </select>
                  <select value={c.op} disabled={!filter.on} aria-label={`Оператор условия ${i + 1}`} onChange={(e) => setCondition(i, { op: e.target.value as BlockOp })}>
                    {ops.map((op) => <option key={op} value={op}>{OP_TITLES[op]}</option>)}
                  </select>
                  {!opNeedsValue(c.op) ? <span className="muted small">—</span> : a?.kind === "category" ? (
                    <select value={c.value} disabled={!filter.on} aria-label={`Значение условия ${i + 1}`} onChange={(e) => setCondition(i, { value: e.target.value })}>
                      <option value="">— выберите —</option>
                      {(a.labels ?? []).map((l) => <option key={l} value={l}>{l}</option>)}
                    </select>
                  ) : (
                    <input value={c.value} disabled={!filter.on} inputMode="decimal" placeholder="число" aria-label={`Значение условия ${i + 1}`}
                      title={a?.stats.min != null && a.stats.max != null ? `В модели: от ${a.stats.min} до ${a.stats.max}` : undefined}
                      onChange={(e) => setCondition(i, { value: e.target.value })} />
                  )}
                  <button type="button" className="icon" title="Убрать условие" aria-label={`Убрать условие ${i + 1}`} onClick={() => setFilter({ conditions: filter.conditions.filter((_, j) => j !== i) })}>✕</button>
                </div>
              );
            })}
          </div>
        )}
        <div className="dh-tools">
          <button type="button" className="small-btn" disabled={!firstField || filter.conditions.length >= 20} title="Добавить условие"
            onClick={() => firstField && setFilter({ on: true, conditions: [...filter.conditions, { field: attr?.key ?? firstField.key, op: (attr ?? firstField).kind === "number" ? "gt" : "eq", value: "" }] })}>+ условие</button>
          {filter.conditions.length > 0 && <button type="button" className="small-btn" title="Убрать все условия" onClick={() => set({ filter: NO_FILTER })}>Очистить</button>}
        </div>
        {filter.conditions.length > 1 && (
          <div className="block-join" role="radiogroup" aria-label="Объединение условий">
            <span className="muted small">Объединение условий:</span>
            <label className="check"><input type="radio" name={`join${dataset.id}`} checked={filter.join === "and"} disabled={!filter.on} onChange={() => setFilter({ join: "and" })} /> И</label>
            <label className="check"><input type="radio" name={`join${dataset.id}`} checked={filter.join === "or"} disabled={!filter.on} onChange={() => setFilter({ join: "or" })} /> Или</label>
          </div>
        )}
        {filter.conditions.length > 0 && (
          <label className="check" title="Показывать блоки, которые условия не проходят">
            <input type="checkbox" checked={filter.invert} disabled={!filter.on} onChange={(e) => setFilter({ invert: e.target.checked })} /> обратный фильтр
          </label>
        )}
      </fieldset>

      <fieldset>
        <legend>Раскраска</legend>
        <label>Цвет по полю
          <select value={style.attribute ?? ""} onChange={(e) => pickAttribute(e.target.value)} aria-label="Цвет по полю">
            <option value="">— один цвет —</option>
            {meta.attributes.map((a) => <option key={a.key} value={a.key}>{a.name}{a.kind === "category" ? " (категории)" : a.unit ? `, ${unitTitle(a.unit)}` : ""}</option>)}
          </select>
        </label>
        {!attr && <label className="color-pick">Цвет блоков<input type="color" value={style.color} aria-label="Цвет блоков" onChange={(e) => set({ color: e.target.value })} /></label>}
        {attr && (
          <div className="dh-stylebar">
            <Popover label="Взять раскраску" title="Взять раскраску, сохранённую для скважин, или из библиотеки проекта (копией)" className="dh-style-pick"
              button={<><span className="grow">{style.palette ? style.palette.name : "Взять раскраску…"}</span><span aria-hidden>▾</span></>}>
              {(close) => (
                <div className="dh-gallery">
                  {([["Из скважин (сохранённые отображения)", sources.holes], ["Библиотека проекта", sources.lib]] as [string, Source[]][]).map(([title, list]) => (
                    <div key={title} className="dh-gallery-group">
                      <span className="muted small">{title}</span>
                      {list.map((s) => (
                        <button key={s.key} type="button" role="menuitem" className="dh-gallery-item" title={`Раскрасить блоки как «${s.name}»`}
                          onClick={() => { onChange(withPalette(style, s.body, s)); close(); }}>
                          <StyleSwatch style={{ level: "palette", body: s.body as unknown as Record<string, unknown> }} />
                          <span className="grow">{s.name}</span>
                        </button>
                      ))}
                      {!list.length && (
                        <span className="muted small block-none">
                          {title.startsWith("Из") ? (fromHoles === null ? "Загрузка…" : `Нет подходящих: нужна раскраска ${attr.kind === "number" ? "числового поля (градации или шкала)" : "текстового поля с заданными цветами"}.`) : "Пока пусто: настройте и нажмите «Сохранить»."}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Popover>
            <button type="button" className="small-btn" title="Сохранить раскраску в библиотеку проекта: её можно взять для скважин и других моделей" onClick={() => void save()}>Сохранить…</button>
          </div>
        )}
        {attr?.kind === "number" && (
          <>
            <div className="dh-seg" role="radiogroup" aria-label="Способ раскраски">
              <button type="button" role="radio" aria-checked={style.colorBy === "scale"} className={style.colorBy === "scale" ? "active" : ""} title="Непрерывная шкала: цвет по положению значения между «от» и «до»" onClick={() => paint({ colorBy: "scale" })}>Шкала</button>
              <button type="button" role="radio" aria-checked={style.colorBy === "classes"} className={style.colorBy === "classes" ? "active" : ""} title="Градации: классы содержаний с границами и своим цветом" onClick={toClasses}>Градации</button>
            </div>
            {style.colorBy === "scale" ? (
              <>
                <div className="dh-gradients">
                  {GRADIENTS.map((g) => (
                    <button key={g.name} type="button" className={`dh-gradient${style.gradient === g.name ? " active" : ""}`} title={`Градиент: ${g.title}`} aria-pressed={style.gradient === g.name} onClick={() => paint({ gradient: g.name })}>
                      <i style={{ background: gradientCss(g.name, style.reverse) }} />{g.title}
                    </button>
                  ))}
                </div>
                <div className="pair">
                  <label>Шкала от<input value={style.min} onChange={(e) => paint({ min: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Шкала от" title="Пусто — 5-й процентиль значений" /></label>
                  <label>до<input value={style.max} onChange={(e) => paint({ max: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Шкала до" title="Пусто — 95-й процентиль значений" /></label>
                </div>
                <div className="pair">
                  <label className="check"><input type="checkbox" checked={style.log} onChange={(e) => paint({ log: e.target.checked })} /> логарифмическая</label>
                  <label className="check"><input type="checkbox" checked={style.reverse} onChange={(e) => paint({ reverse: e.target.checked })} /> обратный порядок</label>
                </div>
                <label className="color-pick">Нет данных<input type="color" value={style.noDataColor} aria-label="Цвет блоков без значения" onChange={(e) => paint({ noDataColor: e.target.value })} /></label>
              </>
            ) : (
              <ClassEditor
                values={sample} classes={style.classes} log={style.log} noData={style.noDataColor} noun="блоков" weights={false}
                onChange={(classes) => paint({ classes })} onNoData={(noDataColor) => paint({ noDataColor })}
              />
            )}
          </>
        )}
        {attr?.kind === "category" && (
          <>
            <div className="block-categories">
              {(attr.labels ?? []).map((label, code) => (
                <span key={label} className="block-category">
                  <input type="checkbox" checked={!style.hidden.includes(label)} aria-label={`Показать: ${label}`} title="Показывать блоки этой категории"
                    onChange={(e) => paint({ hidden: e.target.checked ? style.hidden.filter((h) => h !== label) : [...style.hidden, label] })} />
                  <input type="color" value={categoryColor(style, label, code)} aria-label={`Цвет: ${label}`} title="Цвет категории"
                    onChange={(e) => paint({ catColors: { ...style.catColors, [label]: e.target.value } })} />
                  <span className="grow name" title={label}>{label}</span>
                </span>
              ))}
            </div>
            <div className="dh-tools">
              <label className="check" title="Блоки, у которых это поле пусто"><input type="checkbox" checked={!style.hidden.includes("")} onChange={(e) => paint({ hidden: e.target.checked ? style.hidden.filter((h) => h !== "") : [...style.hidden, ""] })} /> без значения</label>
              <input type="color" value={style.noDataColor} aria-label="Цвет блоков без значения" title="Цвет блоков без значения" onChange={(e) => paint({ noDataColor: e.target.value })} />
              {Object.keys(style.catColors).length > 0 && <button type="button" className="small-btn" title="Вернуть автоматические цвета" onClick={() => paint({ catColors: {} })}>Сбросить цвета</button>}
            </div>
          </>
        )}
      </fieldset>

      <fieldset>
        <legend>Просмотр</legend>
        <div className="block-join" role="radiogroup" aria-label="Что рисовать">
          <label className="check" title="Только блоки, видимые снаружи: быстрее, внешний вид тот же. Оболочка строится по блокам, прошедшим ограничитель">
            <input type="radio" name={`view${dataset.id}`} checked={style.view === "shell"} onChange={() => set({ view: "shell" })} /> внешняя оболочка
          </label>
          <label className="check" title="Все блоки, прошедшие ограничитель (и внутренние). Большие модели рисуются оболочкой">
            <input type="radio" name={`view${dataset.id}`} checked={style.view === "all"} onChange={() => set({ view: "all" })} /> все блоки
          </label>
        </div>
        <label className="check" title="При включённом отсечении по разрезу вместо объёмных блоков рисуется их сечение плоскостью разреза (подложкой под скважины)">
          <input type="checkbox" checked={style.slice} onChange={(e) => set({ slice: e.target.checked })} /> срез на разрезе
        </label>
        <label className="check"><input type="checkbox" checked={style.edges} onChange={(e) => set({ edges: e.target.checked })} /> показать края блоков</label>
        <div className="pair">
          <label className="color-pick">Цвет линии<input type="color" value={style.edgeColor} disabled={!style.edges} aria-label="Цвет краёв блоков" onChange={(e) => set({ edgeColor: e.target.value })} /></label>
          <label>Толщина, px
            <input type="number" min={0.5} max={8} step={0.5} value={style.edgeWidth} disabled={!style.edges} aria-label="Толщина краёв блоков, px"
              onChange={(e) => set({ edgeWidth: clamp(e.target.value, 0.5, 8, style.edgeWidth) })} />
          </label>
        </div>
        <label>Непрозрачность
          <input type="range" min={0.1} max={1} step={0.05} value={style.opacity} onChange={(e) => set({ opacity: Number(e.target.value) })} aria-label="Непрозрачность блоков" />
        </label>
      </fieldset>
      <p className="muted small">Применяется сразу и запоминается для модели.</p>
    </form>
  );
}
