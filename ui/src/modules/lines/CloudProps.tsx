/**
 * «Свойства» of a point cloud (ADR 0014 §2, §6): points, extent, source (LAS: version, format of points, CRS),
 * the table of classes (code, ASPRS name in Russian, points, colour, a check box of visibility), the colour mode
 * («Класс», «Цвет слоя», `h`, `dz` — those the cloud has), the point picked in the 3D window (X, Y, Z of the core,
 * class, return, `h`, `dz`) and the points by pages. The mode and the hidden classes are kept by the project in
 * the view key `cloud_styles` (`patchEntry`): the 3D window follows at once. The name, the colour and the
 * visibility of the layer go through the registry (`object.rename`, `object.color`, `layer.show`).
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { errorText, NO_COLOR, num } from "../../shell/props";
import { ColorField, Facts, useRun } from "../../shell/PropsParts";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { CLOUD_STYLES_KEY, cloudStyleOf, type CloudColorMode, type CloudStyle } from "../../viz/lines/cloud";
import { updateView, viewStore } from "../scene/bind";
import { pickedObject, type LineFileInfo, type LineLayerInfo } from "../scene/objects";
import { patchEntry } from "../scene/viewSync";
import { loadAttrs } from "./actions";
import { classesCount, classLabel, classRows, cloudFacts, cloudOf, modeOf, modeOptions, pointFacts, shownText, withAllClasses, withClass, type CloudPage } from "./classes";
import { ATTR_PAGE, attrText, pager, pageStart, pickedLine } from "./props";
import { layerKey } from "./tree";

export function CloudProps({ layer, file }: { layer: LineLayerInfo; file: LineFileInfo }) {
  const app = useApp();
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const picked = useStore(pickedObject, (p) => p.picked);
  const styles = useStore(viewStore, (k) => k[CLOUD_STYLES_KEY]);
  const { run, error, setError } = useRun();
  const cloud = cloudOf(layer);
  if (!cloud) return null;
  const style = cloudStyleOf(styles, layer.id);
  const mode = modeOf(cloud, style);
  const rows = classRows(cloud.classes, style);
  const line = pickedLine(picked, layer.id);
  const save = (next: CloudStyle) => {
    try {
      updateView(CLOUD_STYLES_KEY, patchEntry(String(layer.id), next));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <section className={css.panel} aria-label={`Свойства: ${layer.name}`} data-testid="props-cloud">
      <header className={css.head}>
        <span className={css.kind}>Облако точек</span>
        <h4 className={css.name} title={layer.name}>
          {layer.name}
        </h4>
        <button type="button" className={css.btn} onClick={() => void run("object.rename", { kind: "lines", id: layer.id })}>
          Переименовать…
        </button>
      </header>
      <div className={css.row}>
        <label className={css.check}>
          Цвет слоя
          <ColorField value={layer.color ?? NO_COLOR} label="Цвет слоя" onCommit={(color) => run("object.color", { kind: "lines", id: layer.id, color })} />
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={!hidden.includes(layerKey(layer.id))} onChange={(e) => void run("layer.show", { layer: "lines", id: layer.id, visible: e.target.checked })} />
          показано в 3D
        </label>
      </div>
      <Facts facts={cloudFacts(layer, file)} />
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
      <fieldset className={css.group} data-testid="props-cloud-mode">
        <legend>Цвет точек</legend>
        <label className={css.check}>
          Режим
          <select className={css.input} aria-label="Режим цвета" value={mode} onChange={(e) => save({ ...(style ?? {}), color: e.target.value as CloudColorMode })}>
            {modeOptions(cloud).map((o) => (
              <option key={o.mode} value={o.mode} disabled={o.why !== null} title={o.why ?? undefined}>
                {o.why ? `${o.title} — ${o.why}` : o.title}
              </option>
            ))}
          </select>
        </label>
        {!classesCount(mode) && <p className={css.note}>В этом режиме показаны все точки: видимость классов действует в режимах «Класс» и «Цвет слоя».</p>}
      </fieldset>
      <fieldset className={css.group} data-testid="props-cloud-classes">
        <legend>Классы</legend>
        {rows.length ? (
          <>
            <div className={css.tableWrap}>
              <table className={css.table} aria-label="Классы облака">
                <thead>
                  <tr>
                    <th scope="col">Класс</th>
                    <th scope="col">Точек</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.code}>
                      <td>
                        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <input type="checkbox" checked={r.shown} aria-label={`Показать класс ${classLabel(r.code)}`} onChange={(e) => save(withClass(style, r.code, e.target.checked))} />
                          <span aria-hidden style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: r.color }} />
                          {classLabel(r.code)}
                        </label>
                      </td>
                      <td>{count(r.points)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={css.row}>
              <span className={css.note}>{shownText(rows)}</span>
              <button type="button" className={css.btn} disabled={rows.every((r) => r.shown)} onClick={() => save(withAllClasses(style, rows, true))}>
                Показать все
              </button>
              <button type="button" className={css.btn} disabled={rows.every((r) => !r.shown)} onClick={() => save(withAllClasses(style, rows, false))}>
                Скрыть все
              </button>
            </div>
          </>
        ) : (
          <p className={css.note}>У точек облака нет классов.</p>
        )}
      </fieldset>
      <PickedPoint layer={layer} line={line} />
      <PointsTable key={`${layer.id}:${layer.version}`} layer={layer} picked={line} />
    </section>
  );
}

/** The point picked in the 3D window: X, Y, Z (float64 of the core), class, return, `h`, `dz`. */
function PickedPoint({ layer, line }: { layer: LineLayerInfo; line: number | null }) {
  const [page, setPage] = useState<CloudPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setPage(null);
    setError(null);
    if (line === null) return;
    let live = true;
    loadAttrs(layer.id, line, 1).then(
      (p) => live && setPage(p as CloudPage),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [layer.id, layer.version, line]);
  if (line === null) return <p className={css.note}>Щёлкните точку облака в 3D-окне — здесь появятся её координаты и класс.</p>;
  return (
    <fieldset className={css.group} data-testid="props-cloud-point">
      <legend>Точка № {count(line + 1)}</legend>
      {error ? <p className={css.error}>{error}</p> : page ? <Facts facts={pointFacts(page)} /> : <p className={css.note}>Загрузка…</p>}
    </fieldset>
  );
}

/** The points of the cloud by pages: X, Y, Z and the fields of the core (class, return, `h`, `dz`). */
function PointsTable({ layer, picked }: { layer: LineLayerInfo; picked: number | null }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CloudPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (picked !== null) setOffset(pageStart(picked));
  }, [picked]);
  useEffect(() => {
    let live = true;
    setError(null);
    loadAttrs(layer.id, offset).then(
      (p) => live && setPage(p as CloudPage),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [layer.id, offset]);
  const nav = pager(page?.total ?? layer.point_count, offset);
  const xyz = (v: number | undefined) => (typeof v === "number" ? num(v, 3) : "—");
  return (
    <fieldset className={css.group} data-testid="props-cloud-points">
      <legend>Точки облака</legend>
      {error && <p className={css.error}>{error}</p>}
      <div className={css.tableWrap}>
        <table className={css.table}>
          <thead>
            <tr>
              <th scope="col">№</th>
              <th scope="col">X</th>
              <th scope="col">Y</th>
              <th scope="col">Z</th>
              {(page?.fields ?? []).map((f) => (
                <th key={f.name} scope="col">
                  {f.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(page?.rows ?? []).map((row, i) => {
              const index = (page?.offset ?? offset) + i;
              const p = page?.xyz?.[i] ?? [];
              return (
                <tr key={index} className={index === picked ? css.picked : undefined}>
                  <td className={css.rowNo}>{count(index + 1)}</td>
                  <td>{xyz(p[0])}</td>
                  <td>{xyz(p[1])}</td>
                  <td>{xyz(p[2])}</td>
                  {row.map((v, k) => (
                    <td key={k}>{attrText(v)}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className={css.pager}>
        <span>{page ? nav.text : "Загрузка…"}</span>
        <button type="button" className={css.iconBtn} disabled={nav.prev === null} aria-label="Предыдущая страница" title={`Предыдущие ${ATTR_PAGE} точек`} onClick={() => nav.prev !== null && setOffset(nav.prev)}>
          <Icon name="chevronUp" size={14} />
        </button>
        <button type="button" className={css.iconBtn} disabled={nav.next === null} aria-label="Следующая страница" title={`Следующие ${ATTR_PAGE} точек`} onClick={() => nav.next !== null && setOffset(nav.next)}>
          <Icon name="chevronDown" size={14} />
        </button>
      </div>
    </fieldset>
  );
}
