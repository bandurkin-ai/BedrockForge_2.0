import { useEffect, useMemo, useState } from "react";
import { blockMeta, type Dataset, type JobInfo } from "../api";
import { Icon } from "./icons";
import { LogButton } from "./LogButton";
import { createLog } from "./opLog";

const KINDS = ["wireframes", "lines", "blocks"] as const;
type ExportKind = (typeof KINDS)[number];
const TITLES: Record<ExportKind, string> = { wireframes: "Каркасы", lines: "Линии", blocks: "Блочные модели" };

/** Why a dataset cannot go into OMF v1, or null. Mirrors exporting/omf.py on the server. */
export function omfProblem(d: Dataset): string | null {
  if (d.kind === "wireframes") return (d.wireframes ?? []).length ? null : "в наборе нет каркасов";
  if (d.kind === "lines") return (d.lines ?? []).length ? null : "в наборе нет линий";
  if (d.kind === "blocks") {
    const g = blockMeta(d).grid;
    if (!g) return "нет описания сетки";
    if (g.subblocked) return "субблочная модель: в OMF v1 только регулярная сетка";
    if (g.rotation) return "модель повёрнута";
    return null;
  }
  return "этот вид объектов в OMF не записывается";
}

/** Datasets that can be exported, grouped by kind; checked by default. */
export function omfCandidates(datasets: Dataset[]): { kind: ExportKind; items: { d: Dataset; problem: string | null }[] }[] {
  return KINDS.map((kind) => ({
    kind,
    items: datasets.filter((d) => d.kind === kind).map((d) => ({ d, problem: omfProblem(d) })),
  })).filter((g) => g.items.length > 0);
}

/** Choice of datasets and the file name for an export to OMF v1 (Leapfrog: OMF 0.9). */
export function OmfExportDialog({ projectName, datasets, initial, onStart, onClose }: {
  projectName: string;
  datasets: Dataset[];
  /** preselected datasets (from the tree); all exportable ones when empty */
  initial?: number[];
  /** starts the job; the job (when returned) goes into the journal */
  onStart: (ids: number[], filename: string) => Promise<JobInfo | void>;
  onClose: () => void;
}) {
  const groups = useMemo(() => omfCandidates(datasets), [datasets]);
  const exportable = groups.flatMap((g) => g.items.filter((i) => !i.problem).map((i) => i.d.id));
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(initial?.length ? initial.filter((id) => exportable.includes(id)) : exportable));
  const [filename, setFilename] = useState(projectName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log] = useState(() => createLog("export", `Экспорт в OMF: ${projectName}`));
  const close = () => {
    log.close();
    onClose();
  };

  useEffect(() => {
    log.open({
      "наборы": groups.flatMap((g) => g.items.map(({ d, problem }) => ({ id: d.id, kind: d.kind, name: d.name, folder: d.folder ?? "", problem: problem ?? "можно экспортировать" }))),
      "отмечены заранее": initial ?? [],
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (id: number) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  async function run() {
    setBusy(true);
    setError(null);
    const ids = exportable.filter((id) => chosen.has(id));
    log.data("Запуск экспорта", { dataset_ids: ids, filename: filename.trim() });
    try {
      const job = await onStart(ids, filename.trim());
      if (job) log.attachJobs([job.id]);
      log.info("Экспорт запущен; файл — во вкладке «Задания»");
      close();
    } catch (e) {
      log.error("Экспорт не запущен", e);
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={close}>
      <div className="modal wizard" role="dialog" aria-label="Экспорт в OMF" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="export" size={16} />
          <strong>Экспорт в OMF</strong>
          <span className="muted small">OMF v1 — открывается в Leapfrog (OMF 0.9) и других ГГИС</span>
          <span className="spacer" />
          <LogButton log={log} />
          <button className="icon" onClick={close} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-body wizard-body">
          {error && <div className="alert error small">{error}</div>}
          {groups.length === 0 ? (
            <p className="muted small">В проекте нет каркасов, линий и блочных моделей.</p>
          ) : (
            <table className="wizard-table">
              <thead>
                <tr><th /><th>Набор</th><th>Состав</th></tr>
              </thead>
              <tbody>
                {groups.map((g) => [
                  <tr key={g.kind}><td colSpan={3}><strong className="small">{TITLES[g.kind]}</strong></td></tr>,
                  ...g.items.map(({ d, problem }) => (
                    <tr key={d.id} className={problem ? "skipped" : ""}>
                      <td>
                        <input type="checkbox" checked={!problem && chosen.has(d.id)} disabled={!!problem} onChange={() => toggle(d.id)}
                          aria-label={`Экспортировать «${d.name}»`} />
                      </td>
                      <td className="name" title={d.folder ? `${d.folder}/${d.name}` : d.name}>{d.folder ? `${d.folder}/` : ""}{d.name}</td>
                      <td className="small">
                        {problem ? <span className="error-text">{problem}</span>
                          : d.kind === "wireframes" ? `каркасов ${(d.wireframes ?? []).length}`
                          : d.kind === "lines" ? `слоёв ${(d.lines ?? []).length}`
                          : `блоков ${(blockMeta(d).block_count ?? 0).toLocaleString("ru-RU")}, сетка ${blockMeta(d).grid.counts.join(" × ")}`}
                      </td>
                    </tr>
                  )),
                ])}
              </tbody>
            </table>
          )}
          <div className="wizard-params">
            <label>
              Имя файла{" "}
              <input value={filename} onChange={(e) => setFilename(e.target.value)} placeholder={projectName} maxLength={200} aria-label="Имя файла OMF" />
              <span className="muted small"> .omf</span>
            </label>
            <span className="muted small">
              Каркасы → поверхности, слои линий → линии (атрибуты линий — на сегментах), регулярная блочная модель → объём с полной сеткой
              (ячейки без блоков пустые). Файл готовится фоновым заданием; ссылка на него — во вкладке «Задания», хранится 48 ч.
            </span>
          </div>
        </div>
        <footer className="modal-foot">
          <span className="muted small">Выбрано: {exportable.filter((id) => chosen.has(id)).length}</span>
          <span className="spacer" />
          <button onClick={close}>Отмена</button>
          <button className="primary" disabled={busy || exportable.every((id) => !chosen.has(id))} onClick={() => void run()}>
            {busy ? "Запуск…" : "Экспортировать"}
          </button>
        </footer>
      </div>
    </div>
  );
}
