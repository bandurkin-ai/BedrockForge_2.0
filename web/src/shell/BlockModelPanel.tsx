import { Fragment, useEffect, useState } from "react";
import { blockMeta, getBlock, type BlockAttribute, type BlockInfo, type Dataset } from "../api";
import { activeConditions, type BlockStyle } from "../viz/blockStyle";
import { Icon } from "./icons";
import { BlockValuesEditor } from "./AttributeEditor";

const fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const UNITS: BlockAttribute["unit"][] = ["g/t", "%", "ppm", "ppb", null];
const UNIT_TITLES: Record<string, string> = { "g/t": "г/т", "%": "%", ppm: "ppm", ppb: "ppb", "": "—" };
export const unitTitle = (u: BlockAttribute["unit"] | undefined) => UNIT_TITLES[u ?? ""] ?? u ?? "—";
const LEVEL_TITLES = { error: "Ошибка", warning: "Внимание", info: "Сведения" } as const;

/** Properties of a block model: description, checks, how it is shown, fields, the picked block. */
export function BlockModelPanel({ dataset, style, picked, onStyle, onUnit, onReport, onValues, onSettings }: {
  dataset: Dataset;
  style: BlockStyle;
  /** block number picked in the view, or null */
  picked: number | null;
  onStyle: (next: BlockStyle) => void;
  onUnit: (key: string, unit: BlockAttribute["unit"]) => void;
  onReport: () => void;
  /** Opens the settings window of the model (restriction, colouring, view). */
  onSettings?: () => void;
  /** Saves new values of the picked block's fields; resolves with the stored block. */
  onValues?: (index: number, values: Record<string, number | string | null>) => Promise<BlockInfo>;
}) {
  const meta = blockMeta(dataset);
  const g = meta.grid;
  const attr = meta.attributes.find((a) => a.key === style.attribute) ?? null;
  const set = (patch: Partial<BlockStyle>) => onStyle({ ...style, ...patch });
  const [block, setBlock] = useState<BlockInfo | null>(null);
  useEffect(() => {
    if (picked === null) return setBlock(null);
    let alive = true;
    getBlock(dataset.id, picked).then((b) => alive && setBlock(b)).catch(() => alive && setBlock(null));
    return () => {
      alive = false;
    };
  }, [dataset.id, picked]);
  const errors = meta.checks.filter((c) => c.level === "error").length;
  const conditions = activeConditions(style.filter, meta.attributes).length;

  return (
    <div className="props block-props">
      <h2 className="dock-title">Блочная модель</h2>
      <div className="props-head"><strong>{dataset.name}</strong></div>
      <dl className="props-list">
        <dt>Формат</dt><dd>{meta.format}</dd>
        <dt>Блоков</dt><dd>{fmt(meta.block_count, 0)}</dd>
        <dt>Объём</dt><dd>{fmt(meta.volume, 0)} м³</dd>
        <dt>Родительский блок</dt><dd>{g.parent.map((v) => fmt(v, 3)).join(" × ")} м{g.from_file ? "" : " (по данным)"}</dd>
        <dt>Сетка</dt><dd>{g.counts.join(" × ")}, занято {fmt(g.parents_used, 0)}</dd>
        <dt>Начало (угол)</dt><dd>{g.origin.map((v) => fmt(v, 2)).join("; ")}</dd>
        <dt>Субблоки</dt><dd>{g.subblocked ? "есть" : "нет"}</dd>
        <dt>Поворот</dt><dd>{g.rotation ? `${g.rotation}°` : "нет"}</dd>
      </dl>
      <button className="primary" onClick={onReport} title="Тоннаж, содержания и металл по бортовым содержаниям">Отчёт по бортам…</button>

      <h3 className="props-sub">Проверки{errors ? ` · ошибок ${errors}` : ""}</h3>
      <ul className="block-checks">
        {meta.checks.map((c, i) => (
          <li key={i} className={`check-${c.level}`} title={LEVEL_TITLES[c.level]}>
            <span className={`dot ${c.level}`} />
            <span>{c.message}{c.count !== null ? `: ${fmt(c.count, 0)}` : ""}</span>
          </li>
        ))}
        {meta.notes.map((n, i) => <li key={`n${i}`} className="muted small">{n}</li>)}
      </ul>

      <h3 className="props-sub">Показ</h3>
      <div className="props-form">
        <label>Цвет по
          <select value={style.attribute ?? ""} aria-label="Цвет по полю"
            onChange={(e) => {
              const a = meta.attributes.find((x) => x.key === e.target.value);
              onStyle({ ...style, attribute: a ? a.key : null, min: "", max: "", hidden: [], catColors: {}, classes: [], colorBy: "scale", log: a?.unit === "g/t" || a?.unit === "ppm" || a?.unit === "ppb", palette: undefined });
            }}>
            <option value="">— один цвет —</option>
            {meta.attributes.map((a) => <option key={a.key} value={a.key}>{a.name}{a.kind === "category" ? " (категории)" : a.unit ? `, ${unitTitle(a.unit)}` : ""}</option>)}
          </select>
        </label>
        <label>Непрозрачность
          <input type="range" min={0.1} max={1} step={0.05} value={style.opacity} onChange={(e) => set({ opacity: Number(e.target.value) })} aria-label="Непрозрачность блоков" />
        </label>
        <p className="muted small block-summary">
          {attr ? (attr.kind === "category" ? "категории" : style.colorBy === "classes" ? `градации (${style.classes.length})` : "шкала") : "один цвет"}
          {style.palette ? ` «${style.palette.name}»` : ""}
          {" · "}{conditions ? `ограничитель: условий ${conditions}` : "без ограничителя"}
          {" · "}{style.view === "shell" ? "внешняя оболочка" : "все блоки"}{style.slice ? ", срез на разрезе" : ""}{style.edges ? ", края" : ""}
        </p>
        <button className="props-gear" onClick={onSettings} title="Ограничитель, раскраска по полю (градации, шкала, из скважин), оболочка / все блоки, срез на разрезе, края">
          <Icon name="gear" size={14} /> Настройки показа…
        </button>
      </div>

      {block && (
        <>
          <h3 className="props-sub">Блок № {fmt(block.index, 0)}</h3>
          <dl className="props-list">
            <dt>Центр</dt><dd>{block.centre.map((v) => fmt(v, 2)).join("; ")}</dd>
            <dt>Размер</dt><dd>{block.size.map((v) => fmt(v, 3)).join(" × ")}</dd>
            {!onValues && Object.entries(block.values).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd>{typeof v === "number" ? fmt(v, 4) : v ?? "—"}</dd></Fragment>
            ))}
          </dl>
          {onValues && (
            <BlockValuesEditor values={block.values}
              fields={meta.attributes.map((a) => ({ name: a.name, kind: a.kind, labels: a.labels }))}
              onSave={async (values) => setBlock(await onValues(block.index, values))} />
          )}
        </>
      )}

      <h3 className="props-sub">Поля ({meta.attributes.length})</h3>
      <table className="block-fields">
        <thead><tr><th>Поле</th><th>Ед.</th><th>Мин</th><th>Макс</th><th title="Среднее, взвешенное по объёму">Среднее</th></tr></thead>
        <tbody>
          {meta.attributes.map((a) => (
            <tr key={a.key}>
              <td title={`${a.source}${a.stats.empty ? `, пустых ${a.stats.empty}` : ""}`}>{a.name}</td>
              {a.kind === "number" ? (
                <>
                  <td>
                    <select value={a.unit ?? ""} onChange={(e) => onUnit(a.key, (e.target.value || null) as BlockAttribute["unit"])} aria-label={`Единицы: ${a.name}`}>
                      {UNITS.map((u) => <option key={u ?? ""} value={u ?? ""}>{unitTitle(u)}</option>)}
                    </select>
                  </td>
                  <td className="num">{fmt(a.stats.min, 3)}</td>
                  <td className="num">{fmt(a.stats.max, 3)}</td>
                  <td className="num">{fmt(a.stats.mean_by_volume, 3)}</td>
                </>
              ) : (
                <td colSpan={4} className="muted">категорий {a.stats.categories}: {(a.stats.top ?? []).slice(0, 4).map((t) => t.label).join(", ")}{(a.stats.categories ?? 0) > 4 ? " …" : ""}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
