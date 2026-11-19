/**
 * «Перенести в другой слой» (ТЗ 7.22 `lines.move_selected`): the selected lines of the layer under edit go to
 * the layer chosen here in one request; both layers get a new version. Point clouds and the layer itself are not
 * offered; a filter by name helps in a project with many layers.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { targetLines } from "../../edit/tools";
import { lineLayers, sceneObjects } from "../scene/objects";
import { editStore } from "./editState";
import { layerCounts } from "./tree";

/** Layers lines may go to: not the source, not a cloud; by the filter. */
export function moveTargets(layers: readonly { id: number; name: string; cloud?: unknown }[], source: number, filter: string): number[] {
  const f = filter.trim().toLowerCase();
  return layers.filter((l) => l.id !== source && !l.cloud && (!f || l.name.toLowerCase().includes(f))).map((l) => l.id);
}

export function MoveLinesWindow({ winId, layerId }: { winId: string; layerId: number }) {
  const app = useApp();
  const layers = useStore(sceneObjects, (o) => lineLayers(o));
  const session = useStore(editStore, (s) => s.session);
  const [filter, setFilter] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = useMemo(() => moveTargets(layers, layerId, filter), [layers, layerId, filter]);
  const chosen = session && session.layerId === layerId ? targetLines(session).length : 0;
  const move = async () => {
    if (target === null) return setError("Выберите слой, в который перенести линии");
    setBusy(true);
    const r = await app.registry.execute("lines.move_selected", { target_id: target });
    setBusy(false);
    if (!r.ok) return setError(r.result);
    closeWindow(winId);
  };
  return (
    <section className={css.panel} data-testid="lines-move">
      <p className={css.note}>{chosen ? `Выбрано линий: ${chosen}. Оба слоя получат новую версию.` : "Сначала выберите линии слоя на правке."}</p>
      <label className={css.field}>
        <span>Найти слой</span>
        <input className={css.input} value={filter} placeholder="часть названия" onChange={(e) => setFilter(e.target.value)} />
      </label>
      <div className={css.tableWrap} role="listbox" aria-label="Слой, в который перенести">
        {ids.length === 0 && <p className={css.note}>Других слоёв линий нет: создайте слой («Моделирование › Новый слой»).</p>}
        {ids.map((id) => {
          const l = layers.find((x) => x.id === id)!;
          return (
            <label key={id} className={css.check} style={{ padding: "2px 6px" }} data-testid={`move-target-${id}`}>
              <input type="radio" name="move-target" checked={target === id} onChange={() => (setTarget(id), setError(null))} />
              {l.name} <span className={css.note}>· {layerCounts(l)}</span>
            </label>
          );
        })}
      </div>
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
      <div className={css.row}>
        <button type="button" className={css.primary} data-testid="move-apply" disabled={busy || !chosen || target === null} onClick={() => void move()}>
          Перенести
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
      </div>
    </section>
  );
}
