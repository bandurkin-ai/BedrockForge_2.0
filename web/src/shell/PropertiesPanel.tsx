import { Fragment, useEffect, useState } from "react";
import type { AuditRun, Dataset, LineLayerInfo, RasterInfo, RasterPatch, Summary, VerticalPlacement } from "../api";
import { blockMeta, checkWireframes, type BlockAttribute, type BlockInfo } from "../api";
import { AttributeEditor, type AttrValue } from "./AttributeEditor";
import { defaultBlockStyle, type BlockStyle } from "../viz/blockStyle";
import { BlockModelPanel } from "./BlockModelPanel";
import { HoleCard } from "../HoleCard";
import type { TableKind } from "../route";
import type { VizSettings } from "../viz/ComponentPanel";
import type { SelectedObject } from "./workspace";

const fmt = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });

/** Right dock: the selected object (hole, wireframe) or the active drillhole set. */
export function PropertiesPanel({
  datasets, activeSet, summary, audit, selectedHole, selected, refreshKey, onOpenTable, onClearHole, onRaster, sectionFrame,
  blockStyles, onBlockStyle, pickedBlock, onBlockUnit, onBlockReport, onWireframe, onBlockValues,
}: {
  datasets: Dataset[];
  activeSet: number | null;
  summary: Summary | null;
  audit: AuditRun | null;
  selectedHole: string | null;
  selected: SelectedObject | null;
  refreshKey: number;
  viz: VizSettings;
  onViz: (next: VizSettings) => void;
  onOpenTable: (kind: TableKind) => void;
  onClearHole: () => void;
  onRaster: (id: number, patch: RasterPatch) => void;
  /** The frame of the current section line, or null when there is none. */
  sectionFrame: () => VerticalPlacement | null;
  blockStyles?: Record<number, BlockStyle>;
  onBlockStyle?: (id: number, style: BlockStyle) => void;
  pickedBlock?: { modelId: number; index: number } | null;
  onBlockUnit?: (id: number, key: string, unit: BlockAttribute["unit"]) => void;
  onBlockReport?: (id: number) => void;
  /** Rename, recolour a wireframe or replace its attributes. */
  onWireframe?: (id: number, patch: { name?: string; color?: string; attributes?: Record<string, AttrValue> }) => Promise<unknown>;
  /** New values of a block's fields; resolves with the block as stored. */
  onBlockValues?: (id: number, index: number, values: Record<string, number | string | null>) => Promise<BlockInfo>;
}) {
  // What is drawn along the holes is set up in the tree: «Стволы» → right click.
  const color = null;
  const set = datasets.find((d) => d.id === activeSet);
  const pick = <T extends { id: number }>(kind: SelectedObject["kind"], list: (d: Dataset) => T[] | null) =>
    selected?.kind === kind ? datasets.flatMap((d) => (list(d) ?? []).map((x) => ({ ...x, file: d }))).find((x) => x.id === selected.id) : undefined;
  const wireframe = pick("wireframe", (d) => d.wireframes);
  const lines = pick("lines", (d) => d.lines);
  const raster = pick("raster", (d) => d.rasters);
  if (lines) return <LinesProps layer={lines} file={lines.file} />;
  const model = selected?.kind === "blocks" ? datasets.find((d) => d.id === selected.id && d.kind === "blocks") : undefined;
  if (model) {
    return (
      <BlockModelPanel
        dataset={model} style={blockStyles?.[model.id] ?? defaultBlockStyle(blockMeta(model))}
        picked={pickedBlock?.modelId === model.id ? pickedBlock.index : null}
        onStyle={(style) => onBlockStyle?.(model.id, style)} onUnit={(key, unit) => onBlockUnit?.(model.id, key, unit)}
        onReport={() => onBlockReport?.(model.id)}
        onValues={onBlockValues ? (index, values) => onBlockValues(model.id, index, values) : undefined}
      />
    );
  }
  if (raster) return <RasterProps raster={raster} file={raster.file} onRaster={onRaster} sectionFrame={sectionFrame} />;

  if (wireframe) {
    const { stats, extent } = wireframe;
    return (
      <div className="props">
        <h2 className="dock-title">Каркас</h2>
        {onWireframe ? <WireframeHead key={wireframe.id} id={wireframe.id} name={wireframe.name} color={wireframe.color} onWireframe={onWireframe} />
          : <div className="props-head"><span className="swatch-static" style={{ background: wireframe.color }} /><strong>{wireframe.name}</strong></div>}
        <dl className="props-list">
          <dt>Файл</dt><dd>{wireframe.file.name}</dd>
          <dt>Формат</dt><dd>{wireframe.file.meta.format ?? "—"}</dd>
          <dt>Вершины</dt><dd>{fmt(wireframe.point_count)}</dd>
          <dt>Треугольники</dt><dd>{fmt(wireframe.triangle_count)}</dd>
          <dt>Площадь</dt><dd>{fmt(stats.area)} м²</dd>
          <dt>Объём</dt><dd>{stats.volume !== null ? `${fmt(stats.volume)} м³` : "— (не замкнут)"}</dd>
          <dt>Замкнутость</dt><dd className={stats.closed ? "" : "sev warning"}>{stats.closed ? "замкнут" : `открытых рёбер: ${fmt(stats.open_edges)}`}</dd>
          {stats.degenerate > 0 && <><dt>Вырожденные</dt><dd className="sev warning">{fmt(stats.degenerate)}</dd></>}
          {stats.orientation === "inward" && <><dt>Нормали</dt><dd className="sev warning">внутрь (объём посчитан по модулю)</dd></>}
          {(stats.nonmanifold_edges ?? 0) > 0 && <><dt>Рёбра 3+ граней</dt><dd className="sev warning">{fmt(stats.nonmanifold_edges!)}</dd></>}
          {stats.checked ? (
            <>
              <dt>Несогласованные нормали</dt>
              <dd className={stats.orientation_conflicts ? "sev warning" : ""}>{stats.orientation_conflicts ? `${fmt(stats.orientation_conflicts)} рёбер` : "нет"}</dd>
              <dt>Самопересечения</dt>
              <dd className={stats.self_intersections?.pairs ? "sev error" : ""} title={stats.self_intersections?.samples.slice(0, 5).map((p) => p.map((v) => v.toFixed(1)).join(", ")).join("\n")}>
                {stats.self_intersections?.pairs ? `${fmt(stats.self_intersections.pairs)} пар треугольников` : "нет"}
              </dd>
            </>
          ) : (
            <>
              <dt>Проверки</dt>
              <dd>
                <span className="muted">не выполнялись </span>
                <button className="link" onClick={() => void checkWireframes(wireframe.file.id).catch(() => undefined)}>проверить</button>
              </dd>
            </>
          )}
          <dt>X</dt><dd>{fmt(extent.min[0])} – {fmt(extent.max[0])}</dd>
          <dt>Y</dt><dd>{fmt(extent.min[1])} – {fmt(extent.max[1])}</dd>
          <dt>Z</dt><dd>{fmt(extent.min[2])} – {fmt(extent.max[2])}</dd>
        </dl>
        {onWireframe ? (
          <AttributeEditor attributes={(stats.attributes ?? {}) as Record<string, AttrValue>} onSave={(attributes) => onWireframe(wireframe.id, { attributes })} />
        ) : stats.attributes && Object.keys(stats.attributes).length > 0 && (
          <>
            <h2 className="dock-title">Атрибуты</h2>
            <dl className="props-list">
              {Object.entries(stats.attributes).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}
            </dl>
          </>
        )}
      </div>
    );
  }

  if (selectedHole && activeSet !== null) {
    return (
      <div className="props">
        <h2 className="dock-title">Скважина{set ? ` · ${set.name}` : ""}</h2>
        <HoleCard setId={activeSet} holeId={selectedHole} refreshKey={refreshKey} onOpenTable={onOpenTable} onClose={onClearHole} />
        {color}
      </div>
    );
  }

  return (
    <div className="props">
      {set ? (
        <>
          <h2 className="dock-title">Набор скважин</h2>
          <dl className="props-list">
            <dt>Название</dt><dd>{set.name}</dd>
            <dt>Устья</dt><dd>{fmt(set.counts?.collar)}</dd>
            <dt>Замеры</dt><dd>{fmt(set.counts?.survey)}</dd>
            <dt>Пробы</dt><dd>{fmt(set.counts?.assay)}</dd>
            <dt>Компоненты</dt><dd>{set.components?.length ? set.components.map((c) => `${c.name} (${c.unit})`).join(", ") : "—"}</dd>
          </dl>
          <h2 className="dock-title">Аудит</h2>
          {audit ? (
            <dl className="props-list">
              <dt>Критично</dt><dd className="sev error">{audit.by_severity.error}</dd>
              <dt>Предупреждения</dt><dd className="sev warning">{audit.by_severity.warning}</dd>
              <dt>Информация</dt><dd className="sev info">{audit.by_severity.info}</dd>
              <dt>Запуск</dt><dd>{new Date(audit.created_at).toLocaleString("ru-RU")}{audit.stale && " · устарел"}</dd>
            </dl>
          ) : (
            <p className="muted small">Аудит ещё не запускался.</p>
          )}
          {summary && summary.imports.length > 0 && (
            <>
              <h2 className="dock-title">История импорта</h2>
              <ul className="props-imports">
                {summary.imports.map((j) => (
                  <li key={j.id}>
                    <span>{j.filename}</span>
                    <span className="muted small">{j.kind} · {j.row_count.toLocaleString("ru-RU")} строк · {new Date(j.created_at).toLocaleDateString("ru-RU")}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      ) : (
        <p className="muted small">Выберите набор скважин, скважину или каркас в дереве проекта.</p>
      )}
      {color}
    </div>
  );
}

/** Name and colour of a wireframe, edited in place: the name is saved on Enter or when the field is left. */
function WireframeHead({ id, name, color, onWireframe }: {
  id: number;
  name: string;
  color: string;
  onWireframe: (id: number, patch: { name?: string; color?: string }) => Promise<unknown>;
}) {
  const [text, setText] = useState(name);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(name), [name]);
  const save = (patch: { name?: string; color?: string }) => void onWireframe(id, patch).then(() => setError(null)).catch((e: Error) => setError(e.message));
  const commit = () => {
    const t = text.trim();
    if (!t) return setText(name);
    if (t !== name) save({ name: t });
  };
  return (
    <>
      <div className="wf-edit">
        <input type="color" value={color} title="Цвет каркаса" aria-label="Цвет каркаса" onChange={(e) => save({ color: e.target.value })} />
        <input type="text" value={text} maxLength={255} aria-label="Название каркаса" title="Название: Enter — сохранить"
          onChange={(e) => setText(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setText(name); }} />
      </div>
      {error && <div className="alert error small">{error}</div>}
    </>
  );
}

function Extent({ extent }: { extent: { min: number[]; max: number[] } }) {
  const axes = extent.min.length === 3 ? ["X", "Y", "Z"] : ["X", "Y"];
  return (
    <>
      {axes.map((axis, i) => (
        <Fragment key={axis}><dt>{axis}</dt><dd>{fmt(extent.min[i])} – {fmt(extent.max[i])}</dd></Fragment>
      ))}
    </>
  );
}

function LinesProps({ layer, file }: { layer: LineLayerInfo; file: Dataset }) {
  return (
    <div className="props">
      <h2 className="dock-title">Линии</h2>
      <div className="props-head"><span className="swatch-static" style={{ background: layer.color }} /><strong>{layer.name}</strong></div>
      <dl className="props-list">
        <dt>Файл</dt><dd>{file.name}</dd>
        <dt>Формат</dt><dd>{file.meta.format ?? "—"}</dd>
        <dt>Линий</dt><dd>{fmt(layer.line_count)}</dd>
        <dt>Замкнутых</dt><dd>{fmt(layer.stats.closed)}</dd>
        <dt>Точек</dt><dd>{fmt(layer.point_count)}</dd>
        <dt>Длина</dt><dd>{fmt(layer.stats.length)} м</dd>
        <Extent extent={layer.extent} />
      </dl>
      {layer.stats.flat && <p className="muted small">Линии плоские: все точки на высоте {fmt(layer.extent.min[2], 2)} м.</p>}
    </div>
  );
}

const FRAME_FIELDS: { key: keyof VerticalPlacement; label: string }[] = [
  { key: "x1", label: "Левый край X" },
  { key: "y1", label: "Левый край Y" },
  { key: "x2", label: "Правый край X" },
  { key: "y2", label: "Правый край Y" },
  { key: "z_top", label: "Верх Z" },
  { key: "z_bottom", label: "Низ Z" },
];

/** Edges and heights of a raster standing in a vertical plane (a section scan). */
function VerticalFrame({ raster, onRaster, sectionFrame }: {
  raster: RasterInfo; onRaster: (id: number, patch: RasterPatch) => void; sectionFrame: () => VerticalPlacement | null;
}) {
  const c = raster.georef.corners3d;
  const current: VerticalPlacement | null = c ? { x1: c[0][0], y1: c[0][1], x2: c[2][0], y2: c[2][1], z_top: c[0][2], z_bottom: c[2][2] } : null;
  const [form, setForm] = useState<Record<string, string>>({});
  useEffect(() => {
    setForm(current ? Object.fromEntries(FRAME_FIELDS.map((f) => [f.key, String(Math.round(current[f.key] * 100) / 100)])) : {});
  }, [raster.id, JSON.stringify(c)]); // eslint-disable-line react-hooks/exhaustive-deps

  const parsed = FRAME_FIELDS.map((f) => Number((form[f.key] ?? "").replace(",", ".")));
  const valid = current !== null && parsed.every(Number.isFinite);
  const changed = valid && FRAME_FIELDS.some((f, i) => Math.abs(parsed[i] - current![f.key]) > 1e-6);
  const save = () => valid && onRaster(raster.id, { vertical: Object.fromEntries(FRAME_FIELDS.map((f, i) => [f.key, parsed[i]])) as unknown as VerticalPlacement });
  const onSection = () => {
    const frame = sectionFrame();
    if (frame) onRaster(raster.id, { vertical: frame });
  };
  const frameAvailable = sectionFrame() !== null;

  return (
    <div className="props-form">
      {current && FRAME_FIELDS.map((f) => (
        <label key={f.key}>
          {f.label}
          <input value={form[f.key] ?? ""} inputMode="decimal" onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && save()} aria-label={f.label} />
        </label>
      ))}
      <div className="props-actions">
        {current && <button onClick={save} disabled={!changed}>Применить</button>}
        <button onClick={onSection} disabled={!frameAvailable} title={frameAvailable ? "Края — по линии разреза, высоты — по данным проекта" : "Сначала проведите линию разреза"}>
          На текущий разрез
        </button>
      </div>
    </div>
  );
}

function RasterProps({ raster, file, onRaster, sectionFrame }: {
  raster: RasterInfo; file: Dataset; onRaster: (id: number, patch: RasterPatch) => void; sectionFrame: () => VerticalPlacement | null;
}) {
  const g = raster.georef;
  const vertical = g.placement === "vertical";
  const [elevation, setElevation] = useState(raster.elevation === null ? "" : String(raster.elevation));
  const [shift, setShift] = useState("");
  const shiftValue = Number(shift.trim().replace(",", "."));
  const canShift = shift.trim() !== "" && Number.isFinite(shiftValue) && shiftValue !== 0 && (vertical || raster.elevation !== null);
  useEffect(() => setElevation(raster.elevation === null ? "" : String(raster.elevation)), [raster.id, raster.elevation]);
  const saveElevation = () => {
    const text = elevation.trim().replace(",", ".");
    if (text === "") {
      if (raster.elevation !== null) onRaster(raster.id, { clear_elevation: true });
      return;
    }
    const value = Number(text);
    if (Number.isFinite(value) && value !== raster.elevation) onRaster(raster.id, { elevation: value });
  };
  return (
    <div className="props">
      <h2 className="dock-title">Растр</h2>
      <div className="props-head"><strong>{raster.name}</strong></div>
      <dl className="props-list">
        <dt>Файл</dt><dd>{file.name}</dd>
        {file.meta.sidecars && file.meta.sidecars.length > 0 && <><dt>Привязка из</dt><dd>{file.meta.sidecars.join(", ")}</dd></>}
        <dt>Размер</dt><dd>{fmt(raster.width)} × {fmt(raster.height)} пикс.</dd>
        <dt>Привязка</dt><dd>{g.source}</dd>
        <dt>Пиксель</dt><dd>{fmt(g.pixel_size, 3)} м</dd>
        {vertical
          ? <><dt>Положение</dt><dd>вертикально, азимут {fmt(g.azimuth, 1)}°</dd></>
          : <><dt>Поворот</dt><dd>{fmt(g.rotation, 3)}°</dd></>}
        <Extent extent={raster.extent} />
      </dl>
      <h2 className="dock-title">{vertical ? "Плоскость растра" : "В вертикальную плоскость"}</h2>
      {!vertical && <p className="muted small">Скан разреза можно поставить в плоскость текущей линии разреза.</p>}
      <VerticalFrame raster={raster} onRaster={onRaster} sectionFrame={sectionFrame} />
      <h2 className="dock-title">Отображение</h2>
      <div className="props-form">
        {!vertical && <label>
          Высота, м
          <input value={elevation} placeholder="авто" onChange={(e) => setElevation(e.target.value)} onBlur={saveElevation}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} aria-label="Высота растра" />
        </label>}
        <label title={vertical || raster.elevation !== null ? "Поднять (+) или опустить (−) растр на заданное число метров" : "Сначала задайте высоту растра"}>
          Сдвиг по Z, м
          <span className="props-inline">
            <input value={shift} inputMode="decimal" placeholder="+10 или −5" onChange={(e) => setShift(e.target.value)} aria-label="Сдвиг растра по Z, м"
              onKeyDown={(e) => e.key === "Enter" && canShift && (onRaster(raster.id, { z_shift: shiftValue }), setShift(""))} />
            <button disabled={!canShift} onClick={() => { onRaster(raster.id, { z_shift: shiftValue }); setShift(""); }}>Сдвинуть</button>
          </span>
        </label>
        <label>
          Непрозрачность
          <input type="range" min={0.05} max={1} step={0.05} defaultValue={raster.opacity} key={raster.id}
            onPointerUp={(e) => onRaster(raster.id, { opacity: Number((e.target as HTMLInputElement).value) })}
            onKeyUp={(e) => onRaster(raster.id, { opacity: Number((e.target as HTMLInputElement).value) })} aria-label="Непрозрачность растра" />
        </label>
        <p className="muted small">
          {vertical ? "Растр стоит в своей плоскости; при отсечении он виден, если попадает в окно разреза." : "Пусто — растр ложится под самый низкий объект сцены. Отсечение по разрезу действует и на растр."}
        </p>
      </div>
      {g.checks.length > 0 && (
        <>
          <h2 className="dock-title">Проверка по опорным точкам</h2>
          {g.checks.map((c) => (
            <div key={c.source} className="georef-check">
              <p className="small">{c.source}: макс. отклонение <strong className={c.max > g.pixel_size * 2 ? "sev warning" : ""}>{fmt(c.max, 2)} м</strong></p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Точка</th><th>dX</th><th>dY</th>{vertical && <th>dZ</th>}<th>м</th></tr></thead>
                  <tbody>{c.points.map((p, i) => <tr key={i}><td>{p.label || i + 1}</td><td>{fmt(p.dx, 2)}</td><td>{fmt(p.dy, 2)}</td>{vertical && <td>{fmt(p.dz, 2)}</td>}<td>{fmt(p.d, 2)}</td></tr>)}</tbody>
                </table>
              </div>
            </div>
          ))}
        </>
      )}
      {g.crs && <p className="muted small">Система координат (TAB): {g.crs}</p>}
      {g.notes.length > 0 && <ul className="muted small notes">{g.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </div>
  );
}
