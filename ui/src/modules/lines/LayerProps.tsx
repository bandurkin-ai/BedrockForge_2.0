/**
 * «Свойства» of a layer of lines or points (ТЗ 7.18; ADR 0011 §5): the name, the colour, visibility, counts,
 * length and closed lines; the attributes of the line picked in the 3D window; the table of attributes of the
 * layer by pages. Changes go through the registry (`object.rename`, `object.color`, `layer.show`). A point cloud
 * has «Свойства» of its own (`CloudProps.tsx`).
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { errorText, NO_COLOR } from "../../shell/props";
import { ColorField, Facts, useRun } from "../../shell/PropsParts";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { pickedObject, sceneObjects, type LineLayerInfo } from "../scene/objects";
import { loadAttrs } from "./actions";
import { CloudProps } from "./CloudProps";
import { ATTR_PAGE, attrText, featureFields, fieldTitle, findLayer, layerFacts, pageStart, pager, pickedLine, pickedTitle, rowFacts, type AttrPage } from "./props";
import { layerKey } from "./tree";

export function LayerProps({ id }: { id: number }) {
  const app = useApp();
  const files = useStore(sceneObjects, (o) => o.files);
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const picked = useStore(pickedObject, (p) => p.picked);
  const found = useMemo(() => findLayer(files, id), [files, id]);
  const { run, error } = useRun();
  if (!found) return null;
  const { layer, file } = found;
  if (layer.cloud) return <CloudProps layer={layer} file={file} />; // a point cloud (ADR 0014)
  const line = pickedLine(picked, id);
  return (
    <section className={css.panel} aria-label={`Свойства: ${layer.name}`} data-testid="props-layer">
      <header className={css.head}>
        <span className={css.kind}>{layer.points_only ? "Слой точек" : "Слой линий"}</span>
        <h4 className={css.name} title={layer.name}>
          {layer.name}
        </h4>
        <button type="button" className={css.btn} onClick={() => void run("object.rename", { kind: "lines", id })}>
          Переименовать…
        </button>
      </header>
      <div className={css.row}>
        <label className={css.check}>
          Цвет
          <ColorField value={layer.color ?? NO_COLOR} label="Цвет слоя" onCommit={(color) => run("object.color", { kind: "lines", id, color })} />
        </label>
        <button type="button" className={css.btn} disabled={layer.color === null} title="Слой рисуется цветом по умолчанию" onClick={() => void run("object.color", { kind: "lines", id, color: null })}>
          Без цвета
        </button>
        <label className={css.check}>
          <input type="checkbox" checked={!hidden.includes(layerKey(id))} onChange={(e) => void run("layer.show", { layer: "lines", id, visible: e.target.checked })} />
          показан в 3D
        </label>
      </div>
      <Facts facts={layerFacts(layer, file)} />
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
      <PickedLine layer={layer} line={line} />
      <AttrTable key={`${layer.id}:${layer.version}`} layer={layer} picked={line} />
    </section>
  );
}

/** The attributes of the line (point) picked in the 3D window. */
function PickedLine({ layer, line }: { layer: LineLayerInfo; line: number | null }) {
  const [page, setPage] = useState<AttrPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasFields = featureFields(layer).length > 0;
  useEffect(() => {
    setPage(null);
    setError(null);
    if (line === null || !hasFields) return;
    let live = true;
    loadAttrs(layer.id, line, 1).then(
      (p) => live && setPage(p),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [layer.id, layer.version, line, hasFields]);
  if (line === null) return <p className={css.note}>Щёлкните {layer.points_only ? "точку" : "линию"} слоя в 3D-окне — здесь появятся её атрибуты.</p>;
  return (
    <fieldset className={css.group} data-testid="props-picked-line">
      <legend>{pickedTitle(layer, line)}</legend>
      {!hasFields ? <p className={css.note}>У слоя нет атрибутов.</p> : error ? <p className={css.error}>{error}</p> : page ? <Facts facts={rowFacts(page)} /> : <p className={css.note}>Загрузка…</p>}
    </fieldset>
  );
}

/** The attributes of the layer: a row per line (per point), `ATTR_PAGE` rows on a page. */
function AttrTable({ layer, picked }: { layer: LineLayerInfo; picked: number | null }) {
  const fields = featureFields(layer);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AttrPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  // the page of the picked line is opened when the pick changes
  useEffect(() => {
    if (picked !== null) setOffset(pageStart(picked));
  }, [picked]);
  useEffect(() => {
    if (!fields.length) return;
    let live = true;
    setError(null);
    loadAttrs(layer.id, offset).then(
      (p) => live && setPage(p),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [layer.id, offset, fields.length]);
  if (!fields.length) return null;
  const total = page?.total ?? (layer.points_only ? layer.point_count : layer.line_count);
  const nav = pager(total, offset);
  return (
    <fieldset className={css.group} data-testid="props-attrs">
      <legend>Атрибуты слоя</legend>
      {error && <p className={css.error}>{error}</p>}
      <div className={css.tableWrap}>
        <table className={css.table}>
          <thead>
            <tr>
              <th scope="col">№</th>
              {(page?.fields ?? fields).map((f) => (
                <th key={f.name} scope="col">
                  {fieldTitle(f)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(page?.rows ?? []).map((row, i) => {
              const index = (page?.offset ?? offset) + i;
              return (
                <tr key={index} className={index === picked ? css.picked : undefined}>
                  <td className={css.rowNo}>{count(index + 1)}</td>
                  {row.map((v, k) => (
                    <td key={k} title={attrText(v)}>
                      {attrText(v)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className={css.pager}>
        <span>{page ? nav.text : "Загрузка…"}</span>
        <button type="button" className={css.iconBtn} disabled={nav.prev === null} aria-label="Предыдущая страница" title={`Предыдущие ${ATTR_PAGE} строк`} onClick={() => nav.prev !== null && setOffset(nav.prev)}>
          <Icon name="chevronUp" size={14} />
        </button>
        <button type="button" className={css.iconBtn} disabled={nav.next === null} aria-label="Следующая страница" title={`Следующие ${ATTR_PAGE} строк`} onClick={() => nav.next !== null && setOffset(nav.next)}>
          <Icon name="chevronDown" size={14} />
        </button>
      </div>
    </fieldset>
  );
}
