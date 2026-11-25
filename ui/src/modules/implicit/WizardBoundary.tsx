/**
 * Step 1 of the wizard «Новая модель» (ТЗ 8.9.4): the name, the boundary (a box of the extent or a closed wireframe, optionally under a
 * DTM) and the step h with the levels L. Under the step — the grid the core will make: the cells along the axes and in all, counted
 * with the defaults of the core (the step by default is the diagonal of the extent / 200, in the range 0,1–100 m).
 */
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { dtms, solids } from "../wireframes/state";
import { AXES, BOX_LABELS, DEFAULT_NAME, extentOf, gridOf, type BoundaryKind } from "./form";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { IssueAt, NumberRow, g, nf, useRanges } from "./WizardParts";
import { setForm, wizardStore } from "./wizardState";

const wireframeExtent = (id: number) => sceneObjects.get().wireframes.find((w) => w.id === id)?.extent ?? null;

/** «Сетка 120 × 96 × 40 = 460 800 ячеек, шаг 8,3 м (по умолчанию), уровней 2». */
export function GridLine() {
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const wires = useSel(sceneObjects, (s) => s.wireframes);
  const ranges = useRanges();
  void wires;
  const grid = gridOf(f, extentOf(f, wireframeExtent), { h_divisor: defaults?.params.h_divisor ?? 200, levels: defaults?.params.levels ?? 2 }, ranges);
  if (!grid) return <p className={css.note} data-testid="imp-grid">Число ячеек будет известно, когда задана граница.</p>;
  return (
    <>
      <p className={mod.grid} data-testid="imp-grid">
        Сетка {grid.n.map(nf).join(" × ")} = {nf(grid.cells)} ячеек, шаг {g(grid.h)} м{grid.typed ? "" : " (по умолчанию: диагональ охвата / 200)"}, уровней {grid.levels}.
      </p>
      {grid.tooMany && (
        <p className={css.error} role="alert" data-testid="imp-grid-many">
          Ячеек по одной оси больше {nf(2 ** 20)}: ядро откажет. Увеличьте шаг h или уменьшите охват.
        </p>
      )}
    </>
  );
}

export function WizardBoundary() {
  const f = useSel(wizardStore, (s) => s.form);
  const wires = useSel(sceneObjects, (s) => s.wireframes);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const ranges = useRanges();
  const bodies = solids(wires);
  const tops = dtms(wires);
  const extent = extentOf(f, wireframeExtent);
  const placeholder = extent && defaults ? g(Math.min(ranges.h?.hi ?? Infinity, Math.max(ranges.h?.lo ?? 0, Math.hypot(extent[3] - extent[0], extent[4] - extent[1], extent[5] - extent[2]) / defaults.params.h_divisor))) : "авто";
  const setBoundary = (boundary: BoundaryKind) => setForm({ boundary });

  return (
    <>
      <fieldset className={css.group} data-testid="imp-step-1">
        <legend>Модель</legend>
        <label className={css.formRow}>
          <span>Название</span>
          <input className={css.input} value={f.name} placeholder={DEFAULT_NAME} aria-label="Название модели" data-testid="imp-name" onChange={(e) => setForm({ name: e.target.value })} />
        </label>
        <IssueAt at="name" id="name" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Граница</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Граница модели">
          <label>
            <input type="radio" name="imp-boundary" checked={f.boundary === "box"} data-testid="imp-boundary-box" onChange={() => setBoundary("box")} />
            Охват (прямоугольный параллелепипед)
          </label>
          <label>
            <input type="radio" name="imp-boundary" checked={f.boundary === "wireframe"} data-testid="imp-boundary-wireframe" onChange={() => setBoundary("wireframe")} />
            Замкнутый каркас
          </label>
        </div>
        {f.boundary === "box" ? (
          <>
            <div className={mod.box} data-testid="imp-box">
              {BOX_LABELS.map((l, i) => (
                <label key={l}>
                  <span>{l}, м</span>
                  <input className={css.input} value={f.box[i]} inputMode="decimal" spellCheck={false} aria-label={`Охват: ${l}`} data-testid={`imp-box-${i}`} onChange={(e) => setForm({ box: f.box.map((v, k) => (k === i ? e.target.value : v)) })} />
                </label>
              ))}
            </div>
            <span className={css.note}>{AXES.join(", ")} — координаты проекта: минимум и максимум по каждой оси.</span>
            {bodies.length > 0 && (
              <label className={css.formRow}>
                <span>Охват каркаса</span>
                <select
                  className={css.input}
                  value=""
                  aria-label="Взять охват у каркаса"
                  data-testid="imp-box-from"
                  onChange={(e) => {
                    const ext = e.target.value ? wireframeExtent(Number(e.target.value)) : null;
                    if (ext && ext.length >= 6 && ext.slice(0, 6).every((x) => typeof x === "number")) setForm({ box: ext.slice(0, 6).map((x) => String(x).replace(".", ",")) });
                  }}
                >
                  <option value="">— взять у каркаса —</option>
                  {bodies.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <IssueAt at="boundary.box" id="box" />
          </>
        ) : (
          <>
            <label className={css.formRow}>
              <span>Каркас</span>
              <select className={css.input} value={f.wireframeId ?? ""} aria-label="Каркас границы" data-testid="imp-wireframe" onChange={(e) => setForm({ wireframeId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">{bodies.length ? "— выберите каркас —" : "в проекте нет каркасов"}</option>
                {bodies.map((w) => (
                  <option key={w.id} value={w.id} disabled={w.stats?.closed === false}>
                    {w.name}
                    {w.stats?.closed === false ? " (не замкнут)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className={css.note}>Каркас должен быть замкнутым: внутренность определяется по чётности луча. Незамкнутый чинится командой «Исправить каркас».</span>
            <IssueAt at="boundary.wireframe_id" id="wireframe" />
          </>
        )}
        <label className={css.formRow}>
          <span>Сверху ограничить</span>
          <select className={css.input} value={f.dtmId ?? ""} aria-label="ЦТМ границы" data-testid="imp-dtm" onChange={(e) => setForm({ dtmId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">без ЦТМ</option>
            {tops.map((w) => (
              <option key={w.id} value={w.id}>
                ЦТМ «{w.name}»
              </option>
            ))}
          </select>
        </label>
        <span className={css.note}>С ЦТМ модель — часть границы под поверхностью рельефа.</span>
        <IssueAt at="boundary.dtm_id" id="dtm" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Шаг и уровни</legend>
        <NumberRow label="Шаг h, м" field="h" rangeKey="h" at="h" id="h" placeholder={placeholder} note="Пусто — диагональ охвата / 200." />
        <NumberRow label="Уровни L" field="levels" rangeKey="levels" at="levels" id="levels" placeholder={String(defaults?.params.levels ?? 2)} note="Грубая сетка в 2^L раз крупнее: вычисление поля только в полосе у поверхности." />
        <GridLine />
      </fieldset>
    </>
  );
}
