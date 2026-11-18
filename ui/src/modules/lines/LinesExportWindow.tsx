/**
 * The window «Экспорт линий» (ТЗ 7.18, 9.3, 9.5, 9.6; ADR 0011 §3, §5): the format — Micromine STR, Surpac
 * STR, DXF, CSV, Micromine DAT — the layers by their files, the file («Сохранить как…») → the job
 * `export.lines`, seen in «Задания».
 */
import { useMemo, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { showJobs } from "../../shell/objectRun";
import { errorText, toggleId } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { sceneObjects, type LineFileInfo } from "../scene/objects";
import { startLinesExport } from "./actions";
import { askConfirm } from "../../shell/dialogs";
import { overwriteQuestion, startWithOverwrite } from "../rasters/export";
import { CSV_DEFAULTS, csvError, exportBody, exportLayers, exportName, exportReason, exportTotals, formatInfo, LINES_FORMATS, skippedNote, type CsvOptions, type LinesFormat } from "./export";
import { layerCounts, shownLayers } from "./tree";

export function LinesExportWindow({ winId, format: initial, layerIds }: { winId: string; format?: string; layerIds?: number[] }) {
  const app = useApp();
  const files = useStore(sceneObjects, (o) => o.files);
  const [format, setFormat] = useState<LinesFormat>(formatInfo(initial ?? "str").id);
  const [ids, setIds] = useState<number[]>(layerIds ?? []);
  const [csv, setCsv] = useState<CsvOptions>(CSV_DEFAULTS);
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = useMemo(() => new Set(ids), [ids]);
  const info = formatInfo(format);
  const reason = exportReason(files, ids, format, path) ?? (format === "csv" ? csvError(csv) : null);
  const skipped = skippedNote(exportLayers(files, ids, format).skipped, format);

  const choose = async () => {
    const p = await bridge()?.pickSave(exportName(files, ids, format), info.filter);
    if (p) setPath(p);
  };
  const setFile = (f: LineFileInfo, on: boolean) => setIds((cur) => f.layers.reduce((list, l) => toggleId(list, l.id, on), cur));
  const start = async () => {
    if (reason || !path) return;
    setBusy(true);
    try {
      const body = exportBody(files, ids, format, path, csv);
      // the format adds its extension and that file exists: ask before replacing it (409 `files_exist`)
      const started = await startWithOverwrite((overwrite) => startLinesExport(body, overwrite), (names) => askConfirm(overwriteQuestion(names)));
      if (!started) {
        setBusy(false);
        return;
      }
      showJobs(app);
      await refreshJobs();
      closeWindow(winId);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <div className={css.win}>
      <div className={css.winBody}>
        <label className={css.formRow}>
          <span>Формат</span>
          <select
            className={css.input}
            value={format}
            aria-label="Формат файла"
            onChange={(e) => {
              setFormat(e.target.value as LinesFormat);
              setPath(null); // another format — another extension: the file is chosen again
            }}
          >
            {LINES_FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.title} ({f.ext})
              </option>
            ))}
          </select>
        </label>
        <p className={css.note}>{info.note}</p>
        {format === "csv" && (
          <div className={css.formRow}>
            <span>Запись CSV</span>
            <span className={css.inline}>
              <select className={css.input} value={csv.delimiter} aria-label="Разделитель столбцов" onChange={(e) => setCsv((c) => ({ ...c, delimiter: e.target.value as CsvOptions["delimiter"] }))}>
                <option value=";">столбцы через «;»</option>
                <option value=",">столбцы через «,»</option>
                <option value={"\t"}>столбцы через табуляцию</option>
              </select>
              <select className={css.input} value={csv.decimal} aria-label="Десятичный знак" onChange={(e) => setCsv((c) => ({ ...c, decimal: e.target.value as CsvOptions["decimal"] }))}>
                <option value=",">десятичная запятая</option>
                <option value=".">десятичная точка</option>
              </select>
            </span>
          </div>
        )}
        <fieldset className={css.group}>
          <legend>Слои</legend>
          <div className={css.layers} data-testid="export-layers">
            {files.map((f) => {
              const all = f.layers.length > 0 && f.layers.every((l) => chosen.has(l.id));
              const { layers, rest } = shownLayers(f); // a DXF may hold thousands of layers
              return (
                <div key={f.id} style={{ display: "contents" }}>
                  <label className={`${css.check} ${css.file}`}>
                    <input type="checkbox" checked={all} disabled={!f.layers.length} aria-label={`Все слои файла «${f.name}»`} onChange={(e) => setFile(f, e.target.checked)} />
                    {f.name}
                    <span className={css.count}>{count(f.layers.length)}</span>
                  </label>
                  {layers.map((l) => (
                    <label key={l.id} className={`${css.check} ${css.layer}`} title={l.points_only && !info.points ? `${info.title} пишет только линии: слой точек не войдёт в файл` : undefined}>
                      <input type="checkbox" checked={chosen.has(l.id)} aria-label={`Слой «${l.name}»`} onChange={(e) => setIds((cur) => toggleId(cur, l.id, e.target.checked))} />
                      {l.name}
                      <span className={css.count}>{layerCounts(l)}</span>
                    </label>
                  ))}
                  {rest > 0 && <span className={`${css.note} ${css.layer}`}>… и ещё слоёв: {count(rest)} — отметьте файл целиком</span>}
                </div>
              );
            })}
          </div>
          <p className={css.note} data-testid="export-totals">
            {exportTotals(files, ids)}
          </p>
          {skipped && <p className={css.warn}>{skipped}</p>}
        </fieldset>
        <label className={css.formRow}>
          <span>Файл</span>
          <span className={css.inline}>
            <input className={css.input} readOnly value={path ?? ""} placeholder={exportName(files, ids, format)} aria-label="Файл экспорта" />
            <button type="button" className={css.btn} onClick={() => void choose()}>
              Сохранить как…
            </button>
          </span>
        </label>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
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
