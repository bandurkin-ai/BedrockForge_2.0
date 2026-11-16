import { useEffect, useMemo, useState } from "react";
import { getProjectStats, type Project, type ProjectStats } from "../api";
import { askText } from "./dialogs";
import { Icon } from "./icons";

const fmt = (n: number) => n.toLocaleString("ru-RU");
const date = (iso: string | undefined) =>
  iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

const objects = (s: ProjectStats) => s.datasets.wireframes + s.datasets.lines + s.datasets.rasters + s.datasets.blocks;

/**
 * Project management: the open project (name, description, what it holds) and every project of the
 * installation with its size and last change — open, rename, delete, create.
 */
export function ProjectsPanel({ projects, currentId, refreshKey, onOpen, onRename, onDescribe, onDelete, onNew, onDemo, onExport, onImport }: {
  projects: Project[];
  currentId: number;
  refreshKey: number;
  onOpen: (id: number) => void;
  onRename: (id: number, name: string) => Promise<unknown>;
  onDescribe: (id: number, description: string) => Promise<unknown>;
  onDelete: (id: number) => Promise<unknown>;
  onNew: () => Promise<unknown>;
  onDemo: () => Promise<unknown>;
  /** the export window of a project (a .bfggis file for another computer) */
  onExport: (project: Project) => void;
  /** the import window of a .bfggis file */
  onImport: () => void;
}) {
  const [stats, setStats] = useState<Record<number, ProjectStats>>({});
  const [query, setQuery] = useState("");
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const current = projects.find((p) => p.id === currentId);
  const [description, setDescription] = useState(current?.description ?? "");
  useEffect(() => setDescription(current?.description ?? ""), [current?.id, current?.description]);

  const ids = projects.map((p) => p.id).join(",");
  useEffect(() => {
    let cancelled = false;
    for (const p of projects) {
      getProjectStats(p.id).then((s) => !cancelled && setStats((prev) => ({ ...prev, [p.id]: s }))).catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [ids, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (p: Promise<unknown>, ok?: string) =>
    void p.then((r) => setNote({ text: typeof r === "string" ? r : ok ?? "Готово" })).catch((e: Error) => setNote({ text: e.message, error: true }));

  const rename = async (p: Project) => {
    const name = await askText({ title: "Переименовать проект", message: `Новое название проекта «${p.name}»`, value: p.name });
    if (name && name !== p.name) run(onRename(p.id, name), `Проект переименован: «${name}»`);
  };

  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    return projects.filter((p) => !q || p.name.toUpperCase().includes(q) || (p.description ?? "").toUpperCase().includes(q))
      .sort((a, b) => (a.id === currentId ? -1 : b.id === currentId ? 1 : (stats[b.id]?.updated_at ?? b.created_at).localeCompare(stats[a.id]?.updated_at ?? a.created_at)));
  }, [projects, query, stats, currentId]);

  const s = current ? stats[current.id] : undefined;
  return (
    <div className="projects-panel">
      {current && (
        <section className="pp-card">
          <div className="pp-head">
            <Icon name="folder" size={18} />
            <strong className="pp-name grow" title={current.name}>{current.name}</strong>
            <button onClick={() => void rename(current)} title="Переименовать открытый проект"><Icon name="edit" size={14} /> Переименовать</button>
            <button onClick={() => onExport(current)} title="Весь проект одним файлом .bfggis — для работы на другом компьютере"><Icon name="export" size={14} /> Экспорт</button>
          </div>
          <div className="muted small">Создан {date(current.created_at)} · изменён {date(s?.updated_at)}</div>
          <label className="pp-desc">
            <span className="small">Описание</span>
            <textarea value={description} rows={2} maxLength={4000} placeholder="Месторождение, лицензия, стадия работ, ответственный…" aria-label="Описание проекта"
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => description.trim() !== (current.description ?? "") && run(onDescribe(current.id, description), "Описание сохранено")} />
          </label>
          {s ? (
            <div className="pp-stats">
              {([
                ["Наборы скважин", s.datasets.drillholes], ["Скважины", s.holes], ["Замеры", s.surveys], ["Пробы", s.assays],
                ["Интервалы таблиц", s.interval_rows], ["Каркасы", s.wireframes], ["Линии", s.datasets.lines], ["Растры", s.datasets.rasters],
                ["Блочные модели", s.datasets.blocks], ["Графики", s.charts], ["Диалоги Claude", s.chats],
              ] as [string, number][]).map(([title, n]) => (
                <div key={title} className={n ? "" : "zero"}><b>{fmt(n)}</b><span className="muted small">{title}</span></div>
              ))}
            </div>
          ) : <span className="muted small">Считаю состав…</span>}
        </section>
      )}

      <section className="pp-card">
        <div className="pp-head">
          <strong className="grow">Все проекты ({projects.length})</strong>
          <button className="primary" onClick={() => run(onNew())}><Icon name="plus" size={14} /> Новый проект</button>
          <button onClick={onImport} title="Открыть проект из файла .bfggis, сделанного экспортом проекта"><Icon name="import" size={14} /> Импорт…</button>
          <button onClick={() => run(onDemo())} title="Учебный проект: 48 скважин, пробы, аудит, каркасы">Демо-проект</button>
        </div>
        <input value={query} placeholder="поиск по названию и описанию" aria-label="Поиск проекта" onChange={(e) => setQuery(e.target.value)} />
        <table className="pp-table">
          <thead><tr><th>Проект</th><th className="num">Скважин</th><th className="num">Объектов</th><th>Изменён</th><th /></tr></thead>
          <tbody>
            {shown.map((p) => {
              const st = stats[p.id];
              const open = p.id === currentId;
              return (
                <tr key={p.id} className={open ? "current" : ""} onDoubleClick={() => !open && onOpen(p.id)}>
                  <td>
                    <div className="pp-row-name">{p.name}{open && <span className="pp-badge">открыт</span>}</div>
                    {p.description && <div className="muted small pp-row-desc" title={p.description}>{p.description}</div>}
                  </td>
                  <td className="num">{st ? fmt(st.holes) : "…"}</td>
                  <td className="num" title={st ? `каркасы ${st.wireframes}, линии ${st.datasets.lines}, растры ${st.datasets.rasters}, блочные модели ${st.datasets.blocks}` : ""}>{st ? fmt(objects(st)) : "…"}</td>
                  <td className="small">{date(st?.updated_at ?? p.created_at)}</td>
                  <td className="pp-actions">
                    <button disabled={open} onClick={() => onOpen(p.id)} title={open ? "Этот проект уже открыт" : "Открыть проект"}>Открыть</button>
                    <button className="icon" onClick={() => onExport(p)} title="Экспорт проекта в файл .bfggis" aria-label={`Экспорт ${p.name}`}><Icon name="export" size={14} /></button>
                    <button className="icon" onClick={() => void rename(p)} title="Переименовать" aria-label={`Переименовать ${p.name}`}><Icon name="edit" size={14} /></button>
                    <button className="icon" onClick={() => run(onDelete(p.id))} title="Удалить проект со всеми данными" aria-label={`Удалить ${p.name}`}><Icon name="trash" size={14} /></button>
                  </td>
                </tr>
              );
            })}
            {!shown.length && <tr><td colSpan={5} className="muted small">Ничего не найдено.</td></tr>}
          </tbody>
        </table>
        <span className="muted small">Двойной щелчок по строке открывает проект.</span>
      </section>
      {note && <div className={`alert ${note.error ? "error" : "ok"} small`}>{note.text}</div>}
    </div>
  );
}
