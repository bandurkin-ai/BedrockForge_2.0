import { useMemo, useState } from "react";
import { classColor, classLabel, sortClasses, type ColorClass } from "../../charts/striplog/model";
import { CLASS_METHODS, classBounds, classCounts, classesFromBounds, parseBounds, type ClassMethod } from "../../viz/downhole/classify";
import { GRADIENTS, gradientCss, mixHex, rampColors } from "../../viz/downhole/gradients";
import { buildHistogram, type FieldValues } from "../../viz/downhole/histogram";
import { HistogramPlot } from "./HistogramPlot";
import { ColorField, MenuItem, NumField, Popover } from "./ui";

/** Grade classes on the histogram of the field: bounds dragged on the distribution, a row per class, automatic breaks, gradients. */
export function ClassEditor({ values, classes, onChange, log: logDefault, noData, onNoData, noun = "проб", weights = true }: {
  values: FieldValues | null;
  classes: ColorClass[];
  onChange: (classes: ColorClass[], key?: string) => void;
  log: boolean;
  noData: string;
  onNoData: (color: string) => void;
  /** what is counted, in the genitive: «проб» of drillholes, «блоков» of a block model */
  noun?: string;
  /** the values have interval lengths to weight them by */
  weights?: boolean;
}) {
  const [log, setLog] = useState(logDefault);
  const [cum, setCum] = useState(false);
  const [byLength, setByLength] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [method, setMethod] = useState<ClassMethod>("quantile");
  const [count, setCount] = useState(5);
  const [gradient, setGradient] = useState("geochem");
  const [reverse, setReverse] = useState(false);
  const [paste, setPaste] = useState("");
  // the bins are counted once for a field and an axis; dragging a bound does not recount them
  const hist = useMemo(() => (values ? buildHistogram(values, 48, log, byLength) : null), [values, log, byLength]);
  const counts = useMemo(() => (values ? classCounts(values.values, classes) : classes.map(() => 0)), [values, classes]);
  const total = values?.values.length ?? 0;
  const bounded = classes.map((c, i) => ({ c, i })).filter((x): x is { c: ColorClass & { from: number }; i: number } => x.c.from !== null);

  const moveBound = (k: number, value: number) => {
    const { i } = bounded[k];
    const lo = k > 0 ? bounded[k - 1].c.from : -Infinity;
    const hi = k + 1 < bounded.length ? bounded[k + 1].c.from : Infinity;
    if (!(value > lo && value < hi) || value === classes[i].from) return;
    onChange(classes.map((c, j) => (j === i ? { ...c, from: value } : c)), `bound${i}`);
  };
  const addBound = (value: number) => {
    if (classes.some((c) => c.from === value) || classes.length >= 40) return;
    const color = classColor(classes, value) ?? "#9aa3b5";
    const base = classes.length ? classes : [{ from: null, color: "#9aa3b5" }];
    onChange(sortClasses([...base, { from: value, color: mixHex(color, "#ffffff", 0.25) }]));
  };
  const remove = (i: number) => {
    const next = classes.filter((_, j) => j !== i);
    if (next.length && i === 0 && classes[0].from === null) next[0] = { ...next[0], from: null }; // the lowest class has no lower bound
    onChange(sortClasses(next));
    setPicked([]);
  };
  const recolor = (name: string, rev: boolean) => {
    const colors = rampColors(name, classes.length, rev);
    onChange(classes.map((c, i) => ({ ...c, color: colors[i] })));
  };
  const stretch = () => {
    const [a, b] = [Math.min(...picked), Math.max(...picked)];
    if (!(b > a + 1)) return;
    onChange(classes.map((c, i) => (i > a && i < b ? { ...c, color: mixHex(classes[a].color, classes[b].color, (i - a) / (b - a)) } : c)));
  };

  return (
    <div className="dh-classes">
      {hist ? (
        <>
          <div className="dh-hist-tools">
            <span className="muted small grow">число {noun}{byLength ? " (по длине)" : ""}</span>
            <label className="check" title="Логарифмическая ось значений"><input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} /> лог X</label>
            <label className="check" title={`Накопленное распределение: доля ${noun} не выше значения`}><input type="checkbox" checked={cum} onChange={(e) => setCum(e.target.checked)} /> накопл.</label>
            {weights && <label className="check" title="Взвешивать пробы по длине интервала"><input type="checkbox" checked={byLength} onChange={(e) => setByLength(e.target.checked)} /> по длине</label>}
          </div>
          <HistogramPlot
            hist={hist} label="Гистограмма значений с границами классов" showCumulative={cum}
            colorAt={(v) => classColor(classes, v) ?? noData} handles={bounded.map((x) => x.c.from)}
            onMove={moveBound} onAdd={addBound} onRemove={(k) => remove(bounded[k].i)}
          />
          <span className="muted small">Границы тянутся мышью и стрелками; двойной щелчок по гистограмме — новая граница. Значение, равное границе, относится к верхнему классу.</span>
        </>
      ) : (
        <span className="muted small">{values ? "Нет числовых значений для гистограммы." : "Загрузка значений…"}</span>
      )}
      <div className="dh-class-rows">
        {classes.map((c, i) => (
          <div key={i} className={`dh-class-row${c.hidden ? " off" : ""}${picked.includes(i) ? " picked" : ""}`}>
            <input type="checkbox" checked={picked.includes(i)} aria-label={`Выделить класс ${i + 1}`} title="Выделить класс (для «растянуть между выбранными»)"
              onChange={(e) => setPicked(e.target.checked ? [...picked, i] : picked.filter((x) => x !== i))} />
            <button type="button" className="icon dh-eye" aria-pressed={!c.hidden} title={c.hidden ? "Класс скрыт в сцене: показать" : "Скрыть класс в сцене"} aria-label={`${c.hidden ? "Показать" : "Скрыть"} класс ${i + 1}`}
              onClick={() => onChange(classes.map((x, j) => (j === i ? { ...x, hidden: !x.hidden } : x)))}>{c.hidden ? "◌" : "👁"}</button>
            <input type="color" value={c.color} aria-label={`Цвет класса ${i + 1}`} title="Цвет класса" onChange={(e) => onChange(classes.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)), `color${i}`)} />
            {c.from === null ? <span className="muted small dh-from">ниже</span> : (
              <NumField className="dh-from" value={c.from} label={`Нижняя граница класса ${i + 1}`} title="Нижняя граница класса (входит в класс)"
                onChange={(v) => v !== null && onChange(sortClasses(classes.map((x, j) => (j === i ? { ...x, from: v } : x))), `from${i}`)} />
            )}
            <input className="dh-class-label" value={c.label ?? ""} placeholder={classLabel(classes.map((x) => ({ ...x, label: undefined })), i)} aria-label={`Подпись класса ${i + 1}`} title="Своя подпись класса в легенде (пусто — по границам)"
              onChange={(e) => onChange(classes.map((x, j) => (j === i ? { ...x, label: e.target.value || undefined } : x)), `label${i}`)} />
            <span className="muted small dh-count" title={`Число ${noun} в классе и их доля`}>{counts[i].toLocaleString("ru-RU")} · {total ? Math.round((counts[i] / total) * 100) : 0} %</span>
            <button type="button" className="icon" title="Удалить класс" aria-label={`Удалить класс ${i + 1}`} disabled={classes.length <= 1} onClick={() => remove(i)}>✕</button>
          </div>
        ))}
      </div>
      <div className="dh-tools">
        <button type="button" className="small-btn" title="Добавить класс выше последнего" disabled={classes.length >= 40} onClick={() => {
          const last = bounded.length ? bounded[bounded.length - 1].c.from : values?.values.length ? values.values[values.values.length >> 1] : 0;
          const hi = values?.values.length ? values.values[values.values.length - 1] : last + 1;
          const next = bounded.length ? Number((last + (hi > last ? (hi - last) / 2 : Math.abs(last) || 1)).toPrecision(3)) : Number(last.toPrecision(3));
          addBound(next);
        }}>+ класс</button>
        <Popover label="Авто-разбивка" title="Разбить значения на классы автоматически" className="small-btn" button="Авто ▾">
          {(close) => (
            <div className="dh-pop-form">
              <label>Способ
                <select value={method} aria-label="Способ разбивки" onChange={(e) => setMethod(e.target.value as ClassMethod)}>
                  {CLASS_METHODS.map(([m, title, hint]) => <option key={m} value={m} title={hint}>{title}</option>)}
                </select>
              </label>
              <span className="muted small">{CLASS_METHODS.find((m) => m[0] === method)?.[2]}</span>
              <label>Число классов<input type="number" min={2} max={12} value={count} aria-label="Число классов" onChange={(e) => setCount(Math.max(2, Math.min(12, Number(e.target.value) || 5)))} /></label>
              <button type="button" className="primary" disabled={!values?.values.length} onClick={() => {
                const bounds = classBounds(values?.values ?? [], method, count);
                if (bounds.length) onChange(classesFromBounds(bounds, gradient, reverse));
                close();
              }}>Разбить</button>
            </div>
          )}
        </Popover>
        <Popover label="Градиент" title="Раскрасить классы по готовому градиенту" className="small-btn" button="Градиент ▾">
          {(close) => (
            <div className="dh-pop-form">
              {GRADIENTS.map((g) => (
                <button key={g.name} type="button" className={`dh-gradient${gradient === g.name ? " active" : ""}`} title={`Раскрасить классы: ${g.title}`} onClick={() => { setGradient(g.name); recolor(g.name, reverse); }}>
                  <i style={{ background: gradientCss(g.name, reverse) }} />{g.title}
                </button>
              ))}
              <label className="check"><input type="checkbox" checked={reverse} onChange={(e) => { setReverse(e.target.checked); recolor(gradient, e.target.checked); }} /> обратный порядок</label>
              <MenuItem disabled={picked.length < 2 || Math.max(...picked) - Math.min(...picked) < 2} title="Выделите два класса флажками: классы между ними получат переходные цвета" onClick={() => { stretch(); close(); }}>Растянуть между выбранными</MenuItem>
            </div>
          )}
        </Popover>
        <Popover label="Вставить границы" title="Ввести границы классов списком" className="small-btn" button="Вставить границы">
          {(close) => (
            <div className="dh-pop-form">
              <label>Границы через пробел
                <input value={paste} placeholder="0,3 1 3 10" aria-label="Границы классов списком" onChange={(e) => setPaste(e.target.value)} />
              </label>
              <button type="button" className="primary" disabled={!parseBounds(paste).length} onClick={() => {
                onChange(classesFromBounds(parseBounds(paste), gradient, reverse, classes));
                close();
              }}>Задать</button>
            </div>
          )}
        </Popover>
      </div>
      <div className="dh-row" title="Цвет значений вне классов и пустых">
        <span className="dh-label">Нет данных</span>
        <ColorField value={noData} label="Цвет «нет данных»" onChange={onNoData} />
      </div>
    </div>
  );
}
