/**
 * The window «Расширение каркаса» (ТЗ 8.9.2, 8.9.4; ADR 0026 решение 18): a closed wireframe and the offset Δ in metres — the surface at the
 * distance Δ from it (Δ < 0 shrinks). An open wireframe has no inside: it is listed but cannot be chosen. «Сохранить» runs `implicit.expand`.
 */
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { solids } from "../wireframes/state";
import { FieldIssue, GroupFrame, TextRow } from "./GroupParts";
import { groupRanges } from "./groupBase";
import { expands } from "./groupKinds";
import { useSel } from "./hooks";
import { deltaWords } from "./ExpandState";
import { wizardStore } from "./wizardState";

export function ExpandForm({ winId }: { winId: string }) {
  const f = useSel(expands.store, (x) => x.form);
  const wires = useSel(sceneObjects, (x) => x.wireframes);
  const defaults = useSel(wizardStore, (x) => x.defaults.data);
  const ranges = groupRanges(defaults);
  const bodies = solids(wires);
  const words = deltaWords(f.delta);
  const known = f.wireframeId === null || bodies.some((w) => w.id === f.wireframeId);

  return (
    <GroupFrame rt={expands} winId={winId} intro="Поверхность на расстоянии Δ от замкнутого каркаса: Δ > 0 расширяет, Δ < 0 сжимает. Новая группа добавляется последней.">
      <fieldset className={css.group} data-testid="grp-name-box">
        <legend>Группа</legend>
        <TextRow rt={expands} label="Название" value={f.name} onChange={(v) => expands.setForm({ name: v })} id="name" at="name" placeholder="Расширение N" />
      </fieldset>
      <fieldset className={css.group} data-testid="grp-wire-box">
        <legend>Каркас и смещение</legend>
        <label className={css.formRow}>
          <span>Каркас</span>
          <select className={css.input} value={f.wireframeId ?? ""} aria-label="Каркас" data-testid="grp-wireframe" onChange={(e) => expands.setForm({ wireframeId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{bodies.length ? "— выберите замкнутый каркас —" : "в проекте нет каркасов"}</option>
            {!known && <option value={f.wireframeId!}>каркас №{f.wireframeId} (не найден)</option>}
            {bodies.map((w) => (
              <option key={w.id} value={w.id} disabled={w.stats?.closed === false}>
                {w.name}
                {w.stats?.closed === false ? " (не замкнут)" : ""}
              </option>
            ))}
          </select>
        </label>
        <span className={css.note}>Каркас должен быть замкнутым: внутренность определяется по знаку расстояния. Незамкнутый чинится командой «Исправить каркас».</span>
        <FieldIssue rt={expands} at="params.wireframe_id" id="wireframe" />
        <TextRow rt={expands} label="Смещение Δ, м" value={f.delta} onChange={(v) => expands.setForm({ delta: v })} id="delta" at="params.delta" placeholder="5" range={ranges.expand} note="Не ноль: отрицательное значение — сжатие." />
        {words && (
          <p className={css.note} data-testid="grp-delta-words">
            {words}
          </p>
        )}
      </fieldset>
    </GroupFrame>
  );
}
