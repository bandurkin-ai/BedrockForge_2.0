/**
 * Step 4 of the wizard «Новая модель» (ТЗ 8.9.3, 8.9.4): «Итог» — what is done with the surfaces (the smallest volume, the simplification,
 * the clipping by the boundary, a wireframe or a DTM) and the summary of the form: the boundary, the grid, the groups, the kernel, the
 * smoothing, the anisotropy. «Сохранить» writes the model, «Сохранить и построить» builds it at once (the job `implicit.build`).
 */
import css from "../../shell/PropsParts.module.css";
import { CLIP_TITLES, KERNEL_TITLES, type ClipKind } from "./api";
import { extentOf, gridOf, TYPE_TITLES } from "./form";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { GridLine } from "./WizardBoundary";
import { IssueAt, NumberRow, nf, useRanges } from "./WizardParts";
import { sceneObjects } from "../scene/objects";
import { dtms, solids } from "../wireframes/state";
import { setForm, wizardStore } from "./wizardState";

const CLIPS: readonly ClipKind[] = ["boundary", "none", "wireframe", "dtm"];

export function WizardResult() {
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const wires = useSel(sceneObjects, (s) => s.wireframes);
  const ranges = useRanges();
  const extent = extentOf(f, (id) => wires.find((w) => w.id === id)?.extent ?? null);
  const grid = gridOf(f, extent, { h_divisor: defaults?.params.h_divisor ?? 200, levels: defaults?.params.levels ?? 2 }, ranges);
  const clipPool = f.clip === "dtm" ? dtms(wires) : solids(wires);
  const wireName = (id: number | null) => (id === null ? "—" : (wires.find((w) => w.id === id)?.name ?? `№${id}`));
  const boundary = f.boundary === "box" ? `охват ${f.box.join("; ")}` : `каркас «${wireName(f.wireframeId)}»`;
  const smoothing = f.smoothing === "manual" ? `вручную, s ${f.s || "по виду группы"}` : `авто, cond ${f.cond || "по умолчанию"}`;
  const trend = f.trend === "none" ? "нет" : f.trend === "list" ? `тренд №${f.trendId ?? "—"}` : `углы ${f.ang.join("; ")}, отношения 1; ${f.ratios.join("; ")}`;

  return (
    <>
      <fieldset className={css.group} data-testid="imp-step-4">
        <legend>Поверхности</legend>
        <NumberRow label="Наименьший объём, м³" field="vMin" rangeKey="v_min" at="post.v_min" id="vmin" placeholder="0" note="Тела меньше — убираются; 0 — не убирать." />
        <label className={css.check}>
          <input type="checkbox" checked={f.simplify} data-testid="imp-simplify" onChange={(e) => setForm({ simplify: e.target.checked })} />
          Упростить сетку (отклонение не больше 0,1·h)
        </label>
        <label className={css.formRow}>
          <span>Обрезка</span>
          <select className={css.input} value={f.clip} aria-label="Обрезка" data-testid="imp-clip" onChange={(e) => setForm({ clip: e.target.value as ClipKind, clipId: null })}>
            {CLIPS.map((c) => (
              <option key={c} value={c}>
                {CLIP_TITLES[c]}
              </option>
            ))}
          </select>
        </label>
        {(f.clip === "wireframe" || f.clip === "dtm") && (
          <label className={css.formRow}>
            <span>{f.clip === "dtm" ? "ЦТМ обрезки" : "Каркас обрезки"}</span>
            <select className={css.input} value={f.clipId ?? ""} aria-label="Объект обрезки" data-testid="imp-clip-id" onChange={(e) => setForm({ clipId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— выберите —</option>
              {clipPool.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <IssueAt at="post" id="post" />
      </fieldset>

      <fieldset className={css.group} data-testid="imp-summary">
        <legend>Итог</legend>
        <dl className={mod.summary}>
          <dt>Название</dt>
          <dd data-testid="imp-sum-name">{f.name.trim() || "Неявная модель"}</dd>
          <dt>Граница</dt>
          <dd data-testid="imp-sum-boundary">
            {boundary}
            {f.dtmId !== null ? `, под ЦТМ «${wireName(f.dtmId)}»` : ""}
          </dd>
          <dt>Сетка</dt>
          <dd data-testid="imp-sum-grid">{grid ? `${grid.n.map(nf).join(" × ")} = ${nf(grid.cells)} ячеек, h ${nf(Math.round(grid.h * 1000) / 1000)} м, L ${grid.levels}` : "—"}</dd>
          <dt>Группы</dt>
          <dd data-testid="imp-sum-groups">
            {f.groups.length ? f.groups.map((g) => `${g.name || "без названия"} (${TYPE_TITLES[g.type] ?? g.type}${g.codes.length ? `: ${g.codes.join(", ")}` : ""})`).join("; ") : "нет (только «Прочее»)"}
          </dd>
          <dt>Ядро</dt>
          <dd data-testid="imp-sum-kernel">{KERNEL_TITLES[f.kernel]}</dd>
          <dt>Сглаживание</dt>
          <dd data-testid="imp-sum-smoothing">{smoothing}</dd>
          <dt>Анизотропия</dt>
          <dd data-testid="imp-sum-trend">{trend}</dd>
        </dl>
        <GridLine />
      </fieldset>
    </>
  );
}
