/**
 * The composition of «Импорт файлов» for lines, points, rasters and wireframes (ТЗ 7.8, 7.18, 7.19, 7.21; ADR
 * 0011 §5; ADR 0013 §6): per file its format, layers (lines, vertices, points), wireframes (`WireframesPart`),
 * coordinate system, warnings and what is left out — a drawing shows both its layers and its wireframes; per
 * raster its size and georeference; the files that will not load, each with the reason; «Уровень растров по Z».
 * A point cloud (a LAS, or a table loaded as one) has its card (`CloudStep`); a table of points may be loaded as
 * a cloud by its check box; in the mode «Облако точек» every table is one (ADR 0014 §6).
 */
import { useEffect, useState } from "react";
import { useStore } from "../../shell/useStore";
import { BlocksStep } from "../blocks/import/BlocksStep";
import { CAN_FORCE, emptyDraft } from "../blocks/import/model";
import { fileCounts, takesEncoding, totalsText, type FileEntry, type FileOk, type FileOptions, type ObjectsWizard } from "./objects";
import { cloudCard, mayBeCloud, type CloudMode } from "./cloud";
import { CloudStep } from "./CloudStep";
import { leftOutText } from "./omf";
import { OmfPart } from "./OmfPart";
import { RasterZLevel } from "./RasterZLevel";
import { ConverterHelp, FileChoices, Preliminary, WireframesTable } from "./WireframesPart";
import css from "./ImportWizard.module.css";

const ENCODINGS = ["", "utf-8", "cp1251", "cp866", "koi8-r", "latin-1"];
const LAYERS_SHOWN = 12;
const n = (v: number) => v.toLocaleString("ru-RU");
const size = (bytes: number) =>
  bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ` : `${Math.max(1, Math.round(bytes / 1024)).toLocaleString("ru-RU")} КБ`;

const CLOUD_NOTE: Record<CloudMode, string> = {
  las: "Облака точек LAS 1.2–1.4 (форматы точек 0–10): X, Y, Z, класс и возврат. Сжатые LAZ не читаются — сохраните их как LAS. Большое облако можно проредить: «каждая k-я запись».",
  xyz: "Таблицы точек XYZ (XYZ, CSV, TXT, DAT) загружаются облаком: колонки X, Y, Z и класс (если есть) находятся по названиям, их можно выбрать. Большую таблицу можно проредить: «каждая k-я запись».",
};

export function ObjectsStep({ wizard, onAdd, onSettings }: { wizard: ObjectsWizard; onAdd: () => void; onSettings: () => void }) {
  const s = useStore(wizard.store, (x) => x);
  const c = s.composition;
  const ok = c?.files.filter((f): f is FileOk => f.ok) ?? [];
  return (
    <>
      {c?.files.map((f) => (
        <FileBlock key={f.path} wizard={wizard} file={f} options={s.options[f.path] ?? {}} cloudMode={!!s.cloud} onSettings={onSettings} />
      ))}
      {c && c.rest.length > 0 && (
        <section className={css.card} aria-label="Не загрузятся" data-testid="objects-rest">
          <h4>Не загрузятся</h4>
          <ul className={css.issues}>
            {c.rest.map((r) => (
              <li key={r.path} className={css.warn}>
                {r.file}: {r.message} {r.hint}{" "}
                <button type="button" className={css.linkBtn} onClick={() => wizard.remove(r.path)}>
                  Убрать
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {c && c.totals.rasters > 0 && <RasterZLevel level={s.level} files={ok} onChange={(patch) => wizard.setLevel(patch)} />}
      <button type="button" className={css.btn} onClick={onAdd}>
        Добавить файлы…
      </button>
      {s.busy && <div className={css.note}>Чтение файлов…</div>}
      {c ? (
        <div className={css.totals} data-testid="objects-totals">
          {totalsText(c.totals)}
        </div>
      ) : (
        !s.busy &&
        (s.cloud ? (
          <div className={css.note}>{CLOUD_NOTE[s.cloud]}</div>
        ) : (
          <div className={css.note}>
            Линии и точки: Micromine STR и PIT, Surpac STR, DXF, DWG, SHP (с .dbf, .shx, .prj, .cpg), MIF/MID, Datamine .dm, таблицы точек XYZ (CSV, TXT, DAT). Каркасы: Micromine TRIDB, DXF, DWG
            (через ODA File Converter), OBJ, STL, Surpac DTM с STR того же имени, Datamine (…tr.dm с …pt.dm). Растры: JPG, PNG, TIFF, BMP с файлом привязки того же имени (world-файл, TAB, GRF) или
            GeoTIFF. Облака точек: LAS. Блочные модели: Micromine DAT и CSV, Datamine .dm и .fbm (центр и размер блока находятся по названиям столбцов; таблицу с другими названиями отметьте «блочной моделью»). OMF v1 (Leapfrog OMF 0.9): поверхности — каркасы, линии — слои, регулярные объёмы — блочные модели; OMF 2 не читается.
          </div>
        ))
      )}
    </>
  );
}

export function FileBlock({ wizard, file, options, cloudMode, onSettings }: { wizard: ObjectsWizard; file: FileEntry; options: FileOptions; cloudMode: boolean; onSettings: () => void }) {
  const [all, setAll] = useState(false);
  const card = cloudCard(file);
  const points = file.ok ? file.format === "points" : file.error.code === "no_coordinates" || file.error.code === "no_column";
  const blocks = file.ok && file.kind === "block_model" && file.blocks ? { ...file, blocks: file.blocks } : null;
  // a table the core did not take for a block model may be loaded as one: the user names its columns
  const mayBeBlocks = !blocks && (file.ok ? file.format === "points" : CAN_FORCE.has(file.error.code));
  return (
    <section className={css.card} aria-label={`Файл ${file.file}`} data-testid="objects-file">
      <header className={css.cardHead}>
        <b>{file.file}</b>
        <span className={css.note}>
          {file.ok ? file.label : "не прочитан"} · {size(file.size)}
        </span>
        {file.ok && file.preliminary && <span className={css.tag}>предварительно</span>}
        <button type="button" className={css.linkBtn} onClick={() => wizard.remove(file.path)}>
          Убрать
        </button>
      </header>
      {!file.ok && (
        <div className={css.error} role="alert" data-testid="objects-file-error" data-code={file.error.code}>
          {file.error.message} {file.error.hint}
        </div>
      )}
      <ConverterHelp wizard={wizard} file={file} onSettings={onSettings} />
      {file.ok && <Preliminary file={file} />}
      {(takesEncoding(file) || (file.ok && Object.keys(file.choices ?? {}).length > 0)) && (
        <div className={css.options}>
          {takesEncoding(file) && (
            <label>
              Кодировка
              <select
                value={options.encoding ?? ""}
                onChange={(e) =>
                  void wizard.setOptions(file.path, {
                    encoding: e.target.value,
                  })
                }
                aria-label={`Кодировка ${file.file}`}
              >
                {ENCODINGS.map((e) => (
                  <option key={e} value={e}>
                    {e || `авто${file.ok && file.encoding ? ` (${file.encoding})` : ""}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          {file.ok && <FileChoices wizard={wizard} file={file} options={options} />}
          {file.ok && mayBeCloud(file) && !cloudMode && (
            <label>
              <input type="checkbox" checked={!!options.cloud} onChange={(e) => void wizard.setOptions(file.path, { cloud: e.target.checked })} aria-label={`Облаком точек ${file.file}`} />
              облаком точек
            </label>
          )}
          {mayBeBlocks && (
            <label>
              <input type="checkbox" checked={!!options.blocks} onChange={(e) => void wizard.setOptions(file.path, { blocks: e.target.checked ? emptyDraft() : null })} aria-label={`Блочной моделью ${file.file}`} />
              блочной моделью
            </label>
          )}
          {points && !options.blocks && <PointColumns options={options} onApply={(patch) => void wizard.setOptions(file.path, patch)} />}
        </div>
      )}
      {file.ok && card && <CloudStep wizard={wizard} file={file} card={card} options={options} />}
      {blocks && <BlocksStep wizard={wizard} file={blocks} options={options} />}
      {file.ok && !card && !blocks && file.kind !== "raster" && (
        <>
          <div>
            {fileCounts(file)}
            {" · "}
            {file.crs?.name ? `СК: ${file.crs.name}${file.crs.epsg ? ` (EPSG:${file.crs.epsg})` : ""}` : "СК не задана"}
            {file.sidecars.length > 0 && ` · с файлами: ${file.sidecars.join(", ")}`}
          </div>
          {file.kind === "omf" && <OmfPart file={file.file} info={file.omf} blocks={file.block_models ?? []} />}
          {file.layers.length > 0 && (
            <table className={css.layers} aria-label={`Слои ${file.file}`}>
              <thead>
                <tr>
                  <th>Слой</th>
                  <th>Линий</th>
                  <th>Вершин</th>
                  <th>Точек</th>
                  <th>Атрибуты</th>
                </tr>
              </thead>
              <tbody>
                {(all ? file.layers : file.layers.slice(0, LAYERS_SHOWN)).map((l) => (
                  <tr key={l.name}>
                    <td>{l.name}</td>
                    <td className={css.num}>{l.lines ? n(l.lines) : ""}</td>
                    <td className={css.num}>{l.vertices ? n(l.vertices) : ""}</td>
                    <td className={css.num}>{l.points ? n(l.points) : ""}</td>
                    <td className={css.note}>{l.attributes.map((a) => a.name).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {file.totals.layers > LAYERS_SHOWN && (
            <button type="button" className={css.linkBtn} style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)}>
              {all ? "Свернуть список слоёв" : `Все слои (${n(file.totals.layers)})`}
            </button>
          )}
          {all && file.totals.layers > file.layers.length && <div className={css.note}>Показаны первые {n(file.layers.length)} слоёв; загрузятся все.</div>}
          {file.kind !== "omf" && <WireframesTable file={file} />}
        </>
      )}
      {file.ok &&
        file.rasters.map((r) => (
          <div key={r.name}>
            Растр {n(r.width)} × {n(r.height)} px · привязка: {r.georef.source_label}
            {r.sidecars.length > 0 && ` (${r.sidecars.join(", ")})`}
            {r.georef.pixel ? ` · пиксель ${r.georef.pixel.toLocaleString("ru-RU", { maximumFractionDigits: 3 })} м` : ""}
            {r.vertical ? " · вертикальный" : ""}
          </div>
        ))}
      {file.ok && (file.warnings.length > 0 || file.left_out.length > 0) && (
        <ul className={css.issues} data-testid="objects-warnings">
          {file.left_out.map((x, i) => (
            <li key={`${x.key ?? ""}:${x.what}:${i}`} className={css.warn}>
              Не загрузится: {leftOutText(x)}
            </li>
          ))}
          {file.warnings.slice(0, 20).map((w, i) => (
            <li key={i} className={css.warn}>
              {w}
            </li>
          ))}
          {file.warnings.length > 20 && <li className={css.note}>… и ещё замечаний: {file.warnings.length - 20} (все — в журнале операции)</li>}
        </ul>
      )}
    </section>
  );
}

/** Columns of a table of points when they are not found by their names. Applied on Enter or leaving a field:
 * every keystroke would read the files again. */
function PointColumns({ options, onApply }: { options: FileOptions; onApply: (patch: FileOptions) => void }) {
  const [draft, setDraft] = useState({
    x: options.x ?? "",
    y: options.y ?? "",
    z: options.z ?? "",
  });
  useEffect(() => setDraft({ x: options.x ?? "", y: options.y ?? "", z: options.z ?? "" }), [options.x, options.y, options.z]);
  const apply = () => {
    if ((["x", "y", "z"] as const).some((k) => draft[k].trim() !== (options[k] ?? ""))) onApply({ x: draft.x.trim(), y: draft.y.trim(), z: draft.z.trim() });
  };
  return (
    <>
      {(["x", "y", "z"] as const).map((k) => (
        <label key={k}>
          Колонка {k.toUpperCase()}
          <input
            value={draft[k]}
            placeholder="авто"
            style={{ width: 90 }}
            onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            onBlur={apply}
            onKeyDown={(e) => e.key === "Enter" && apply()}
            aria-label={`Колонка ${k.toUpperCase()}`}
          />
        </label>
      ))}
    </>
  );
}
