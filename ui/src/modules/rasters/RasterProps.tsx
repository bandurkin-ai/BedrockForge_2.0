/**
 * «Свойства» of a raster (ТЗ 7.19; ADR 0011 §5): the georeference and the deviations at the control points,
 * opacity, «Высота» of a raster in plan or the edges of a vertical one, «Сдвиг по Z» + «Сдвинуть» (not
 * available without a height — with the reason), «На текущий разрез» (own confirmation window). Changes go
 * through the registry: `raster.set`, `raster.shift`, `raster.place_on_section`.
 */
import { useEffect, useState, type KeyboardEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { fieldNumber } from "../../shell/props";
import { Facts, Slider, useRun } from "../../shell/PropsParts";
import { useRegistryVersion, useStore } from "../../shell/useStore";
import css from "../../shell/Props.module.css";
import { sceneObjects, type RasterInfo } from "../scene/objects";
import { checkLines, EDGE_LABELS, edgeTexts, OPACITY_MAX, OPACITY_MIN, parseEdges, parseField, rasterEdges, rasterFacts, shiftReason, type EdgeKey } from "./props";
import { rasterKey } from "./tree";

type Run = (id: string, args?: Record<string, unknown>) => Promise<boolean>;

export function RasterProps({ id }: { id: number }) {
  const app = useApp();
  const raster = useStore(sceneObjects, (o) => o.rasters.find((r) => r.id === id));
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const { run, error, setError } = useRun();
  if (!raster) return null;
  return (
    <section className={css.panel} aria-label={`Свойства: ${raster.name}`} data-testid="props-raster">
      <header className={css.head}>
        <span className={css.kind}>{raster.vertical ? "Вертикальный растр" : "Растр"}</span>
        <h4 className={css.name} title={raster.name}>
          {raster.name}
        </h4>
        <button type="button" className={css.btn} onClick={() => void run("object.rename", { kind: "raster", id })}>
          Переименовать…
        </button>
      </header>
      <div className={css.row}>
        <label className={css.check}>
          <input type="checkbox" checked={!hidden.includes(rasterKey(id))} onChange={(e) => void run("layer.show", { layer: "raster", id, visible: e.target.checked })} />
          показан в 3D
        </label>
        <button type="button" className={css.btn} onClick={() => void run("rasters.export", { raster_id: id })}>
          Экспорт…
        </button>
      </div>
      <Facts facts={rasterFacts(raster)} />
      <Notes raster={raster} />
      <div className={css.row}>
        <span>Непрозрачность</span>
        <Slider value={raster.opacity} min={OPACITY_MIN} max={OPACITY_MAX} step={0.05} label="Непрозрачность растра" onCommit={(opacity) => run("raster.set", { raster_id: id, opacity })} />
      </div>
      <Placement raster={raster} run={run} fail={setError} />
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/** Deviations at the control points of the georeference files and the notes of the import. */
function Notes({ raster }: { raster: RasterInfo }) {
  const checks = checkLines(raster);
  if (!checks.length && !raster.warnings.length) return null;
  return (
    <>
      {checks.length > 0 && (
        <div>
          <h5 className={css.sub}>Отклонения по опорным точкам</h5>
          <ul className={css.list} data-testid="raster-checks">
            {checks.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {raster.warnings.length > 0 && (
        <ul className={`${css.list} ${css.warn}`}>
          {raster.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function Placement({ raster, run, fail }: { raster: RasterInfo; run: Run; fail: (text: string | null) => void }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  useStore(app.store, (s) => s.hasSection); // the reason of «На текущий разрез» follows the section line
  const stored = edgeTexts(rasterEdges(raster));
  const storedKey = JSON.stringify(stored);
  const [elevation, setElevation] = useState(fieldNumber(raster.elevation));
  const [edges, setEdges] = useState(stored);
  const [dz, setDz] = useState("");
  const [zTop, setZTop] = useState("");
  // a field starts again from the stored value when that value changes — what is typed in the others stays
  useEffect(() => setElevation(fieldNumber(raster.elevation)), [raster.elevation]);
  useEffect(() => setEdges(JSON.parse(storedKey) as typeof stored), [storedKey]);
  const id = raster.id;
  const noShift = shiftReason(raster);
  const noSection = app.registry.reason("raster.place_on_section");

  const applyElevation = () => {
    const p = parseField(elevation, "Высота");
    if (p.error) return fail(p.error);
    void run("raster.set", { raster_id: id, elevation: p.value });
  };
  const applyEdges = () => {
    const p = parseEdges(edges);
    if (!p.edges) return fail(p.error);
    void run("raster.set", { raster_id: id, ...p.edges });
  };
  const shift = () => {
    const p = parseField(dz, "Сдвиг по Z", true);
    if (p.error) return fail(p.error);
    void run("raster.shift", { raster_id: id, dz: p.value }).then((ok) => ok && setDz(""));
  };
  const place = () => {
    const p = parseField(zTop, "Верх Z");
    if (p.error) return fail(p.error);
    void run("raster.place_on_section", p.value === null ? { raster_id: id } : { raster_id: id, z_top: p.value });
  };
  const onEnter = (action: () => void) => (e: KeyboardEvent) => {
    if (e.key === "Enter") action();
  };
  const edge = (key: EdgeKey, label: string) => (
    <label key={key} className={css.field}>
      <span>{label}</span>
      <input className={css.input} inputMode="decimal" value={edges[key]} aria-label={label} onChange={(e) => setEdges((s) => ({ ...s, [key]: e.target.value }))} onKeyDown={onEnter(applyEdges)} />
    </label>
  );

  return (
    <>
      {raster.vertical ? (
        <fieldset className={css.group} data-testid="raster-edges">
          <legend>Положение в вертикальной плоскости</legend>
          <div className={css.pair}>{EDGE_LABELS.map(([key, label]) => edge(key, label))}</div>
          <div className={css.row}>
            <button type="button" className={css.btn} onClick={applyEdges}>
              Применить
            </button>
          </div>
        </fieldset>
      ) : (
        <fieldset className={css.group}>
          <legend>Высота</legend>
          <div className={css.row}>
            <label className={css.field}>
              <span>Высота, м</span>
              <input className={css.input} inputMode="decimal" value={elevation} placeholder="под самым низким объектом" aria-label="Высота, м" onChange={(e) => setElevation(e.target.value)} onKeyDown={onEnter(applyElevation)} />
            </label>
            <button type="button" className={css.btn} onClick={applyElevation}>
              Применить
            </button>
          </div>
          <p className={css.note}>Пусто — растр лежит под самым низким объектом.</p>
        </fieldset>
      )}
      <fieldset className={css.group}>
        <legend>Сдвиг по Z</legend>
        <div className={css.row}>
          <label className={css.field}>
            <span>Сдвиг по Z, м</span>
            <input className={css.input} inputMode="decimal" value={dz} placeholder="например −5" aria-label="Сдвиг по Z, м" disabled={noShift !== null} onChange={(e) => setDz(e.target.value)} onKeyDown={onEnter(shift)} />
          </label>
          <button type="button" className={css.btn} disabled={noShift !== null} title={noShift ?? "Плюс — вверх, минус — вниз"} onClick={shift}>
            Сдвинуть
          </button>
        </div>
        <p className={css.note} data-testid="raster-shift-note">
          {noShift ?? (raster.vertical ? "Сдвигаются все углы растра: плюс — вверх, минус — вниз." : "Плюс — вверх, минус — вниз.")}
        </p>
      </fieldset>
      <fieldset className={css.group}>
        <legend>Разрез</legend>
        <div className={css.row}>
          <label className={css.field}>
            <span>Верх Z, м</span>
            <input className={css.input} inputMode="decimal" value={zTop} placeholder="по высотам данных" aria-label="Верх Z на разрезе, м" disabled={noSection !== null} onChange={(e) => setZTop(e.target.value)} onKeyDown={onEnter(place)} />
          </label>
          <button type="button" className={css.btn} disabled={noSection !== null} title={noSection ?? "Поставить растр в плоскость линии разреза"} onClick={place}>
            На текущий разрез
          </button>
        </div>
        <p className={css.note}>{noSection ?? "Растр встанет в плоскость линии разреза; с «Верх Z» низ считается по пропорциям изображения."}</p>
      </fieldset>
    </>
  );
}
