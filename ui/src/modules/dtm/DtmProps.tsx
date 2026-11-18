/**
 * The settings of a DTM at the top of «Свойства» (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5): the raster on the relief
 * chosen from the rasters in plan (vertical ones are not offered), «Скрыть данные за ЦТМ», the colour and the
 * opacity of the raster, the shadows of the relief, «Вернуть в каркасы». Every change is `wireframe.dtm`.
 */
import { askConfirm } from "../../shell/dialogs";
import { Check, ColorField, Slider } from "../../shell/PropsParts";
import { counted } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects, type DtmInfo } from "../scene/objects";
import { backMessage, drapeOptions, verticalCount } from "./props";

type Run = (id: string, args?: Record<string, unknown>) => Promise<boolean>;

export function DtmProps({ id, name, dtm, run }: { id: number; name: string; dtm: DtmInfo; run: Run }) {
  const rasters = useStore(sceneObjects, (o) => o.rasters);
  const options = drapeOptions(rasters);
  const vertical = verticalCount(rasters);
  const set = (settings: Record<string, unknown>) => run("wireframe.dtm", { wireframe_id: id, ...settings });
  const back = async () => {
    if (await askConfirm({ title: "Вернуть в каркасы", message: backMessage(name), okLabel: "Вернуть" })) await run("wireframe.dtm", { wireframe_id: id, on: false });
  };
  const draped = dtm.raster_id !== null;
  return (
    <fieldset className={`${css.group} ${css.top}`} aria-label="Настройки ЦТМ" data-testid="dtm-settings">
      <legend>Настройки ЦТМ</legend>
      <label className={css.field}>
        <span>Растр на рельефе</span>
        <select className={css.input} value={dtm.raster_id ?? 0} aria-label="Растр на рельефе" onChange={(e) => void set({ raster_id: Number(e.target.value) })}>
          <option value={0}>— без растра —</option>
          {options.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      {!options.length && <p className={css.note}>В проекте нет растров в плане: загрузите карту или ортофото с привязкой.</p>}
      {vertical > 0 && <p className={css.note}>Вертикальные растры ({counted(vertical, "растр", "растра", "растров")}) на рельеф не кладутся и в списке не показаны.</p>}
      <Check checked={dtm.clip} title="Виден только растр на ЦТМ; без галочки остальное плоско вокруг на своей высоте" onCommit={(clip) => set({ clip })}>
        Скрыть данные за ЦТМ
      </Check>
      <div className={css.row}>
        <label className={css.check} title="Множитель изображения: белый — без изменений">
          Цвет растра
          <ColorField value={dtm.tint} label="Цвет растра" disabled={!draped} onCommit={(tint) => set({ tint })} />
        </label>
        <button type="button" className={css.btn} disabled={!draped || dtm.tint === "#ffffff"} title="Белый: изображение без изменений" onClick={() => void set({ tint: "#ffffff" })}>
          Без изменений
        </button>
      </div>
      <div className={css.row} title="Перекрытие собственного цвета поверхности: 0 — растра не видно, 1 — только растр">
        <span>Непрозрачность растра</span>
        <Slider value={dtm.opacity} min={0} max={1} step={0.05} label="Непрозрачность растра на ЦТМ" disabled={!draped} onCommit={(opacity) => set({ opacity })} />
      </div>
      <Check checked={dtm.shade} title="Свет и тень на изображении; без галочки — цвета как есть" onCommit={(shade) => set({ shade })}>
        Тени рельефа
      </Check>
      <div className={css.row}>
        <button type="button" className={css.btn} title="Настройки рельефа сбросятся, растр снимется" onClick={() => void back()}>
          Вернуть в каркасы
        </button>
      </div>
    </fieldset>
  );
}
