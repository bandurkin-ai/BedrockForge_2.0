import { Fragment, useEffect, useState } from "react";
import { blockMeta, getBlock, type BlockAttribute, type BlockInfo, type Dataset } from "../api";
import { CATEGORY_COLORS, type BlockStyle } from "../viz/blockStyle";

const fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const UNITS: BlockAttribute["unit"][] = ["g/t", "%", "ppm", "ppb", null];
const UNIT_TITLES: Record<string, string> = { "g/t": "г/т", "%": "%", ppm: "ppm", ppb: "ppb", "": "—" };
export const unitTitle = (u: BlockAttribute["unit"] | undefined) => UNIT_TITLES[u ?? ""] ?? u ?? "—";
const LEVEL_TITLES = { error: "Ошибка", warning: "Внимание", info: "Сведения" } as const;

/** Properties of a block model: description, checks, how it is shown, fields, the picked block. */
export function BlockModelPanel({ dataset, style, picked, onStyle, onUnit, onReport }: {
  dataset: Dataset;
  style: BlockStyle;
  /** block number picked in the view, or null */
  picked: number | null;
  onStyle: (next: BlockStyle) => void;
  onUnit: (key: string, unit: BlockAttribute["unit"]) => void;
  onReport: () => void;
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
          <select value={style.attribute ?? ""} onChange={(e) => set({ attribute: e.target.value || null, min: "", max: "", from: "", to: "", hidden: [] })} aria-label="Цвет по полю">
            <option value="">— один цвет —</option>
            {meta.attributes.map((a) => <option key={a.key} value={a.key}>{a.name}{a.kind === "category" ? " (категории)" : a.unit ? `, ${unitTitle(a.unit)}` : ""}</option>)}
          </select>
        </label>
        {attr?.kind === "number" && (
          <>
            <div className="pair">
              <label>Шкала от<input value={style.min} onChange={(e) => set({ min: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Шкала от" /></label>
              <label>до<input value={style.max} onChange={(e) => set({ max: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Шкала до" /></label>
            </div>
            <label className="check"><input type="checkbox" checked={style.log} onChange={(e) => set({ log: e.target.checked })} /> логарифмическая</label>
            <div className="pair">
              <label>Показать от<input value={style.from} onChange={(e) => set({ from: e.target.value })} placeholder="все" inputMode="decimal" aria-label="Показать блоки от" title="Только блоки со значением не меньше (например, бортовое)" /></label>
              <label>до<input value={style.to} onChange={(e) => set({ to: e.target.value })} placeholder="все" inputMode="decimal" aria-label="Показать блоки до" /></label>
            </div>
          </>
        )}
        {attr?.kind === "category" && (
          <div className="block-categories">
            {(attr.labels ?? []).map((label, i) => (
              <label key={label} className="check">
                <input type="checkbox" checked={!style.hidden.includes(label)}
                  onChange={(e) => set({ hidden: e.target.checked ? style.hidden.filter((h) => h !== label) : [...style.hidden, label] })} />
                <span className="swatch-static" style={{ background: CATEGORY_COLORS[i % CATEGORY_COLORS.length] }} /> {label}
              </label>
            ))}
          </div>
        )}
        <label>Непрозрачность
          <input type="range" min={0.1} max={1} step={0.05} value={style.opacity} onChange={(e) => set({ opacity: Number(e.target.value) })} aria-label="Непрозрачность блоков" />
        </label>
      </div>

      {block && (
        <>
          <h3 className="props-sub">Блок № {fmt(block.index, 0)}</h3>
          <dl className="props-list">
            <dt>Центр</dt><dd>{block.centre.map((v) => fmt(v, 2)).join("; ")}</dd>
            <dt>Размер</dt><dd>{block.size.map((v) => fmt(v, 3)).join(" × ")}</dd>
            {Object.entries(block.values).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd>{typeof v === "number" ? fmt(v, 4) : v ?? "—"}</dd></Fragment>
            ))}
          </dl>
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
