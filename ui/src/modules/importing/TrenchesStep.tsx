/**
 * Step «Канавы по полилиниям» of the import wizard (ТЗ 7.35 Д-16; ADR 0011 §5): the file of polylines (a STR,
 * a DXF or a table of vertices), where the name of a trench comes from — the string or a field, — the layers,
 * «Обратить направление» for all or for single trenches, and the preview of what will be loaded.
 */
import { useEffect, useState } from "react";
import { pickFilesIn } from "../../shell/lastFolder";
import { useStore } from "../../shell/useStore";
import { isVertexTable, PREVIEW_LIMIT, toggleReverse, trenchSummary, type TrenchState } from "./trenches";
import type { Wizard } from "./wizard";
import css from "./ImportWizard.module.css";

const ENCODINGS = ["", "utf-8", "cp1251", "cp866", "koi8-r", "latin-1"];
const num = (v: number, digits = 2) => v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function TrenchesStep({ wizard }: { wizard: Wizard }) {
  const t = useStore(wizard.store, (s) => s.trench);
  const p = t.preview;
  const table = isVertexTable(t);
  const pick = async () => {
    const [file] = await pickFilesIn("trenches", false);
    if (file) await wizard.setTrenchFile(file);
  };
  const allLayers = p?.layers.map((l) => l.name) ?? [];
  const taken = (name: string) => t.layers === null || t.layers.includes(name);
  const setLayer = (name: string, on: boolean) => {
    const next = allLayers.filter((n) => (n === name ? on : taken(n)));
    void wizard.patchTrench({ layers: next.length === allLayers.length ? null : next });
  };

  return (
    <>
      <section className={css.card} aria-label="Файл линий канав" data-testid="trench-file">
        <header className={css.cardHead}>
          <b>{t.name || "Файл не выбран"}</b>
          {t.busy && <span className={css.note}>Чтение…</span>}
          <button type="button" className={css.linkBtn} onClick={() => void pick()}>
            {t.path ? "Выбрать другой…" : "Выбрать файл…"}
          </button>
        </header>
        {!t.path && <div className={css.note}>Micromine STR и PIT, Surpac STR, DXF или таблица вершин «канава, X, Y, Z» (CSV, TXT, DAT).</div>}
        {t.error && <div className={css.error}>{t.error}</div>}
        {t.path && (
          <>
            <div className={css.options}>
              <label>
                Кодировка
                <select value={t.encoding ?? ""} onChange={(e) => void wizard.patchTrench({ encoding: e.target.value || null })} aria-label="Кодировка файла линий">
                  {ENCODINGS.map((e) => (
                    <option key={e} value={e}>
                      {e || "авто"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {table ? (
              <VertexColumns wizard={wizard} trench={t} />
            ) : (
              <fieldset className={css.group}>
                <legend>Имя канавы</legend>
                <label className={css.radio}>
                  <input type="radio" checked={t.nameFrom === "string"} onChange={() => void wizard.patchTrench({ nameFrom: "string" })} /> Из имени стринга (STRING, номер стринга Surpac, слой DXF)
                </label>
                <label className={css.radio}>
                  <input
                    type="radio"
                    checked={t.nameFrom === "field"}
                    disabled={!p?.fields.length && t.nameFrom !== "field"}
                    onChange={() => void wizard.patchTrench({ nameFrom: "field", nameField: t.nameField || p?.fields[0] || "" })}
                  />{" "}
                  Из поля
                  <select value={t.nameField} disabled={t.nameFrom !== "field"} onChange={(e) => void wizard.patchTrench({ nameField: e.target.value })} aria-label="Поле с именем канавы">
                    {!p?.fields.includes(t.nameField) && <option value={t.nameField}>{t.nameField || "—"}</option>}
                    {p?.fields.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                  {!p?.fields.length && <span className={css.note}>у линий файла нет полей</span>}
                </label>
              </fieldset>
            )}
            {!table && allLayers.length > 1 && (
              <fieldset className={css.group}>
                <legend>
                  Слои ({allLayers.filter(taken).length} из {allLayers.length})
                  <button type="button" className={css.linkBtn} onClick={() => void wizard.patchTrench({ layers: null })}>
                    все
                  </button>
                </legend>
                <div className={css.chips}>
                  {p!.layers.slice(0, 200).map((l) => (
                    <label key={l.name} title={`Линий: ${l.lines}`}>
                      <input type="checkbox" checked={taken(l.name)} onChange={(e) => setLayer(l.name, e.target.checked)} /> {l.name}
                    </label>
                  ))}
                  {allLayers.length > 200 && <span className={css.note}>… и ещё {allLayers.length - 200}</span>}
                </div>
              </fieldset>
            )}
            <label className={css.force}>
              <input type="checkbox" checked={t.reverseAll} onChange={(e) => void wizard.patchTrench({ reverseAll: e.target.checked })} /> Обратить направление всех канав (устье — последняя вершина линии)
            </label>
          </>
        )}
      </section>
      {p && (
        <section className={css.card} aria-label="Предпросмотр канав" data-testid="trench-preview">
          <h4>Предпросмотр</h4>
          <div className={p.trenches ? "" : css.error}>{trenchSummary(p)}</div>
          {p.warnings.length > 0 && (
            <ul className={css.issues}>
              {p.warnings.map((w, i) => (
                <li key={i} className={css.warn}>
                  {w}
                </li>
              ))}
            </ul>
          )}
          {p.preview.length > 0 && (
            <div className={css.previewWrap}>
              <table className={css.preview} aria-label="Канавы">
                <thead>
                  <tr>
                    <th>Канава</th>
                    <th>Вершин</th>
                    <th>Длина по 3D, м</th>
                    <th>Устье X</th>
                    <th>Устье Y</th>
                    <th>Устье Z</th>
                    <th>Обратить</th>
                  </tr>
                </thead>
                <tbody>
                  {p.preview.map((r) => (
                    <tr key={r.name}>
                      <td>{r.name}</td>
                      <td className={css.num}>{r.vertices.toLocaleString("ru-RU")}</td>
                      <td className={css.num}>{num(r.length)}</td>
                      <td className={css.num}>{num(r.x)}</td>
                      <td className={css.num}>{num(r.y)}</td>
                      <td className={css.num}>{num(r.z)}</td>
                      <td>
                        <input
                          type="checkbox"
                          checked={t.reverseAll || t.reversed.includes(r.name)}
                          disabled={t.reverseAll}
                          aria-label={`Обратить направление канавы ${r.name}`}
                          onChange={(e) => void wizard.patchTrench({ reversed: toggleReverse(t.reversed, r.name, e.target.checked) })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {p.trenches > p.preview.length && <div className={css.note}>Показаны первые {Math.min(PREVIEW_LIMIT, p.preview.length)} из {p.trenches.toLocaleString("ru-RU")} канав.</div>}
        </section>
      )}
    </>
  );
}

/** Columns of a table of vertices: the name of the trench and, when they are not found by their names, X, Y, Z.
 * Applied on Enter or leaving the field: every keystroke would read the file again. */
function VertexColumns({ wizard, trench }: { wizard: Wizard; trench: TrenchState }) {
  const [draft, setDraft] = useState({ name: trench.nameField, ...trench.columns });
  useEffect(() => setDraft({ name: trench.nameField, ...trench.columns }), [trench.nameField, trench.columns]);
  const fields = trench.preview?.fields ?? [];
  const apply = () => {
    const columns = { x: draft.x, y: draft.y, z: draft.z };
    const same = draft.name === trench.nameField && (["x", "y", "z"] as const).every((k) => columns[k] === trench.columns[k]);
    if (!same) void wizard.patchTrench({ nameField: draft.name, columns });
  };
  const field = (key: "name" | "x" | "y" | "z", label: string) => (
    <label>
      {label}
      <input
        value={draft[key]}
        list={key === "name" ? "trench-name-columns" : undefined}
        placeholder="авто"
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        onBlur={apply}
        onKeyDown={(e) => e.key === "Enter" && apply()}
        aria-label={`Колонка: ${label}`}
      />
    </label>
  );
  return (
    <fieldset className={css.group}>
      <legend>Колонки таблицы вершин</legend>
      <div className={css.options}>
        {field("name", "Имя канавы")}
        {field("x", "X")}
        {field("y", "Y")}
        {field("z", "Z")}
        <datalist id="trench-name-columns">
          {fields.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
      </div>
      <div className={css.note}>Пусто — колонка находится по названию (TRENCH, Канава, X, EAST, Z, RL…). Строки одной канавы идут в порядке файла.</div>
    </fieldset>
  );
}
