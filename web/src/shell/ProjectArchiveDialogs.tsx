/**
 * Moving a whole project to another computer: export into one .bfggis file and import of such a file
 * (api/app/routers/archives.py). Both run as background jobs; the dialogs follow them to the end.
 */
import { useEffect, useRef, useState } from "react";
import {
  cancelJob, deleteUpload, getArchiveSummary, inspectArchive, jobDownloadUrl, startProjectExport, startProjectImport,
  subscribeJob, uploadResumable,
  type ArchiveImportMode, type ArchiveInfo, type ArchiveSummary, type JobInfo, type ProjectImportResult, type UploadInfo,
} from "../api";
import { askConfirm } from "./dialogs";
import { Icon } from "./icons";
import { LogButton } from "./LogButton";
import { createLog } from "./opLog";

const fmt = (n: number) => n.toLocaleString("ru-RU");
const date = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export function bytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  const units = ["КБ", "МБ", "ГБ", "ТБ"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`;
}

/** Rows of the archive's tables shown to the user, in the order of the project panel. */
const CONTENT: [string, string[]][] = [
  ["Наборы и объекты дерева", ["dataset"]],
  ["Скважины (устья)", ["collar"]],
  ["Замеры инклинометрии", ["survey"]],
  ["Пробы", ["assay"]],
  ["Интервальные таблицы / строки", ["interval_table", "interval_row"]],
  ["Каркасы", ["wireframe"]],
  ["Слои линий / версии", ["line_layer", "line_layer_version"]],
  ["Растры", ["raster"]],
  ["Графики", ["chart"]],
  ["Стили", ["style"]],
  ["Аудит: замечания", ["audit_finding"]],
  ["Журнал правок", ["data_edit"]],
  ["Диалоги Claude", ["assistant_chat"]],
];

function Contents({ tables, files, fileBytes }: { tables: Record<string, number>; files: number; fileBytes: number }) {
  return (
    <table className="wizard-table pa-contents">
      <tbody>
        {CONTENT.map(([title, keys]) => {
          const values = keys.map((k) => tables[k] ?? 0);
          return (
            <tr key={title} className={values.some(Boolean) ? "" : "skipped"}>
              <td>{title}</td>
              <td className="num">{values.map(fmt).join(" / ")}</td>
            </tr>
          );
        })}
        <tr><td>Файлы хранилища (геометрия, изображения, исходные файлы)</td><td className="num">{fmt(files)} · {bytes(fileBytes)}</td></tr>
      </tbody>
    </table>
  );
}

function Bar({ value, state = "running" }: { value: number; state?: string }) {
  return (
    <div className={`job-bar ${state}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <div style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
    </div>
  );
}

/** Follows a job to its end with server-sent events. */
function useJob(initial: JobInfo | null): JobInfo | null {
  const [job, setJob] = useState<JobInfo | null>(initial);
  const id = initial?.id;
  useEffect(() => {
    setJob(initial);
    if (id === undefined) return;
    return subscribeJob(id, setJob);
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  return job;
}

const today = () => new Date().toISOString().slice(0, 10);

// ---- export

export function ProjectExportDialog({ project, browserState, onClose }: {
  project: { id: number; name: string };
  /** view settings of this browser; null: not available (a project that is not open) */
  browserState: Record<string, unknown> | null;
  onClose: () => void;
}) {
  const [summary, setSummary] = useState<ArchiveSummary | null>(null);
  const [filename, setFilename] = useState(`${project.name}_${today()}`);
  const [usePassword, setUsePassword] = useState(false);
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [withView, setWithView] = useState(browserState !== null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<JobInfo | null>(null);
  const job = useJob(started);
  const [log] = useState(() => createLog("export", `Экспорт проекта: ${project.name}`));

  useEffect(() => {
    log.open({ project_id: project.id, name: project.name });
    getArchiveSummary(project.id).then(setSummary).catch((e: Error) => setError(e.message));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (job?.status === "done") log.info(`Готово: ${bytes((job.result as { bytes?: number } | null)?.bytes ?? 0)}`);
    if (job?.status === "failed") log.error("Экспорт не удался", new Error(job.error ?? ""));
  }, [job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    log.close();
    onClose();
  };
  const running = !!job && (job.status === "queued" || job.status === "running");
  const passwordProblem = usePassword && (password.length < 4 ? "Пароль — не короче 4 символов" : password !== password2 ? "Пароли не совпадают" : null);

  async function run() {
    setError(null);
    try {
      const j = await startProjectExport(project.id, {
        filename: filename.trim(), password: usePassword ? password : undefined, browser: withView ? browserState : null,
      });
      log.attachJobs([j.id]);
      log.data("Запуск", { filename: filename.trim(), encrypted: usePassword, view: withView });
      setStarted(j);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const result = job?.result as { bytes?: number; filename?: string; missing_files?: number } | null;
  return (
    <div className="modal-backdrop" onMouseDown={running ? undefined : close}>
      <div className="modal wizard pa-dialog" role="dialog" aria-label="Экспорт проекта" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="export" size={16} />
          <strong>Экспорт проекта</strong>
          <span className="muted small">весь проект одним файлом .bfggis — для работы на другом компьютере</span>
          <span className="spacer" />
          <LogButton log={log} />
          <button className="icon" onClick={close} title="Закрыть" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-body wizard-body">
          {error && <div className="alert error small">{error}</div>}
          <div><strong>{project.name}</strong>{summary?.description && <span className="muted small"> — {summary.description}</span>}</div>
          {summary ? <Contents tables={summary.tables} files={summary.files} fileBytes={summary.file_bytes} /> : <span className="muted small">Считаю состав…</span>}
          {!started && (
            <div className="wizard-params pa-params">
              <label>
                Имя файла <input value={filename} onChange={(e) => setFilename(e.target.value)} maxLength={200} aria-label="Имя файла" />
                <span className="muted small">.bfggis</span>
              </label>
              <label title={browserState ? "" : "Доступно только для открытого проекта"}>
                <input type="checkbox" checked={withView} disabled={!browserState} onChange={(e) => setWithView(e.target.checked)} />
                Настройки вида (видимость слоёв, стили устьев и стволов, порядок разделов дерева)
              </label>
              <label>
                <input type="checkbox" checked={usePassword} onChange={(e) => setUsePassword(e.target.checked)} /> Защитить паролем (AES-256)
              </label>
              {usePassword && (
                <span className="pa-password">
                  <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="пароль" aria-label="Пароль" autoComplete="new-password" />
                  <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} placeholder="повтор пароля" aria-label="Повтор пароля" autoComplete="new-password" />
                  {passwordProblem && <span className="error-text small">{passwordProblem}</span>}
                  <span className="muted small">Пароль нигде не сохраняется: без него файл не открыть.</span>
                </span>
              )}
            </div>
          )}
          {job && (
            <div className="pa-progress">
              <div className="small">{job.status === "done" ? "Файл готов" : job.status === "failed" ? "Ошибка" : job.status === "cancelled" ? "Отменено" : job.message || "В очереди"}</div>
              <Bar value={job.status === "done" ? 1 : job.progress} state={job.status} />
              {job.status === "failed" && <div className="alert error small">{job.error}</div>}
              {job.status === "done" && (
                <div className="pa-done">
                  <a className="button primary" href={jobDownloadUrl(job.id)} download={result?.filename ?? true}>
                    <Icon name="export" size={14} /> Скачать {result?.filename} ({bytes(result?.bytes ?? 0)})
                  </a>
                  <span className="muted small">Ссылка хранится 48 ч и есть во вкладке «Задания». Прерванное скачивание браузер продолжит с места остановки.</span>
                  {!!result?.missing_files && <span className="error-text small">Не найдено файлов хранилища: {result.missing_files}</span>}
                </div>
              )}
            </div>
          )}
        </div>
        <footer className="modal-foot">
          <span className="muted small">{summary ? `Данные: примерно ${bytes(summary.file_bytes)} файлов + таблицы; файл будет сжат` : ""}</span>
          <span className="spacer" />
          {running && <button onClick={() => job && void cancelJob(job.id)}>Отменить экспорт</button>}
          <button onClick={close}>{started ? "Закрыть" : "Отмена"}</button>
          {!started && (
            <button className="primary" disabled={!summary || !!passwordProblem || !filename.trim()} onClick={() => void run()}>Экспортировать</button>
          )}
        </footer>
      </div>
    </div>
  );
}

// ---- import

type Step = "file" | "upload" | "review" | "import";

export function ProjectImportDialog({ initialFile, onOpenProject, onImported, onClose }: {
  initialFile?: File | null;
  /** opens the imported project; `applyView`: put its view settings into this browser first */
  onOpenProject: (result: ProjectImportResult, applyView: boolean) => void;
  /** the project list changed (a project was added or replaced) */
  onImported: (result: ProjectImportResult) => void;
  onClose: () => void;
}) {
  const [step, setStep] = useState<Step>("file");
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState(0);
  const [upload, setUpload] = useState<UploadInfo | null>(null);
  const [info, setInfo] = useState<ArchiveInfo | null>(null);
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<ArchiveImportMode>("copy");
  const [name, setName] = useState("");
  const [applyView, setApplyView] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<JobInfo | null>(null);
  const job = useJob(started);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [log] = useState(() => createLog("import", "Импорт проекта"));
  const reported = useRef(false);

  useEffect(() => {
    log.open({});
    if (initialFile) void choose(initialFile);
    return () => abort.current?.abort();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const result = job?.status === "done" ? (job.result as unknown as ProjectImportResult) : null;
  useEffect(() => {
    if (result && !reported.current) {
      reported.current = true;
      log.info(`Проект «${result.name}» создан`);
      onImported(result);
    }
    if (job?.status === "failed") log.error("Импорт не удался", new Error(job.error ?? ""));
  }, [job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    abort.current?.abort();
    // An upload that was not imported is not needed any more.
    if (upload && !started) void deleteUpload(upload.id);
    log.close();
    onClose();
  };

  async function choose(f: File) {
    setError(null);
    if (!f.name.toLowerCase().endsWith(".bfggis")) {
      setError("Выберите файл проекта BedrockForge с расширением .bfggis");
      return;
    }
    setFile(f);
    setStep("upload");
    setSent(0);
    log.data("Файл", { name: f.name, size: f.size });
    const controller = new AbortController();
    abort.current = controller;
    try {
      const up = await uploadResumable(null, f, (s) => setSent(s), controller.signal);
      setUpload(up);
      await check(up, "");
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      log.error("Загрузка не удалась", e);
      setError((e as Error).message);
      setStep("file");
    }
  }

  async function check(up: UploadInfo, pass: string) {
    setBusy(true);
    setError(null);
    try {
      const i = await inspectArchive(up.id, pass);
      setInfo(i);
      if (i.password_ok && i.project) {
        setName(i.name_taken ? i.free_name ?? i.project.name : i.project.name);
        setMode(i.name_taken ? "copy" : "rename");
        log.data("Содержимое файла", i);
      } else if (i.error) {
        setError(i.error);
      }
      setStep("review");
    } catch (e) {
      setError((e as Error).message);
      setStep("file");
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (!upload || !info?.project) return;
    if (mode === "replace" && !(await askConfirm({
      title: "Заменить проект",
      message: `Проект «${info.project.name}» на этом компьютере будет удалён и заменён проектом из файла. Отменить это нельзя.`,
      ok: "Заменить", danger: true,
    }))) return;
    setError(null);
    try {
      const j = await startProjectImport({
        upload_id: upload.id, password: info.encrypted ? password : undefined, mode,
        name: mode === "replace" ? info.project.name : name.trim(),
      });
      log.attachJobs([j.id]);
      setStarted(j);
      setStep("import");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const running = step === "upload" || (!!job && (job.status === "queued" || job.status === "running"));
  const locked = info && !info.password_ok;
  const nameProblem = mode === "rename" && !name.trim() ? "Введите название" : null;
  return (
    <div className="modal-backdrop" onMouseDown={running ? undefined : close}>
      <div className="modal wizard pa-dialog" role="dialog" aria-label="Импорт проекта" onMouseDown={(e) => e.stopPropagation()}
        onDragOver={(e) => step === "file" && e.preventDefault()}
        onDrop={(e) => {
          if (step !== "file") return;
          e.preventDefault();
          const f = e.dataTransfer.files[0];
          if (f) void choose(f);
        }}>
        <header className="modal-head">
          <Icon name="import" size={16} />
          <strong>Импорт проекта</strong>
          <span className="muted small">из файла .bfggis, сделанного экспортом проекта</span>
          <span className="spacer" />
          <LogButton log={log} />
          <button className="icon" onClick={close} title="Закрыть" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-body wizard-body">
          <ol className="pa-steps small">
            {(["Файл", "Проверка", "Импорт"] as const).map((t, i) => (
              <li key={t} className={i === ({ file: 0, upload: 0, review: 1, import: 2 } as const)[step] ? "active" : ""}>{t}</li>
            ))}
          </ol>
          {error && <div className="alert error small">{error}</div>}

          {step === "file" && (
            <div className="pa-drop" onClick={() => input.current?.click()} role="button" tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && input.current?.click()}>
              <Icon name="import" size={28} />
              <div>Перетащите сюда файл <b>.bfggis</b> или щёлкните, чтобы выбрать</div>
              <div className="muted small">Файл любого размера загружается частями с проверкой контрольных сумм; при обрыве связи загрузка продолжится с места остановки.</div>
              <input ref={input} type="file" accept=".bfggis" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void choose(f);
              }} />
            </div>
          )}

          {step === "upload" && file && (
            <div className="pa-progress">
              <div className="small">Загрузка «{file.name}»: {bytes(sent)} из {bytes(file.size)}</div>
              <Bar value={sent / Math.max(1, file.size)} />
              {sent >= file.size && <span className="muted small">Проверяю файл…</span>}
            </div>
          )}

          {step === "review" && info && locked && (
            <div className="wizard-params pa-params">
              <span>Файл «{info.filename}» ({bytes(info.size)}) защищён паролем.</span>
              <label>Пароль <input type="password" value={password} autoFocus onChange={(e) => setPassword(e.target.value)} aria-label="Пароль файла"
                onKeyDown={(e) => e.key === "Enter" && upload && password && void check(upload, password)} /></label>
              <button disabled={!password || busy} onClick={() => upload && void check(upload, password)}>{busy ? "Проверка…" : "Открыть"}</button>
            </div>
          )}

          {(step === "review" || step === "import") && info?.password_ok && info.project && (
            <>
              <div className="pa-source">
                <div><strong>{info.project.name}</strong>{info.encrypted && <span className="pp-badge">с паролем</span>}</div>
                {info.project.description && <div className="muted small">{info.project.description}</div>}
                <div className="muted small">
                  Экспортирован {date(info.exported_at)} · BedrockForge {info.app_version} · схема {info.schema_revision} · файл {bytes(info.size)}
                </div>
              </div>
              <Contents tables={info.tables ?? {}} files={info.files ?? 0} fileBytes={info.file_bytes ?? 0} />
            </>
          )}

          {step === "review" && info?.password_ok && info.project && (
            <div className="wizard-params pa-params pa-modes">
              {info.name_taken ? (
                <>
                  <span className="pa-warn small">Проект «{info.project.name}» уже есть на этом компьютере. Что сделать?</span>
                  <label><input type="radio" checked={mode === "copy"} onChange={() => { setMode("copy"); setName(info.free_name ?? ""); }} />
                    Создать копию «{info.free_name}»</label>
                  <label><input type="radio" checked={mode === "rename"} onChange={() => setMode("rename")} />
                    Другое название <input value={mode === "rename" ? name : ""} disabled={mode !== "rename"} onChange={(e) => setName(e.target.value)} maxLength={200} aria-label="Название проекта" /></label>
                  <label><input type="radio" checked={mode === "replace"} onChange={() => setMode("replace")} />
                    <span className="error-text">Заменить существующий</span> (он будет удалён после успешного импорта)</label>
                </>
              ) : (
                <label>Название проекта <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} aria-label="Название проекта" /></label>
              )}
              {info.browser && (
                <label><input type="checkbox" checked={applyView} onChange={(e) => setApplyView(e.target.checked)} /> Применить настройки вида из файла</label>
              )}
            </div>
          )}

          {step === "import" && job && (
            <div className="pa-progress">
              <div className="small">{job.status === "done" ? `Проект «${result?.name}» готов` : job.status === "failed" ? "Ошибка импорта" : job.message || "В очереди"}</div>
              <Bar value={job.status === "done" ? 1 : job.progress} state={job.status} />
              {job.status === "failed" && <div className="alert error small">{job.error}</div>}
            </div>
          )}
        </div>
        <footer className="modal-foot">
          <span className="muted small">{step === "review" && info?.password_ok ? "Все данные останутся и в файле: импорт его не меняет" : ""}</span>
          <span className="spacer" />
          {step === "upload" && <button onClick={() => { abort.current?.abort(); setStep("file"); }}>Остановить загрузку</button>}
          {step === "import" && running && <button onClick={() => job && void cancelJob(job.id)}>Отменить импорт</button>}
          <button onClick={close}>{result ? "Закрыть" : "Отмена"}</button>
          {step === "review" && info?.password_ok && (
            <button className="primary" disabled={!!nameProblem} onClick={() => void run()} title={nameProblem ?? ""}>Импортировать</button>
          )}
          {result && (
            <button className="primary" onClick={() => {
              log.close();
              onOpenProject(result, applyView && !!info?.browser);
            }}>Открыть проект</button>
          )}
        </footer>
      </div>
    </div>
  );
}
