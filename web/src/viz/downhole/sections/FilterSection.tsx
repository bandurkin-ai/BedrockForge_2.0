import type { TrackFilter } from "../../../charts/stripLog";
import type { LayerCtx } from "../context";
import { ColorField, NumField, Row, Section, Segmented, showNum } from "../ui";
import { NO_DATA_COLOR } from "../../downholeGeometry";

const BASE: TrackFilter = { mode: "all", empty: "skip", nonPositive: "asis" };

/** Which intervals are drawn: the cut-off, hidden classes, and what empty, zero and negative values mean. */
export function FilterSection({ ctx }: { ctx: LayerCtx }) {
  const { track: t, patch } = ctx;
  const f = t.filter ?? BASE;
  const set = (p: Partial<TrackFilter>, key?: string) => patch({ filter: { ...f, ...p } }, key);
  const hiddenClasses = t.colorBy === "classes" ? (t.classes ?? []).filter((c) => c.hidden).length : 0;
  const hidden = hiddenClasses + (t.hidden?.length ?? 0);
  const parts = [
    f.mode === "min" ? `показывать ≥ ${showNum(f.min ?? 0)}` : f.mode === "range" ? `от ${showNum(f.min ?? null) || "…"} до ${showNum(f.max ?? null) || "…"}` : "все значения",
    hidden ? `скрыто: ${hidden}` : "",
    f.nonPositive !== "asis" ? "≤ 0 особо" : "",
  ].filter(Boolean);
  const replace = typeof f.nonPositive === "object" ? f.nonPositive.replace : null;
  return (
    <Section id="filter" title="Фильтр" summary={parts.join(" · ")}>
      {ctx.numeric && (
        <>
          <Row label="Показывать" title="Какие интервалы рисовать по значению поля">
            <Segmented value={f.mode} label="Показывать" options={[
              { value: "all", label: "Все", title: "Все интервалы" },
              { value: "min", label: "От борта", title: "Только значения не ниже борта" },
              { value: "range", label: "Диапазон", title: "Только значения в диапазоне" },
            ]} onChange={(mode) => set({ mode, min: mode !== "all" && f.min === undefined ? Number((ctx.range?.[0] ?? 0).toPrecision(2)) : f.min })} />
          </Row>
          {f.mode !== "all" && (
            <Row label={f.mode === "min" ? "Борт, ≥" : "От – до"} title="Значение, равное границе, показывается">
              <NumField value={f.min ?? null} label="Показывать от" onChange={(v) => set({ min: v ?? undefined }, "fmin")} />
              {f.mode === "range" && <><span className="muted">–</span><NumField value={f.max ?? null} label="Показывать до" onChange={(v) => set({ max: v ?? undefined }, "fmax")} /></>}
            </Row>
          )}
        </>
      )}
      {hidden > 0 && (
        <Row label="Скрыто" title="Классы и категории, выключенные «глазом» в разделе «Цвет»">
          <span>{hidden}</span>
          <button type="button" className="small-btn" onClick={() => patch({ hidden: undefined, classes: t.classes?.map((c) => ({ ...c, hidden: false })) })}>показать все</button>
        </Row>
      )}
      <Row label="Пустые" title="Интервалы без значения: пропускать или рисовать цветом «нет данных»">
        <Segmented value={f.empty} label="Пустые значения" options={[
          { value: "skip", label: "Пропуск", title: "Интервал без значения не рисуется" },
          { value: "nodata", label: "Цветом", title: "Рисуется цветом «нет данных» (трубка и лента)" },
        ]} onChange={(empty) => set({ empty })} />
        {f.empty === "nodata" && <ColorField value={t.noDataColor ?? NO_DATA_COLOR} label="Цвет «нет данных»" onChange={(c) => patch({ noDataColor: c }, "nodata")} />}
      </Row>
      {ctx.numeric && (
        <>
          <Row label="Ноль и ниже" title="Нулевые и отрицательные значения: как есть, считать пустыми или заменить (например, на половину предела обнаружения)">
            <Segmented value={typeof f.nonPositive === "object" ? "replace" : f.nonPositive} label="Ноль и отрицательные" options={[
              { value: "asis", label: "Как есть", title: "Рисовать как в таблице" },
              { value: "empty", label: "Пустые", title: "Считать пустыми" },
              { value: "replace", label: "Заменить", title: "Заменить на заданное значение" },
            ]} onChange={(m) => set({ nonPositive: m === "replace" ? { replace: replace ?? 0.005 } : m })} />
            {replace !== null && <NumField value={replace} label="Заменить на" onChange={(v) => set({ nonPositive: { replace: v ?? 0 } }, "repl")} />}
          </Row>
          <Row label="Пустые коды, ≤" title="Значения не больше этого считаются пустыми (коды вроде −999). Пусто — не используется">
            <NumField value={f.emptyBelow ?? null} label="Значения не больше считать пустыми" placeholder="нет" onChange={(v) => set({ emptyBelow: v ?? undefined }, "ebelow")} />
          </Row>
          {t.log && <span className="muted small">При логарифмической шкале ноль и отрицательные рисуются на минимуме шкалы.</span>}
        </>
      )}
    </Section>
  );
}
