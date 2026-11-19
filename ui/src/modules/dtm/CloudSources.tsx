/**
 * «Облака точек» of the window «Построение ЦТМ» (ТЗ 7.35 Д-22; ADR 0014 §5): a check box per cloud, the classes
 * taken (by default 2 — земля) and the step of the thinning. A cloud without ground points is told to run
 * «Фильтр земли» first (the command `ground.classify`).
 */
import { useApp } from "../../shell/AppContext";
import { toggleId } from "../../shell/props";
import { count } from "../../shell/treeActions";
import css from "../../shell/PropsParts.module.css";
import type { LineLayerInfo } from "../scene/objects";
import { classOptions, GROUND, pickedPoints, type CloudPick } from "./cloud";

export function CloudSources({ clouds, picks, onChange }: { clouds: readonly LineLayerInfo[]; picks: readonly CloudPick[]; onChange: (next: CloudPick[]) => void }) {
  const app = useApp();
  if (!clouds.length) return null;
  const set = (id: number, p: Partial<CloudPick> | null) => {
    const rest = picks.filter((x) => x.layerId !== id);
    const was = picks.find((x) => x.layerId === id);
    onChange(p === null ? rest : [...rest, { layerId: id, classes: [GROUND], cell: "", ...was, ...p }]);
  };
  return (
    <fieldset className={css.group} data-testid="dtm-clouds">
      <legend>Источники: облака точек</legend>
      {clouds.map((l) => {
        const pick = picks.find((x) => x.layerId === l.id);
        const ground = l.cloud?.classes?.[String(GROUND)] ?? 0;
        return (
          <div key={l.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label className={css.check}>
              <input type="checkbox" checked={!!pick} aria-label={`Облако «${l.name}»`} onChange={(e) => set(l.id, e.target.checked ? {} : null)} />
              {l.name}
              <span className={css.count}>{count(l.point_count)} точек</span>
            </label>
            {pick && (
              <div style={{ paddingLeft: 22, display: "flex", flexDirection: "column", gap: 4 }}>
                <div className={css.row} role="group" aria-label={`Классы облака «${l.name}»`}>
                  {classOptions(l).map((c) => (
                    <label key={c.code} className={css.check} title={`Точек: ${count(c.points)}`}>
                      <input type="checkbox" checked={pick.classes.includes(c.code)} aria-label={`Класс ${c.label} облака «${l.name}»`} onChange={(e) => set(l.id, { classes: toggleId(pick.classes, c.code, e.target.checked) })} />
                      {c.label}
                      <span className={css.count}>{count(c.points)}</span>
                    </label>
                  ))}
                </div>
                <label className={css.formRow}>
                  <span>Шаг прореживания, м</span>
                  <input className={css.input} inputMode="decimal" value={pick.cell} placeholder="по облаку: не больше 2 млн вершин" aria-label={`Шаг прореживания облака «${l.name}», м`} onChange={(e) => set(l.id, { cell: e.target.value })} />
                </label>
                <p className={css.note}>Точек отмеченных классов: {count(pickedPoints(l, pick.classes))}. В ячейке шага остаётся одна точка — с нижней медианой высоты над землёй.</p>
                {!ground && (
                  <p className={css.note}>
                    Точек земли нет.{" "}
                    <button type="button" className={css.btn} onClick={() => void app.run("ground.classify", { cloud_id: l.id })}>
                      Фильтр земли…
                    </button>
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}
