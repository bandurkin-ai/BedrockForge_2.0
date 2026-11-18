/**
 * The window «Экспорт растра» (ТЗ 7.19, 9.7; ADR 0011 §3, §5): the raster, the format — the source file with
 * its georeference next to it, or one GeoTIFF — the file («Сохранить как…») → the job `export.raster`.
 * Files next to the chosen one that the export would replace are asked about in our own window first.
 */
import { useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { showJobs } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { askConfirm } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { sceneObjects } from "../scene/objects";
import { startRasterExport } from "./actions";
import { exportFilter, exportName, exportNotes, exportReason, overwriteQuestion, RASTER_FORMATS, startWithOverwrite, type RasterFormat } from "./export";
import type { RasterRow } from "./props";
import { rasterPlace } from "./tree";

export function RasterExportWindow({ winId, rasterId, format: initial }: { winId: string; rasterId?: number; format?: RasterFormat }) {
  const app = useApp();
  const rasters: readonly RasterRow[] = useStore(sceneObjects, (o) => o.rasters);
  const [id, setId] = useState<number | null>(rasterId ?? rasters[0]?.id ?? null);
  const [format, setFormat] = useState<RasterFormat>(initial === "geotiff" ? "geotiff" : "source");
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const raster = rasters.find((r) => r.id === id);
  const reason = exportReason(raster, format, path);
  const name = raster ? exportName(raster, format) : "";

  const choose = async () => {
    if (!raster) return;
    const p = await bridge()?.pickSave(name, exportFilter(raster, format));
    if (p) setPath(p);
  };
  const start = async () => {
    if (reason || !path || !raster) return;
    setBusy(true);
    setError(null);
    try {
      const started = await startWithOverwrite(
        (overwrite) => startRasterExport(raster.id, path, format, overwrite),
        (files) => askConfirm(overwriteQuestion(files)),
      );
      if (!started) {
        setBusy(false); // «Отмена»: the window stays, another name can be chosen
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
          <span>Растр</span>
          <select
            className={css.input}
            value={id ?? ""}
            aria-label="Растр"
            onChange={(e) => {
              setId(Number(e.target.value));
              setPath(null);
            }}
          >
            {rasters.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} — {rasterPlace(r)}
              </option>
            ))}
          </select>
        </label>
        <fieldset className={css.group}>
          <legend>Формат</legend>
          {RASTER_FORMATS.map((f) => (
            <label key={f.id} className={css.check} title={f.note}>
              <input
                type="radio"
                name={`raster-format-${winId}`}
                checked={format === f.id}
                onChange={() => {
                  setFormat(f.id);
                  setPath(null); // another format — another extension: the file is chosen again
                }}
              />
              {f.title}
            </label>
          ))}
          <p className={css.note}>{RASTER_FORMATS.find((f) => f.id === format)!.note}</p>
          {raster &&
            exportNotes(raster, format).map((n) => (
              <p key={n} className={css.warn}>
                {n}
              </p>
            ))}
        </fieldset>
        <label className={css.formRow}>
          <span>Файл</span>
          <span className={css.inline}>
            <input className={css.input} readOnly value={path ?? ""} placeholder={name} aria-label="Файл экспорта" />
            <button type="button" className={css.btn} disabled={!raster} onClick={() => void choose()}>
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
