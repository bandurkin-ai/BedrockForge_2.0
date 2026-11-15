import { quantile } from "../classify";
import type { LayerCtx } from "../context";
import { NumField, parseNum, Row, Section, Segmented, showNum } from "../ui";

const text = (v: number | null) => (v === null ? "" : String(Number(v.toPrecision(6))));

/** The scale of the layer: what the length of a bar, the place of a line and the thickness of a tube are measured against. */
export function ScaleSection({ ctx }: { ctx: LayerCtx }) {
  const { track: t, patch, range, values } = ctx;
  const fixed = t.min.trim() !== "" || t.max.trim() !== "";
  const lo = parseNum(t.min) ?? range?.[0] ?? null;
  const hi = parseNum(t.max) ?? range?.[1] ?? null;
  const summary = `${showNum(lo, 3) || "?"} – ${showNum(hi, 3) || "?"} · ${t.log ? "логарифмическая" : "линейная"}${fixed ? "" : " · по данным"}`;
  const q = (p: number) => (values && values.values.length ? quantile(values.values, p) : null);
  const set = (a: number | null, b: number | null) => patch({ min: text(a), max: text(b), dynamic: false });
  return (
    <Section id="scale" title="Шкала" summary={summary}>
      <Row label="От – до" title="Границы шкалы. Пустое поле — по данным (в подсказке — фактическое значение)">
        <NumField value={parseNum(t.min)} label="Шкала от" placeholder={range ? showNum(range[0], 3) : "авто"} onChange={(v) => patch({ min: text(v) }, "min")} />
        <span className="muted">–</span>
        <NumField value={parseNum(t.max)} label="Шкала до" placeholder={range ? showNum(range[1], 3) : "авто"} onChange={(v) => patch({ max: text(v) }, "max")} />
      </Row>
      <Row label="" title="Быстрый выбор границ по значениям поля">
        <button type="button" className="small-btn" disabled={!values?.values.length} title="От минимума до максимума значений" onClick={() => set(q(0), q(1))}>мин – макс</button>
        <button type="button" className="small-btn" disabled={!values?.values.length} title="От 2-го до 98-го процентиля" onClick={() => set(q(0.02), q(0.98))}>P2 – P98</button>
        <button type="button" className="small-btn" disabled={!values?.values.length} title="От нуля до 99-го процентиля" onClick={() => set(0, q(0.99))}>0 – P99</button>
      </Row>
      <Row label="Границы" title="Закреплены: числа выше остаются при обновлении таблицы. По данным: пересчитываются">
        <Segmented value={fixed ? "fixed" : "dynamic"} label="Границы шкалы" options={[
          { value: "dynamic", label: "По данным", title: "Границы пересчитываются по значениям таблицы" },
          { value: "fixed", label: "Закреплены", title: "Границы не меняются при обновлении таблицы" },
        ]} onChange={(m) => (m === "dynamic" ? patch({ min: "", max: "", dynamic: true }) : set(range?.[0] ?? null, range?.[1] ?? null))} />
      </Row>
      <label className="check" title="Логарифмическая шкала. Ноль и отрицательные значения прижимаются к минимуму шкалы">
        <input type="checkbox" checked={t.log} onChange={(e) => patch({ log: e.target.checked })} /> логарифмическая
      </label>
      {t.log && <span className="muted small">Ноль и отрицательные значения рисуются на минимуме шкалы.</span>}
      <Row label="Выше максимума" title="Что делать со значениями больше максимума шкалы (ураганные пробы)">
        <Segmented value={t.over ?? "clip"} label="Значения выше максимума" options={[
          { value: "clip", label: "Обрезать", title: "Столбец обрезается по максимуму шкалы" },
          { value: "mark", label: "С пометкой", title: "Обрезается, на конце столбца красный штрих" },
          { value: "asis", label: "Как есть", title: "Столбец выходит за ширину колонки" },
        ]} onChange={(over) => patch({ over })} />
      </Row>
    </Section>
  );
}
