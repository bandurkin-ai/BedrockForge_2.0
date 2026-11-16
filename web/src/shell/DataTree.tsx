import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Dataset, Hole, RasterInfo, SavedChartKind } from "../api";
import type { TableKind } from "../route";
import { loadHoles } from "../viz/useVizData";
import { Icon, type IconName } from "./icons";
import { isShown, layerKey, type ObjectKind, type SelectedObject, type WorkspaceState } from "./workspace";

const MAX_SHOWN = 300;


/** An item of a file-backed category (a wireframe, a line layer). */
interface FileItem {
  id: number;
  name: string;
  color: string;
  count: number;
  countTitle: string;
}

const TABLES: { kind: TableKind; label: string }[] = [
  { kind: "collar", label: "Устья" },
  { kind: "survey", label: "Инклинометрия" },
  { kind: "assay", label: "Пробы" },
];

export interface DataTreeProps {
  datasets: Dataset[] | null;
  ws: WorkspaceState;
  refreshKey: number;
  activeSet: number | null;
  selectedHole: string | null;
  selected: SelectedObject | null;
  onShow: (key: string, shown: boolean) => void;
  onSelectSet: (setId: number) => void;
  onSelectHole: (setId: number, holeId: string | null) => void;
  onSelectObject: (object: SelectedObject | null) => void;
  onOpenTable: (setId: number, kind: TableKind) => void;
  onOpenAudit: (setId: number) => void;
  onImport: (setId: number | null) => void;
  onUpload: (kind: "wireframes" | "lines" | "rasters" | "blocks") => void;
  onRename: (dataset: Dataset) => void;
  /** Ask for a folder and move the object there. */
  onMove: (dataset: Dataset) => void;
  onDelete: (dataset: Dataset) => void;
  onColor: (kind: "wireframe" | "lines", id: number, color: string) => void;
  onDeleteItem: (kind: "wireframe" | "lines", id: number, name: string) => void;
  onDemo: () => void;
  /** Right click on a wireframe or a line layer: its menu (edit, operations, export). */
  onItemMenu?: (kind: "wireframe" | "lines", id: number, x: number, y: number) => void;
  /** «+» of the lines group: load a file or make a new layer. */
  onLinesMenu?: (x: number, y: number) => void;
  /** The layer being edited and whether it has unsaved changes. */
  editing?: { layerId: number; dirty: boolean } | null;
  /** Right click on a drillhole set (or its assay table): the charts menu. */
  onSetMenu?: (setId: number, x: number, y: number) => void;
  /** A saved chart under a set: open it, or its menu (rename, delete). */
  onOpenChart?: (chartId: number) => void;
  onChartMenu?: (chart: { id: number; kind: SavedChartKind; name: string }, x: number, y: number) => void;
  /** The gear (or right click) of «Устья»: the form of the collars' marker and label. */
  onCollarStyle?: (setId: number) => void;
  /** The gear of «Стволы»: line width and colour, depth ticks, crossings with wireframes and block models. */
  onTraceStyle?: (setId: number) => void;
  /** The gear (or right click) of a block model: restriction, colouring by a field, how it is drawn. */
  onBlockSettings?: (id: number) => void;
  /** Right click on «Стволы»: what to show along the holes (grades, lithology, events). */
  onPathsMenu?: (setId: number, x: number, y: number) => void;
  /** A saved display of data along the holes: its form. */
  onOpenDisplay?: (chartId: number) => void;
  /** An interval or event table: its rows. */
  onOpenIntervalTable?: (table: { id: number; name: string; kind?: "interval" | "event" }, setId: number) => void;
  /** Right click on an interval table (the assays: key "assay"): downhole log, rename, delete. */
  onTableMenu?: (setId: number, table: { key: string; id: number | null; name: string }, x: number, y: number) => void;
  /** A table of the set shown on its holes in 3D (null: a new log window). */
  onNewLog?: (setId: number, tableKey: string | null) => void;
  /** Saved downhole displays that are on in the 3D view, and turning one on or off. */
  logsOn?: number[];
  onToggleLog?: (chartId: number, on: boolean) => void;
}

function Twisty({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button className="icon twisty" onClick={(e) => { e.stopPropagation(); onClick(); }} aria-label={open ? "Свернуть" : "Развернуть"} aria-expanded={open}>
      <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
    </button>
  );
}

function Eye({ shown, onChange, label }: { shown: boolean; onChange: (shown: boolean) => void; label: string }) {
  return <input type="checkbox" className="eye" checked={shown} onChange={(e) => onChange(e.target.checked)} aria-label={`Показать: ${label}`} title="Показать в 3D" />;
}

function Action({ icon, title, onClick, disabled }: { icon: IconName; title: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="icon row-action" title={title} aria-label={title} onClick={(e) => { e.stopPropagation(); onClick(); }} disabled={disabled}>
      <Icon name={icon} size={14} />
    </button>
  );
}

function Category({
  title, icon, count, open, onToggle, actions, children,
}: {
  title: string; icon: IconName; count?: number; open: boolean; onToggle: () => void; actions?: ReactNode; children?: ReactNode;
}) {
  return (
    <li className="tree-category">
      <div className="tree-row category" onClick={onToggle}>
        <Twisty open={open} onClick={onToggle} />
        <Icon name={icon} size={15} />
        <span className="grow">{title}</span>
        {count !== undefined && <span className="count">{count}</span>}
        <span className="row-actions" onClick={(e) => e.stopPropagation()}>{actions}</span>
      </div>
      {open && <ul className="tree-children">{children}</ul>}
    </li>
  );
}

/** Objects of one tree group arranged by their "/"-separated folder. */
interface FolderNode {
  name: string;
  path: string;
  folders: Map<string, FolderNode>;
  items: Dataset[];
}

export function folderTree(list: Dataset[]): FolderNode {
  const root: FolderNode = { name: "", path: "", folders: new Map(), items: [] };
  for (const d of list) {
    let node = root;
    for (const part of (d.folder ?? "").split("/").filter(Boolean)) {
      let next = node.folders.get(part);
      if (!next) {
        next = { name: part, path: node.path ? `${node.path}/${part}` : part, folders: new Map(), items: [] };
        node.folders.set(part, next);
      }
      node = next;
    }
    node.items.push(d);
  }
  return root;
}

const countIn = (node: FolderNode): number => node.items.length + [...node.folders.values()].reduce((n, f) => n + countIn(f), 0);

/** Folders first (open unless collapsed), then the objects of this level. */
function Folders({ node, group, closed, toggle, render }: {
  node: FolderNode; group: string; closed: (key: string) => boolean; toggle: (key: string) => void; render: (d: Dataset) => ReactNode;
}) {
  const folders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name, "ru"));
  return (
    <>
      {folders.map((f) => {
        const key = `fold:${group}:${f.path}`;
        const isOpen = !closed(key);
        return (
          <li key={key}>
            <div className="tree-row folder" onClick={() => toggle(key)}>
              <Twisty open={isOpen} onClick={() => toggle(key)} />
              <Icon name="folder" size={14} />
              <span className="grow name" title={f.path}>{f.name}</span>
              <span className="count">{countIn(f)}</span>
            </div>
            {isOpen && (
              <ul className="tree-children">
                <Folders node={f} group={group} closed={closed} toggle={toggle} render={render} />
              </ul>
            )}
          </li>
        );
      })}
      {node.items.map(render)}
    </>
  );
}

function HoleList({ setId, refreshKey, selectedHole, active, onSelectHole }: {
  setId: number; refreshKey: number; selectedHole: string | null; active: boolean; onSelectHole: (holeId: string | null) => void;
}) {
  const [holes, setHoles] = useState<Hole[] | null>(null);
  const [query, setQuery] = useState("");
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    let cancelled = false;
    loadHoles(setId, refreshKey).then((h) => !cancelled && setHoles(h)).catch(() => !cancelled && setHoles([]));
    return () => {
      cancelled = true;
    };
  }, [setId, refreshKey]);

  // Bring the hole picked in 3D into view.
  useEffect(() => {
    if (active && selectedHole) listRef.current?.querySelector(".active")?.scrollIntoView({ block: "nearest" });
  }, [active, selectedHole, holes]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (holes ?? []).filter((h) => (!q || h.hole_id.toLowerCase().includes(q)) && (!onlyFlagged || h.error + h.warning > 0));
  }, [holes, query, onlyFlagged]);

  if (holes === null) return <li className="tree-note muted small">Загрузка…</li>;
  if (holes.length === 0) return <li className="tree-note muted small">Нет устьев. Импортируйте Collar.</li>;
  return (
    <li className="hole-block">
      <div className="tree-filter">
        <input type="search" placeholder="Поиск скважины…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Поиск скважины" />
        <button className={onlyFlagged ? "icon active" : "icon"} onClick={() => setOnlyFlagged((v) => !v)} aria-pressed={onlyFlagged} title="Только скважины с замечаниями аудита">
          <Icon name="audit" size={15} />
        </button>
      </div>
      <ul className="hole-list" ref={listRef}>
        {filtered.slice(0, MAX_SHOWN).map((h) => {
          const selected = active && h.hole_id === selectedHole;
          return (
            <li key={h.hole_id}>
              <button
                className={selected ? "tree-row leaf active" : "tree-row leaf"}
                onClick={() => onSelectHole(selected ? null : h.hole_id)}
                title={`${h.hole_id}: глубина ${h.td ?? "—"} м, замеров ${h.survey_count}, проб ${h.assay_count}`}
              >
                <span className="grow hole-name">{h.hole_id}</span>
                <span className="dots">
                  {h.error > 0 && <span className="dot error" title={`Критично: ${h.error}`}>{h.error}</span>}
                  {h.warning > 0 && <span className="dot warning" title={`Предупреждений: ${h.warning}`}>{h.warning}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {filtered.length > MAX_SHOWN && <p className="tree-note muted small">Показано {MAX_SHOWN} из {filtered.length.toLocaleString("ru-RU")}. Уточните поиск.</p>}
      {filtered.length === 0 && <p className="tree-note muted small">Ничего не найдено.</p>}
    </li>
  );
}

/** Left dock: every object of the project by kind, with visibility in the 3D view. */
export function DataTree(props: DataTreeProps) {
  const { datasets, ws, activeSet } = props;
  const [open, setOpen] = useState<Set<string>>(() => new Set(["cat:drillholes", "cat:wireframe", "cat:lines", "cat:rasters", "cat:blocks"]));
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const isOpen = (key: string) => open.has(key);

  // Open the active set (and its holes when a hole gets selected elsewhere).
  useEffect(() => {
    if (activeSet === null) return;
    setOpen((prev) => {
      const keys = [`set:${activeSet}`, ...(props.selectedHole ? [`holes:${activeSet}`] : [])];
      if (keys.every((k) => prev.has(k))) return prev;
      return new Set([...prev, ...keys]);
    });
  }, [activeSet, props.selectedHole]);

  const sets = (datasets ?? []).filter((d) => d.kind === "drillholes");
  // A set whose chart count grew (a chart was just made): show its charts.
  const chartCounts = useRef(new Map<number, number>());
  useEffect(() => {
    const grown: string[] = [];
    for (const d of sets) {
      const n = d.charts?.length ?? 0;
      const before = chartCounts.current.get(d.id);
      if (before !== undefined && n > before) grown.push(`set:${d.id}`, `charts:${d.id}`);
      chartCounts.current.set(d.id, n);
    }
    if (grown.length) setOpen((prev) => new Set([...prev, ...grown]));
  }, [sets]);
  const setMenu = (e: React.MouseEvent, setId: number) => {
    if (!props.onSetMenu) return;
    e.preventDefault();
    e.stopPropagation();
    props.onSetMenu(setId, e.clientX, e.clientY);
  };
  const tableMenu = (e: React.MouseEvent, setId: number, table: { key: string; id: number | null; name: string } | null) => {
    e.preventDefault();
    e.stopPropagation();
    if (table !== null) props.onTableMenu?.(setId, table, e.clientX, e.clientY);
  };
  const empty = datasets !== null && datasets.length === 0;
  const shown = (key: string) => isShown(ws, key);

  return (
    <div className="tree">
      <h2 className="dock-title">Данные проекта</h2>
      {empty && (
        <div className="empty">
          <p className="muted small">Проект пуст. Загрузите скважины или каркасы.</p>
          <button className="primary" onClick={() => props.onImport(null)}>Импорт скважин</button>
          <button onClick={() => props.onUpload("wireframes")}>Загрузить каркасы</button>
          <button onClick={props.onDemo}>Загрузить демо-проект</button>
        </div>
      )}
      <ul className="tree-root">
        <Category
          title="Скважины" icon="drillholes" count={sets.length} open={isOpen("cat:drillholes")} onToggle={() => toggle("cat:drillholes")}
          actions={<Action icon="plus" title="Новый набор скважин: импорт файлов" onClick={() => props.onImport(null)} />}
        >
          {sets.length === 0 && <li className="tree-note muted small">Нет наборов. «+» — импорт в новый набор.</li>}
          <Folders node={folderTree(sets)} group="drillholes" closed={isOpen} toggle={toggle} render={(d) => {
            const key = `set:${d.id}`;
            const counts = d.counts ?? { collar: 0, survey: 0, assay: 0 };
            const displays = (d.charts ?? []).filter((c) => c.kind === "downhole");
            return (
              <li key={d.id}>
                <div
                  className={d.id === activeSet ? "tree-row set active-set" : "tree-row set"} onClick={() => props.onSelectSet(d.id)}
                  onContextMenu={(e) => setMenu(e, d.id)} title="Правая кнопка — графики"
                >
                  <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                  <Eye shown={shown(layerKey.set(d.id))} onChange={(v) => props.onShow(layerKey.set(d.id), v)} label={d.name} />
                  <span className="grow name" title={d.name}>{d.name}</span>
                  <span className="count">{counts.collar.toLocaleString("ru-RU")}</span>
                  <span className="row-actions">
                    <Action icon="import" title="Импорт файлов в этот набор" onClick={() => props.onImport(d.id)} />
                    <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                    <Action icon="folder" title="В папку…" onClick={() => props.onMove(d)} />
                    <Action icon="trash" title="Удалить набор со всеми данными" onClick={() => props.onDelete(d)} />
                  </span>
                </div>
                {isOpen(key) && (
                  <ul className="tree-children">
                    <li className="tree-row layer" onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); props.onCollarStyle?.(d.id); }}
                      title="Устья: значок и подпись — шестерёнка или правая кнопка">
                      <Eye shown={shown(layerKey.setPart(d.id, "collars"))} onChange={(v) => props.onShow(layerKey.setPart(d.id, "collars"), v)} label="устья" />
                      <span className="grow">Устья</span>
                      <span className="row-actions always">
                        <Action icon="gear" title="Значок, цвет, размер и подпись устьев…" onClick={() => props.onCollarStyle?.(d.id)} />
                      </span>
                    </li>
                    <li className="tree-row layer" onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); props.onPathsMenu?.(d.id, e.clientX, e.clientY); }}
                      title="Стволы: шестерёнка — толщина, цвет, риски глубины, пересечения; правая кнопка — что показать по стволам (содержания, литология, события)">
                      <Twisty open={!isOpen(`logsClosed:${d.id}`)} onClick={() => toggle(`logsClosed:${d.id}`)} />
                      <Eye shown={shown(layerKey.setPart(d.id, "paths"))} onChange={(v) => props.onShow(layerKey.setPart(d.id, "paths"), v)} label="стволы" />
                      <span className="grow">Стволы</span>
                      <span className="count">{displays.length || ""}</span>
                      <span className="row-actions always">
                        <Action icon="gear" title="Толщина и цвет стволов, риски глубины, точки пересечения с каркасами и блочными моделями…" onClick={() => props.onTraceStyle?.(d.id)} />
                      </span>
                    </li>
                    {/* Saved displays of data along the holes: on / off, drag onto the view, double click to edit. */}
                    {!isOpen(`logsClosed:${d.id}`) && (
                      <li>
                        <ul className="tree-children">
                          {displays.length === 0 && (
                            <li className="tree-note muted small">Правая кнопка на «Стволах» → Содержания, Литология или События.</li>
                          )}
                          {displays.map((c) => {
                            const on = (props.logsOn ?? []).includes(c.id);
                            return (
                              <li key={c.id}>
                                <div
                                  className={on ? "tree-row layer active" : "tree-row layer"} draggable
                                  onDragStart={(e) => {
                                    e.dataTransfer.setData("application/x-bedrockforge-log", String(c.id));
                                    e.dataTransfer.effectAllowed = "copy";
                                  }}
                                  onClick={() => props.onToggleLog?.(c.id, !on)} onDoubleClick={() => props.onOpenDisplay?.(c.id)}
                                  onContextMenu={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    props.onChartMenu?.(c, e.clientX, e.clientY);
                                  }}
                                  title={`${c.name}: клик — показать / скрыть на скважинах, можно перетащить в окно вида; двойной клик — настройки`}
                                >
                                  <input type="checkbox" className="eye" checked={on} onClick={(e) => e.stopPropagation()}
                                    onChange={(e) => props.onToggleLog?.(c.id, e.target.checked)} aria-label={`На скважинах: ${c.name}`} />
                                  <Icon name="striplog" size={14} />
                                  <span className="grow name">{c.name}</span>
                                  <span className="row-actions">
                                    <Action icon="gear" title="Настройки отображения…" onClick={() => props.onOpenDisplay?.(c.id)} />
                                  </span>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      </li>
                    )}
                    {/* The built-in assay table only when it has rows: assay files are named tables of their own now. */}
                    {TABLES.filter((t) => t.kind !== "assay" || counts.assay > 0).map((t) => (
                      <li key={t.kind}>
                        <button
                          className="tree-row leaf" onClick={() => props.onOpenTable(d.id, t.kind)}
                          onContextMenu={t.kind === "assay" ? (e) => tableMenu(e, d.id, { key: "assay", id: null, name: "Пробы" }) : undefined}
                          title={t.kind === "assay" ? "Таблица «Пробы»; правая кнопка — графики" : `Таблица «${t.label}»`}
                        >
                          <Icon name="table" size={14} />
                          <span className="grow">{t.label}</span>
                          <span className="count">{counts[t.kind].toLocaleString("ru-RU")}</span>
                        </button>
                      </li>
                    ))}
                    {(d.interval_tables ?? []).map((t) => (
                      <li key={`it${t.id}`}>
                        <button
                          className="tree-row leaf" onClick={() => props.onOpenIntervalTable?.(t, d.id)}
                          onContextMenu={(e) => tableMenu(e, d.id, { key: String(t.id), id: t.id, name: t.name })}
                          title={`${t.kind === "event" ? "События" : "Интервальная таблица"} «${t.name}»: клик — открыть в нижней панели, правая кнопка — меню`}
                        >
                          <Icon name="table" size={14} />
                          <span className="grow name">{t.name}{t.kind === "event" ? " · события" : ""}</span>
                          <span className="count">{t.row_count.toLocaleString("ru-RU")}</span>
                        </button>
                      </li>
                    ))}
                    <li>
                      <button className="tree-row leaf" onClick={() => props.onOpenAudit(d.id)} title="Результаты аудита набора">
                        <Icon name="audit" size={14} />
                        <span className="grow">Аудит</span>
                        {d.audit ? (
                          <span className="dots">
                            <span className="dot error">{d.audit.error}</span>
                            <span className="dot warning">{d.audit.warning}</span>
                          </span>
                        ) : (
                          <span className="count">не запускался</span>
                        )}
                      </button>
                    </li>
                    <li>
                      <div className="tree-row" onClick={() => toggle(`charts:${d.id}`)} onContextMenu={(e) => setMenu(e, d.id)} title="Сохранённые графики; правая кнопка — новый">
                        <Twisty open={isOpen(`charts:${d.id}`)} onClick={() => toggle(`charts:${d.id}`)} />
                        <Icon name="chart" size={14} />
                        <span className="grow">Графики</span>
                        <span className="count">{(d.charts ?? []).filter((c) => c.kind !== "striplog" && c.kind !== "downhole").length}</span>
                      </div>
                      {isOpen(`charts:${d.id}`) && (
                        <ul className="tree-children">
                          {!(d.charts ?? []).some((c) => c.kind !== "striplog" && c.kind !== "downhole") && <li className="tree-note muted small">Нет графиков. Правая кнопка на наборе или «Пробы» → график.</li>}
                          {(d.charts ?? []).filter((c) => c.kind !== "striplog" && c.kind !== "downhole").map((c) => (
                            <li key={c.id}>
                              <button
                                className="tree-row leaf" onClick={() => props.onOpenChart?.(c.id)} title={`${c.name} — открыть`}
                                onContextMenu={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  props.onChartMenu?.(c, e.clientX, e.clientY);
                                }}
                              >
                                <Icon name={c.kind === "downhole" ? "striplog" : c.kind} size={14} />
                                <span className="grow name">{c.name}</span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                    <li>
                      <div className="tree-row" onClick={() => toggle(`holes:${d.id}`)}>
                        <Twisty open={isOpen(`holes:${d.id}`)} onClick={() => toggle(`holes:${d.id}`)} />
                        <Icon name="hole" size={14} />
                        <span className="grow">Скважины</span>
                        <span className="count">{counts.collar.toLocaleString("ru-RU")}</span>
                      </div>
                      {isOpen(`holes:${d.id}`) && (
                        <ul className="tree-children">
                          <HoleList
                            setId={d.id} refreshKey={props.refreshKey} active={d.id === activeSet} selectedHole={props.selectedHole}
                            onSelectHole={(hole) => props.onSelectHole(d.id, hole)}
                          />
                        </ul>
                      )}
                    </li>
                  </ul>
                )}
              </li>
            );
          }} />
        </Category>

        <FileCategory
          {...props} kind="wireframe" title="Каркасы" icon="wireframe" open={open} toggle={toggle}
          uploadTitle="Загрузить каркасы: Micromine TRIDB, DXF" emptyNote="Нет каркасов. «+» — файлы .tridb или .dxf."
          files={(datasets ?? []).filter((d) => d.kind === "wireframes")}
          items={(d) => (d.wireframes ?? []).map((w) => ({ id: w.id, name: w.name, color: w.color, count: w.triangle_count, countTitle: "Треугольников" }))}
          onAdd={() => props.onUpload("wireframes")}
        />
        <FileCategory
          {...props} kind="lines" title="Линии" icon="lines" open={open} toggle={toggle}
          uploadTitle="Загрузить линии: Micromine STR, Surpac STR, DXF" emptyNote="Нет линий. «+» — файлы .str или .dxf."
          files={(datasets ?? []).filter((d) => d.kind === "lines")}
          items={(d) => (d.lines ?? []).map((l) => ({ id: l.id, name: l.name, color: l.color, count: l.line_count, countTitle: "Линий" }))}
          onAdd={(e) => (props.onLinesMenu ? props.onLinesMenu(e.clientX, e.clientY) : props.onUpload("lines"))}
        />
        <RasterCategory {...props} open={open} toggle={toggle} />

        <BlockCategory {...props} open={open} toggle={toggle} />
      </ul>
    </div>
  );
}

const LAYER_KEY: Record<"wireframe" | "lines", (id: number) => string> = { wireframe: layerKey.wireframe, lines: layerKey.lines };

/** Wireframes and lines: files, and inside every file its items with colour and visibility. */
function FileCategory(props: DataTreeProps & {
  kind: "wireframe" | "lines";
  title: string;
  icon: IconName;
  uploadTitle: string;
  emptyNote: string;
  files: Dataset[];
  items: (d: Dataset) => FileItem[];
  open: Set<string>;
  toggle: (key: string) => void;
  onAdd: (e: React.MouseEvent) => void;
}) {
  const { kind, files, open, toggle, ws } = props;
  const keyOf = LAYER_KEY[kind];
  const catKey = `cat:${kind}`;
  const total = files.reduce((n, d) => n + props.items(d).length, 0);
  const isSelected = (id: number) => props.selected?.kind === kind && props.selected.id === id;
  return (
    <Category
      title={props.title} icon={props.icon} count={total} open={open.has(catKey)} onToggle={() => toggle(catKey)}
      actions={<button className="icon row-action" title={props.uploadTitle} aria-label={props.uploadTitle} onClick={(e) => { e.stopPropagation(); props.onAdd(e); }}><Icon name="plus" size={14} /></button>}
    >
      {files.length === 0 && <li className="tree-note muted small">{props.emptyNote}</li>}
      <Folders node={folderTree(files)} group={kind} closed={(k) => open.has(k)} toggle={toggle} render={(d) => {
        const key = `file:${d.id}`;
        const items = props.items(d);
        const allShown = items.every((item) => isShown(ws, keyOf(item.id)));
        return (
          <li key={d.id}>
            <div className="tree-row set" onClick={() => toggle(key)}>
              <Twisty open={open.has(key)} onClick={() => toggle(key)} />
              <Eye shown={allShown} onChange={(v) => items.forEach((item) => props.onShow(keyOf(item.id), v))} label={d.name} />
              <span className="grow name" title={`${d.name}${d.meta.format ? ` · ${d.meta.format}` : ""}`}>{d.name}</span>
              <span className="count">{items.length}</span>
              <span className="row-actions">
                <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                <Action icon="folder" title="В папку…" onClick={() => props.onMove(d)} />
                <Action icon="trash" title="Удалить файл со всем содержимым" onClick={() => props.onDelete(d)} />
              </span>
            </div>
            {open.has(key) && (
              <ul className="tree-children">
                {items.map((item) => (
                  <li key={item.id}>
                    <div className={isSelected(item.id) ? "tree-row leaf active" : "tree-row leaf"} onClick={() => props.onSelectObject(isSelected(item.id) ? null : { kind, id: item.id })}
                      onContextMenu={(e) => {
                        if (!props.onItemMenu) return;
                        e.preventDefault();
                        props.onItemMenu(kind, item.id, e.clientX, e.clientY);
                      }}>
                      <Eye shown={isShown(ws, keyOf(item.id))} onChange={(v) => props.onShow(keyOf(item.id), v)} label={item.name} />
                      <input
                        type="color" className="swatch" value={item.color} title="Цвет" aria-label={`Цвет: ${item.name}`}
                        onClick={(e) => e.stopPropagation()} onChange={(e) => props.onColor(kind, item.id, e.target.value)}
                      />
                      <span className="grow name" title={item.name}>{item.name}
                        {kind === "lines" && props.editing?.layerId === item.id && <span className="edit-dirty" title={props.editing.dirty ? "Редактируется: есть несохранённые изменения" : "Редактируется"}>{props.editing.dirty ? " ● правка" : " ✎"}</span>}
                      </span>
                      <span className="count" title={item.countTitle}>{item.count.toLocaleString("ru-RU")}</span>
                      <span className="row-actions">
                        <Action icon="trash" title="Удалить" onClick={() => props.onDeleteItem(kind, item.id, item.name)} />
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      }} />
    </Category>
  );
}

/** Block models: one model per file. */
function BlockCategory(props: DataTreeProps & { open: Set<string>; toggle: (key: string) => void }) {
  const { open, toggle, ws } = props;
  const files = (props.datasets ?? []).filter((d) => d.kind === "blocks");
  const kind: ObjectKind = "blocks";
  return (
    <Category
      title="Блочные модели" icon="blocks" count={files.length} open={open.has("cat:blocks")} onToggle={() => toggle("cat:blocks")}
      actions={<Action icon="plus" title="Загрузить блочную модель: Micromine DAT или CSV (X, Y, Z и размеры _X, _Y, _Z)" onClick={() => props.onUpload("blocks")} />}
    >
      {files.length === 0 && <li className="tree-note muted small">Нет моделей. «+» — Micromine DAT или CSV.</li>}
      <Folders node={folderTree(files)} group="blocks" closed={(k) => open.has(k)} toggle={toggle} render={(d) => {
        const selected = props.selected?.kind === kind && props.selected.id === d.id;
        const n = (d.meta as { block_count?: number }).block_count ?? 0;
        const errors = ((d.meta as { checks?: { level: string }[] }).checks ?? []).filter((c) => c.level === "error").length;
        return (
          <li key={d.id}>
            <div className={selected ? "tree-row leaf active" : "tree-row leaf"} onClick={() => props.onSelectObject(selected ? null : { kind, id: d.id })}
              onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); props.onBlockSettings?.(d.id); }}>
              <Eye shown={isShown(ws, layerKey.blocks(d.id))} onChange={(v) => props.onShow(layerKey.blocks(d.id), v)} label={d.name} />
              <Icon name="blocks" size={14} />
              <span className="grow name" title={`${d.name}${d.meta.format ? ` · ${d.meta.format}` : ""} — шестерёнка или правая кнопка: настройки`}>{d.name}</span>
              {errors > 0 && <span className="dot error" title={`Ошибок проверки: ${errors}`}>{errors}</span>}
              <span className="count" title="Блоков">{n.toLocaleString("ru-RU")}</span>
              <span className="row-actions always">
                <Action icon="gear" title="Настройки модели: ограничитель, раскраска по полю, просмотр (оболочка, срез, края)…" onClick={() => props.onBlockSettings?.(d.id)} />
              </span>
              <span className="row-actions">
                <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                <Action icon="folder" title="В папку…" onClick={() => props.onMove(d)} />
                <Action icon="trash" title="Удалить модель" onClick={() => props.onDelete(d)} />
              </span>
            </div>
          </li>
        );
      }} />
    </Category>
  );
}

/** Rasters: one image per entry. */
function RasterCategory(props: DataTreeProps & { open: Set<string>; toggle: (key: string) => void }) {
  const { open, toggle, ws } = props;
  const files = (props.datasets ?? []).filter((d) => d.kind === "rasters");
  const entries = files.flatMap((d) => (d.rasters ?? []).map((r): [Dataset, RasterInfo] => [d, r]));
  const kind: ObjectKind = "raster";
  return (
    <Category
      title="Растры" icon="raster" count={entries.length} open={open.has("cat:rasters")} onToggle={() => toggle("cat:rasters")}
      actions={<Action icon="plus" title="Загрузить растры: изображение вместе с файлом привязки (.jgw/.pgw/.tfw, .tab, .grf) или GeoTIFF" onClick={() => props.onUpload("rasters")} />}
    >
      {entries.length === 0 && <li className="tree-note muted small">Нет растров. «+» — изображение и файл привязки.</li>}
      <Folders node={folderTree(files)} group="rasters" closed={(k) => open.has(k)} toggle={toggle} render={(d) => (d.rasters ?? []).map((r) => {
        const selected = props.selected?.kind === kind && props.selected.id === r.id;
        return (
          <li key={r.id}>
            <div className={selected ? "tree-row leaf active" : "tree-row leaf"} onClick={() => props.onSelectObject(selected ? null : { kind, id: r.id })}>
              <Eye shown={isShown(ws, layerKey.raster(r.id))} onChange={(v) => props.onShow(layerKey.raster(r.id), v)} label={r.name} />
              <Icon name="raster" size={14} />
              <span className="grow name" title={`${d.name} · ${r.georef.source}`}>{r.name}</span>
              <span className="count" title="Размер пикселя">{r.georef.pixel_size.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} м</span>
              <span className="row-actions">
                <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                <Action icon="folder" title="В папку…" onClick={() => props.onMove(d)} />
                <Action icon="trash" title="Удалить растр" onClick={() => props.onDelete(d)} />
              </span>
            </div>
          </li>
        );
      })} />
    </Category>
  );
}
