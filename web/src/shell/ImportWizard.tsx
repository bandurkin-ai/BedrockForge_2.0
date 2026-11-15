import { useEffect, useMemo, useRef, useState } from "react";
import {
  deleteUpload, detectUploads, startImport, uploadResumable, type Dataset, type DetectedFile, type JobInfo, type VerticalPlacement,
} from "../api";
import { Icon } from "./icons";
import { formatBytes } from "./JobsPanel";
import type { UploadState } from "./useJobs";

export type ObjectKind = "wireframes" | "lines" | "rasters" | "blocks";

const KIND_TITLES: Record<ObjectKind, string> = { wireframes: "Каркасы", lines: "Линии", rasters: "Растры", blocks: "Блочная модель" };
const isObjectKind = (k: string): k is ObjectKind => k in KIND_TITLES;
const TABLE_SUFFIXES = [".dat", ".csv", ".txt", ".xlsx", ".xlsm", ".xls"];
const OBJECT_SUFFIXES = [".tridb", ".dxf", ".obj", ".stl", ".str", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp",
  ".jgw", ".jpgw", ".pgw", ".pngw", ".tfw", ".tifw", ".bpw", ".wld", ".tab", ".grf"];
export const WIZARD_ACCEPT = [...OBJECT_SUFFIXES, ...TABLE_SUFFIXES].join(",");

const suffix = (name: string) => {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
};

/**
 * Files sorted before sending: object files go up, tables go to the drillhole wizard. A table
 * that is a block model (`blockModels`, found by sniffBlocks) is an object file too.
 */
export function sortFiles(files: File[], blockModels: Set<File> = new Set()): { objects: File[]; tables: File[]; unknown: File[] } {
  const objects: File[] = [];
  const tables: File[] = [];
  const unknown: File[] = [];
  for (const f of files) {
    const s = suffix(f.name);
    if (OBJECT_SUFFIXES.includes(s) || blockModels.has(f)) objects.push(f);
    else if (TABLE_SUFFIXES.includes(s)) tables.push(f);
    else unknown.push(f);
  }
  return { objects, tables, unknown };
}

interface Row extends DetectedFile {
  chosen: ObjectKind | null;
}

/** Import jobs to start: rasters with their georeference files together, other files one by one. */
export function planJobs(rows: Row[]): { kind: ObjectKind; uploads: string[]; names: string[] }[] {
  const plan: { kind: ObjectKind; uploads: string[]; names: string[] }[] = [];
  const images = rows.filter((r) => r.chosen === "rasters" && r.kind === "rasters");
  // Georeference files go with the images that use them; a stray one would fail the job.
  const used = new Set(images.flatMap((r) => r.sidecars ?? []));
  const rasters = [...images, ...rows.filter((r) => r.kind === "sidecar" && used.has(r.filename))];
  if (images.length) plan.push({ kind: "rasters", uploads: rasters.map((r) => r.upload), names: rasters.map((r) => r.filename) });
  for (const r of rows) {
    if (r.chosen === "wireframes" || r.chosen === "lines" || r.chosen === "blocks") plan.push({ kind: r.chosen, uploads: [r.upload], names: [r.filename] });
  }
  return plan;
}

/**
 * The import wizard (stage 7.6): files → transfer → what is inside (format, kind, preview) →
 * parameters (kind, folder, placement of images without a georeference) → background jobs.
 */
export function ImportWizard({
  projectId, files, blockModels, kindHint, datasets, sectionFrame, trackUpload, updateUpload, onStarted, onTables, onClose,
}: {
  projectId: number;
  files: File[];
  /** Tables that are block models (sniffed from their header): imported as objects. */
  blockModels?: Set<File>;
  /** Opened from the "Каркасы" / "Линии" / "Растры" buttons: their kind is preselected. */
  kindHint: ObjectKind | null;
  datasets: Dataset[];
  sectionFrame: () => VerticalPlacement | null;
  trackUpload: (filename: string, total: number) => UploadState;
  updateUpload: (key: string, patch: Partial<UploadState>) => void;
  onStarted: (jobs: JobInfo[]) => void;
  onTables: (files: File[]) => void;
  onClose: () => void;
}) {
  const sorted = useMemo(() => sortFiles(files, blockModels), [files, blockModels]);
  const asBlocks = useMemo(() => new Set(files.filter((f) => blockModels?.has(f) || (kindHint === "blocks" && TABLE_SUFFIXES.includes(suffix(f.name)))).map((f) => f.name)),
    [files, blockModels, kindHint]);
  const [stage, setStage] = useState<"upload" | "review" | "starting">(sorted.objects.length ? "upload" : "review");
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [onSection, setOnSection] = useState(true);
  const aborts = useRef<AbortController[]>([]);
  const uploaded = useRef<string[]>([]);
  const started = useRef(false);
  const section = sectionFrame();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // React runs mount effects twice in development: the transfer must start once, and its
  // results go to whichever mount is current.
  const mounted = useRef(false);
  const transferStarted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Transfer, then ask the server what is inside.
  useEffect(() => {
    if (!sorted.objects.length || transferStarted.current) return;
    transferStarted.current = true;
    const alive = { get current() { return mounted.current; } };
    (async () => {
      try {
        const ids: string[] = [];
        for (const file of sorted.objects) {
          const state = trackUpload(file.name, file.size);
          aborts.current.push(state.abort);
          const info = await uploadResumable(projectId, file, (sent) => {
            updateUpload(state.key, { sent });
            if (alive.current) setProgress((p) => ({ ...p, [file.name]: sent }));
          }, state.abort.signal);
          updateUpload(state.key, { sent: file.size, status: "done" });
          ids.push(info.id);
        }
        const detected = await detectUploads(projectId, ids);
        uploaded.current = ids;
        if (!alive.current) return;
        setRows(detected.map((d) => ({
          ...d,
          // Sent as a block model (sniffed in the browser, or from the "Блочные модели" button):
          // it stays one even if the server did not recognise it; the import reports why.
          chosen: d.kind === "blocks" || (d.kind === "drillholes" && asBlocks.has(d.filename)) ? "blocks"
            : d.kind === "wireframes" || d.kind === "lines" || d.kind === "rasters"
            ? ((kindHint === "wireframes" || kindHint === "lines") && d.kind !== "rasters" && suffix(d.filename) === ".dxf" ? kindHint : d.kind)
            : d.kind === "sidecar" ? "rasters" : null,
        })));
        setStage("review");
      } catch (e) {
        if (alive.current) setError((e as Error).name === "AbortError" ? "Передача прервана" : (e as Error).message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function close() {
    aborts.current.forEach((a) => a.abort());
    // Files sent but not imported are removed now (otherwise the nightly cleanup does it).
    if (!started.current) uploaded.current.forEach((id) => void deleteUpload(id));
    onClose();
  }

  const plan = planJobs(rows);
  const hasUnplaced = rows.some((r) => r.chosen === "rasters" && r.kind === "rasters" && !(r.sidecars ?? []).length);
  const kindsInPlan = [...new Set(plan.map((p) => p.kind))];
  const folders = useMemo(() => {
    const set = new Set<string>();
    for (const d of datasets) if (d.folder && kindsInPlan.includes(d.kind as ObjectKind)) set.add(d.folder);
    return [...set].sort((a, b) => a.localeCompare(b, "ru"));
  }, [datasets, kindsInPlan.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    setStage("starting");
    setError(null);
    try {
      const jobs: JobInfo[] = [];
      for (const p of plan) {
        jobs.push(await startImport(projectId, {
          uploads: p.uploads, kind: p.kind, folder,
          section: p.kind === "rasters" && onSection ? section : null,
        }));
      }
      started.current = true;
      // Files left out of the import (unchecked, unknown) are not kept.
      const used = new Set(plan.flatMap((p) => p.uploads));
      rows.filter((r) => !used.has(r.upload)).forEach((r) => void deleteUpload(r.upload));
      onStarted(jobs);
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setStage("review");
    }
  }

  const setKind = (upload: string, chosen: ObjectKind | null) => setRows((prev) => prev.map((r) => (r.upload === upload ? { ...r, chosen } : r)));

  return (
    <div className="modal-backdrop" onMouseDown={close}>
      <div className="modal wizard" role="dialog" aria-label="Импорт файлов" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="import" size={16} />
          <strong>Импорт файлов</strong>
          <span className="muted small">
            {stage === "upload" ? "1 · передача" : stage === "review" ? "2 · проверка и параметры" : "3 · запуск"}
          </span>
          <span className="spacer" />
          <button className="icon" onClick={close} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-body wizard-body">
          {error && <div className="alert error small">{error}</div>}

          {stage === "upload" && (
            <table className="wizard-table">
              <tbody>
                {sorted.objects.map((f) => (
                  <tr key={f.name}>
                    <td className="name">{f.name}</td>
                    <td className="bar-cell">
                      <div className="job-bar"><div style={{ width: `${Math.round(((progress[f.name] ?? 0) / Math.max(f.size, 1)) * 100)}%` }} /></div>
                    </td>
                    <td className="muted small">{formatBytes(progress[f.name] ?? 0)} из {formatBytes(f.size)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {stage !== "upload" && (
            <>
              {rows.length > 0 && (
                <table className="wizard-table">
                  <thead>
                    <tr><th>Файл</th><th>Формат</th><th>Размер</th><th>Загрузить как</th><th>Содержимое</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.upload} className={r.chosen ? "" : "skipped"}>
                        <td className="name" title={r.filename}>{r.filename}</td>
                        <td>{r.format}</td>
                        <td className="num">{formatBytes(r.size)}</td>
                        <td>
                          {r.kind === "sidecar" ? (
                            <span className="muted small">привязка растра</span>
                          ) : r.kind === "drillholes" && asBlocks.has(r.filename) ? (
                            <select value={r.chosen ?? ""} onChange={(e) => setKind(r.upload, (e.target.value || null) as ObjectKind | null)} aria-label={`Тип: ${r.filename}`}>
                              <option value="blocks">{KIND_TITLES.blocks}</option>
                              <option value="">Не загружать</option>
                            </select>
                          ) : isObjectKind(r.kind) ? (
                            <select value={r.chosen ?? ""} onChange={(e) => setKind(r.upload, (e.target.value || null) as ObjectKind | null)} aria-label={`Тип: ${r.filename}`}>
                              {(suffix(r.filename) === ".dxf" ? (["wireframes", "lines"] as ObjectKind[]) : [r.kind]).map((k) => (
                                <option key={k} value={k}>{KIND_TITLES[k]}</option>
                              ))}
                              <option value="">Не загружать</option>
                            </select>
                          ) : (
                            <span className="muted small">—</span>
                          )}
                        </td>
                        <td className="small">
                          {r.preview?.error ? <span className="error-text">{r.preview.error}</span> : r.preview?.summary}
                          {r.note && <div className="muted">{r.note}</div>}
                          {r.preview?.items && r.preview.items.length > 0 && (
                            <details>
                              <summary className="muted">Состав ({r.preview.items.length})</summary>
                              <ul className="wizard-items">
                                {r.preview.items.slice(0, 200).map((it, i) => (
                                  <li key={i}>
                                    {it.name}
                                    {it.triangles != null && <span className="muted"> · {it.triangles.toLocaleString("ru-RU")} треуг.</span>}
                                    {it.entities && <span className="muted"> · {Object.entries(it.entities).map(([k, n]) => `${k} ${n}`).join(", ")}</span>}
                                  </li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {(sorted.tables.length > 0 || sorted.unknown.length > 0) && (
                <div className="wizard-note">
                  {sorted.tables.length > 0 && (
                    <p className="small">
                      Таблицы скважин ({sorted.tables.map((f) => f.name).join(", ")}) загружаются через мастер скважин: там назначаются колонки.{" "}
                      <button className="link" onClick={() => { onTables(sorted.tables); onClose(); }}>Открыть мастер скважин</button>
                    </p>
                  )}
                  {sorted.unknown.length > 0 && (
                    <p className="small muted">Не поддерживаются: {sorted.unknown.map((f) => f.name).join(", ")}</p>
                  )}
                </div>
              )}

              {plan.length > 0 && (
                <div className="wizard-params">
                  <label>
                    Папка в дереве{" "}
                    <input list="wizard-folders" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="в корень группы" maxLength={255} aria-label="Папка в дереве" />
                    <datalist id="wizard-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
                  </label>
                  {hasUnplaced && (
                    <label title={section ? "Изображения без файла привязки встанут в плоскость текущей линии разреза" : "Сначала постройте разрез"}>
                      <input type="checkbox" checked={onSection && !!section} disabled={!section} onChange={(e) => setOnSection(e.target.checked)} />{" "}
                      Изображения без привязки — на текущий разрез
                    </label>
                  )}
                  <span className="muted small">
                    Заданий: {plan.length} ({kindsInPlan.map((k) => KIND_TITLES[k]).join(", ")}). Ход — во вкладке «Задания».
                  </span>
                </div>
              )}
            </>
          )}
        </div>
        <footer className="modal-foot">
          <span className="spacer" />
          <button onClick={close}>Отмена</button>
          <button className="primary" disabled={stage !== "review" || plan.length === 0} onClick={() => void run()}>
            {stage === "starting" ? "Запуск…" : "Импортировать"}
          </button>
        </footer>
      </div>
    </div>
  );
}
