import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { formatDate, listProjects, objectCount, projectStore, SUMMARY_TITLES, updateProject, type Description, type ProjectEntry } from "./state";
import css from "./Projects.module.css";

const DESC_FIELDS: [keyof Description, string][] = [
  ["deposit", "Месторождение"],
  ["license", "Лицензия"],
  ["stage", "Стадия работ"],
];

/** «Проекты» (ТЗ 7.3): the open project — name, description, dates, composition; all projects — search, open, rename, delete, export. */
export function ProjectsWindow() {
  const app = useApp();
  const current = useStore(projectStore, (s) => s.current);
  const version = useStore(projectStore, (s) => s.version);
  const coreOnline = useStore(app.store, (s) => s.core === "online");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<ProjectEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [desc, setDesc] = useState<Description>({ deposit: "", license: "", stage: "" });

  // The fields follow the project when another one opens; a save of one field must not reset the field
  // being typed next (the reply of the core arrives while the user types).
  const uid = current?.uid;
  useEffect(() => {
    const cur = projectStore.get().current;
    if (cur) setDesc({ ...cur.description });
  }, [uid]);

  useEffect(() => {
    if (!coreOnline) return;
    let alive = true;
    const t = setTimeout(() => {
      listProjects(query)
        .then((r) => alive && (setRows(r.projects), setError(null)))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    }, 150);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [query, version, coreOnline]);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const saveDesc = () => {
    if (!current) return;
    const changed = DESC_FIELDS.some(([k]) => (current.description[k] ?? "") !== desc[k]);
    if (changed) updateProject(app, current.uid, { description: desc, expected_version: current.version }).catch(fail);
  };
  const run = (id: string, args: Record<string, unknown> = {}) => void app.run(id, args);
  const open = (uid: string) => app.run("project.open", { uid });

  return (
    <div className={css.wrap}>
      <section className={css.section} aria-label="Открытый проект">
        <h3>Открытый проект</h3>
        {current ? (
          <>
            <div className={css.title}>
              <span data-testid="current-project">{current.name}</span>
              {current.read_only && <span className={css.warn}>только чтение</span>}
              <button type="button" className={css.btn} onClick={() => run("project.rename")} disabled={current.read_only}>
                Переименовать
              </button>
              <button type="button" className={css.btn} onClick={() => run("project.export")}>
                Экспорт
              </button>
            </div>
            {DESC_FIELDS.map(([k, label]) => (
              <label key={k} className={css.row}>
                <span className={css.label}>{label}</span>
                <input className={css.input} aria-label={label} value={desc[k]} disabled={current.read_only} onChange={(e) => setDesc({ ...desc, [k]: e.target.value })} onBlur={saveDesc} />
              </label>
            ))}
            <div className={css.row}>
              <span className={css.label}>Создан / изменён</span>
              <span>
                {formatDate(current.created_at)} / {formatDate(current.updated_at)}
              </span>
            </div>
            <div className={css.counts} aria-label="Состав проекта">
              {SUMMARY_TITLES.map(([k, title]) => (
                <span key={k}>
                  {title}:<b>{current.summary[k] ?? 0}</b>
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className={css.note}>Проект не открыт: откройте проект из списка, создайте новый или загрузите демо-проект.</div>
        )}
      </section>

      <section className={css.section} aria-label="Все проекты">
        <h3>Все проекты</h3>
        <input className={css.input} placeholder="Поиск по названию и описанию" aria-label="Поиск проектов" value={query} onChange={(e) => setQuery(e.target.value)} />
        {error && <div className={css.error}>{error}</div>}
        <table className={css.table}>
          <thead>
            <tr>
              <th>Название</th>
              <th className={css.num}>Скважин</th>
              <th className={css.num}>Объектов</th>
              <th>Изменён</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((p) => (
              <tr key={p.uid} className={p.open ? css.open : ""} onDoubleClick={() => void open(p.uid)} data-testid="project-row">
                <td>
                  {p.name}
                  {p.damaged && <span className={css.error}> · повреждён</span>}
                  {p.outdated && <span className={css.note}> · будет обновлён при открытии</span>}
                </td>
                <td className={css.num}>{p.summary.holes ?? 0}</td>
                <td className={css.num}>{objectCount(p.summary)}</td>
                <td>{formatDate(p.updated_at)}</td>
                <td className={css.actions}>
                  <button type="button" className={css.btn} onClick={() => void open(p.uid)} disabled={p.open}>
                    Открыть
                  </button>
                  <button type="button" className={css.iconBtn} title="Переименовать" aria-label={`Переименовать ${p.name}`} onClick={() => void open(p.uid).then(() => run("project.rename"))}>
                    <Icon name="edit" size={14} />
                  </button>
                  <button type="button" className={css.iconBtn} title="Экспорт" aria-label={`Экспорт ${p.name}`} onClick={() => void open(p.uid).then(() => run("project.export"))}>
                    <Icon name="export" size={14} />
                  </button>
                  <button type="button" className={`${css.iconBtn} ${css.danger}`} title="Удалить" aria-label={`Удалить ${p.name}`} onClick={() => void open(p.uid).then(() => run("project.delete"))}>
                    <Icon name="trash" size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows && !rows.length && <div className={css.note}>{query ? "Ничего не найдено." : "Проектов пока нет."}</div>}
      </section>

      <div className={css.footer}>
        <button type="button" className={`${css.btn} ${css.primary}`} onClick={() => run("project.new")}>
          Новый проект
        </button>
        <button type="button" className={css.btn} onClick={() => run("project.demo")}>
          Демо-проект
        </button>
        <button type="button" className={css.btn} onClick={() => run("project.import")}>
          Импорт…
        </button>
      </div>
    </div>
  );
}
