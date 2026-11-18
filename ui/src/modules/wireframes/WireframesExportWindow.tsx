/**
 * The window «Экспорт каркасов» (ТЗ 7.21, 9.4, 9.5, 9.12; ADR 0013 §2, §5): the format — Micromine TRIDB, DXF,
 * DWG, OBJ, STL, Surpac DTM — its options (the version of a DWG; a binary STL in float32 with a warning, or a
 * text one), the wireframes by the sections and folders of the tree, the file («Сохранить как…») → the job
 * `export.wireframes`, seen in «Задания». Files the export would replace are asked about in our own window
 * (409 `files_exist`); a DWG without ODA File Converter offers «Настройка › Внешние программы».
 */
import { useMemo, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import { showJobs } from "../../shell/objectRun";
import { errorText, toggleId } from "../../shell/props";
import { count, counted } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { overwriteQuestion, startWithOverwrite } from "../rasters/export";
import { sceneObjects } from "../scene/objects";
import {
  DWG_VERSIONS,
  exportBody,
  exportGroups,
  exportName,
  exportReason,
  exportTotals,
  odaMissing,
  OPTION_DEFAULTS,
  stlWarning,
  WIREFRAME_FORMATS,
  wireframeFormat,
  type DwgVersion,
  type ExportOptions,
  type WireframeFormat,
} from "./export";
import { startWireframesExport } from "./exportCommands";
import type { WireframeInfo } from "./state";

interface Props {
  winId: string;
  format?: string;
  wireframeIds?: number[];
  options?: Partial<ExportOptions>;
}

export function WireframesExportWindow({ winId, format: initial, wireframeIds, options: given }: Props) {
  const app = useApp();
  const list = useStore(sceneObjects, (o) => o.wireframes) as WireframeInfo[];
  const [format, setFormat] = useState<WireframeFormat>(wireframeFormat(initial ?? "tridb").id);
  const [ids, setIds] = useState<number[]>(wireframeIds ?? []);
  const [options, setOptions] = useState<ExportOptions>({ ...OPTION_DEFAULTS, ...given });
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; oda: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = useMemo(() => new Set(ids), [ids]);
  const groups = useMemo(() => exportGroups(list), [list]);
  const info = wireframeFormat(format);
  const reason = exportReason(list, ids, path);
  const name = exportName(list, ids, format);
  const float32 = format === "stl" ? stlWarning(list, ids, options.binary) : null;

  const choose = async () => {
    const p = await bridge()?.pickSave(name, info.filter);
    if (p) setPath(p);
  };
  const setGroup = (items: readonly WireframeInfo[], on: boolean) => setIds((cur) => items.reduce((all, w) => toggleId(all, w.id, on), cur));
  const start = async () => {
    if (reason || !path) return;
    setBusy(true);
    setError(null);
    try {
      const body = exportBody(list, ids, format, path, options);
      // the format adds its extension (a DTM — its .str too) and those files exist: ask before replacing them
      const started = await startWithOverwrite((overwrite) => startWireframesExport(body, overwrite), (files) => askConfirm(overwriteQuestion(files)));
      if (!started) {
        setBusy(false); // «Отмена»: the window stays, another name can be chosen
        return;
      }
      showJobs(app);
      await refreshJobs();
      closeWindow(winId);
    } catch (e) {
      setError({ text: errorText(e), oda: odaMissing(e) });
      setBusy(false);
    }
  };

  return (
    <div className={css.win} data-testid="wireframes-export">
      <div className={css.winBody}>
        <label className={css.formRow}>
          <span>Формат</span>
          <select
            className={css.input}
            value={format}
            aria-label="Формат файла"
            onChange={(e) => {
              setFormat(e.target.value as WireframeFormat);
              setPath(null); // another format — another extension: the file is chosen again
              setError(null);
            }}
          >
            {WIREFRAME_FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.title} ({f.id === "dtm" ? ".dtm + .str" : f.ext})
              </option>
            ))}
          </select>
        </label>
        <p className={css.note}>{info.note}</p>
        {format === "dwg" && (
          <label className={css.formRow}>
            <span>Версия DWG</span>
            <select className={css.input} value={options.version} aria-label="Версия DWG" onChange={(e) => setOptions((o) => ({ ...o, version: e.target.value as DwgVersion }))}>
              {DWG_VERSIONS.map((v) => (
                <option key={v} value={v}>
                  AutoCAD {v}
                </option>
              ))}
            </select>
          </label>
        )}
        {format === "stl" && (
          <label className={css.formRow}>
            <span>Запись STL</span>
            <select className={css.input} value={options.binary ? "binary" : "text"} aria-label="Вид STL" onChange={(e) => setOptions((o) => ({ ...o, binary: e.target.value === "binary" }))}>
              <option value="binary">двоичный (float32, файл меньше)</option>
              <option value="text">текстовый (координаты без потерь)</option>
            </select>
          </label>
        )}
        {float32 && <p className={css.warn}>{float32}</p>}
        <fieldset className={css.group}>
          <legend>Каркасы</legend>
          <div className={css.layers} data-testid="export-wireframes">
            {groups.map((g) => {
              const items = g.items as WireframeInfo[];
              const all = items.every((w) => chosen.has(w.id));
              return (
                <div key={g.key} style={{ display: "contents" }}>
                  <label className={`${css.check} ${css.file}`}>
                    <input type="checkbox" checked={all} aria-label={`Все каркасы: ${g.title}`} onChange={(e) => setGroup(items, e.target.checked)} />
                    {g.title}
                    <span className={css.count}>{count(items.length)}</span>
                  </label>
                  {items.map((w) => (
                    <label key={w.id} className={`${css.check} ${css.layer}`}>
                      <input type="checkbox" checked={chosen.has(w.id)} aria-label={`Каркас «${w.name}»`} onChange={(e) => setIds((cur) => toggleId(cur, w.id, e.target.checked))} />
                      {w.name}
                      <span className={css.count}>{counted(w.triangle_count, "треугольник", "треугольника", "треугольников")}</span>
                    </label>
                  ))}
                </div>
              );
            })}
          </div>
          <p className={css.note} data-testid="export-totals">
            {exportTotals(list, ids)}
          </p>
        </fieldset>
        <label className={css.formRow}>
          <span>Файл</span>
          <span className={css.inline}>
            <input className={css.input} readOnly value={path ?? ""} placeholder={name} aria-label="Файл экспорта" />
            <button type="button" className={css.btn} onClick={() => void choose()}>
              Сохранить как…
            </button>
          </span>
        </label>
        {error && (
          <div className={css.inline}>
            <p className={`${css.error} ${css.grow}`} role="alert">
              {error.text}
            </p>
            {error.oda && (
              <button type="button" className={css.btn} onClick={() => void app.run("settings.open", { tab: "tools" })}>
                Внешние программы…
              </button>
            )}
          </div>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{reason ?? "Экспорт идёт как задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={reason !== null || busy} onClick={() => void start()}>
          Экспортировать
        </button>
      </div>
    </div>
  );
}
