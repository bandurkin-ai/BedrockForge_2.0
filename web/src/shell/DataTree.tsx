import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Dataset, Hole } from "../api";
import type { TableKind } from "../route";
import { loadHoles } from "../viz/useVizData";
import { Icon, type IconName } from "./icons";
import { isShown, layerKey, type WorkspaceState } from "./workspace";

const MAX_SHOWN = 300;

/** Categories of the project tree; only drillholes and wireframes can be loaded so far. */
const STUBS: { kind: string; title: string; icon: IconName; note: string }[] = [
  { kind: "lines", title: "Линии", icon: "lines", note: "Стринги Micromine STR, Surpac STR и DXF — этап 9" },
  { kind: "blocks", title: "Блочные модели", icon: "blocks", note: "Micromine DAT, Datamine DM, CSV — этап 12" },
  { kind: "rasters", title: "Растры", icon: "raster", note: "JPG, PNG, TIFF с привязкой (JGW, TAB, GRF) — этап 11" },
];

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
  selectedWireframe: number | null;
  onShow: (key: string, shown: boolean) => void;
  onSelectSet: (setId: number) => void;
  onSelectHole: (setId: number, holeId: string | null) => void;
  onSelectWireframe: (id: number | null) => void;
  onOpenTable: (setId: number, kind: TableKind) => void;
  onOpenAudit: (setId: number) => void;
  onImport: (setId: number | null) => void;
  onUploadWireframes: () => void;
  onRename: (dataset: Dataset) => void;
  onDelete: (dataset: Dataset) => void;
  onWireframeColor: (id: number, color: string) => void;
  onDeleteWireframe: (id: number, name: string) => void;
  onDemo: () => void;
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
  const [open, setOpen] = useState<Set<string>>(() => new Set(["cat:drillholes", "cat:wireframes"]));
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
  const wireframeFiles = (datasets ?? []).filter((d) => d.kind === "wireframes");
  const empty = datasets !== null && datasets.length === 0;
  const shown = (key: string) => isShown(ws, key);

  return (
    <div className="tree">
      <h2 className="dock-title">Данные проекта</h2>
      {empty && (
        <div className="empty">
          <p className="muted small">Проект пуст. Загрузите скважины или каркасы.</p>
          <button className="primary" onClick={() => props.onImport(null)}>Импорт скважин</button>
          <button onClick={props.onUploadWireframes}>Загрузить каркасы</button>
          <button onClick={props.onDemo}>Загрузить демо-проект</button>
        </div>
      )}
      <ul className="tree-root">
        <Category
          title="Скважины" icon="drillholes" count={sets.length} open={isOpen("cat:drillholes")} onToggle={() => toggle("cat:drillholes")}
          actions={<Action icon="plus" title="Новый набор скважин: импорт файлов" onClick={() => props.onImport(null)} />}
        >
          {sets.length === 0 && <li className="tree-note muted small">Нет наборов. «+» — импорт в новый набор.</li>}
          {sets.map((d) => {
            const key = `set:${d.id}`;
            const counts = d.counts ?? { collar: 0, survey: 0, assay: 0 };
            const component = d.components?.[0];
            return (
              <li key={d.id}>
                <div className={d.id === activeSet ? "tree-row set active-set" : "tree-row set"} onClick={() => props.onSelectSet(d.id)}>
                  <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                  <Eye shown={shown(layerKey.set(d.id))} onChange={(v) => props.onShow(layerKey.set(d.id), v)} label={d.name} />
                  <span className="grow name" title={d.name}>{d.name}</span>
                  <span className="count">{counts.collar.toLocaleString("ru-RU")}</span>
                  <span className="row-actions">
                    <Action icon="import" title="Импорт файлов в этот набор" onClick={() => props.onImport(d.id)} />
                    <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                    <Action icon="trash" title="Удалить набор со всеми данными" onClick={() => props.onDelete(d)} />
                  </span>
                </div>
                {isOpen(key) && (
                  <ul className="tree-children">
                    <li className="tree-row layer">
                      <Eye shown={shown(layerKey.setPart(d.id, "paths"))} onChange={(v) => props.onShow(layerKey.setPart(d.id, "paths"), v)} label="стволы" />
                      <span className="grow">Стволы</span>
                    </li>
                    <li className="tree-row layer">
                      <Eye shown={shown(layerKey.setPart(d.id, "intervals"))} onChange={(v) => props.onShow(layerKey.setPart(d.id, "intervals"), v)} label="содержания" />
                      <span className="grow">Содержания{component ? ` · ${d.components!.map((c) => c.name).join(", ")}` : ""}</span>
                    </li>
                    <li className="tree-row layer">
                      <Eye shown={shown(layerKey.setPart(d.id, "collars"))} onChange={(v) => props.onShow(layerKey.setPart(d.id, "collars"), v)} label="устья" />
                      <span className="grow">Устья</span>
                    </li>
                    {TABLES.map((t) => (
                      <li key={t.kind}>
                        <button className="tree-row leaf" onClick={() => props.onOpenTable(d.id, t.kind)} title={`Таблица «${t.label}»`}>
                          <Icon name="table" size={14} />
                          <span className="grow">{t.label}</span>
                          <span className="count">{counts[t.kind].toLocaleString("ru-RU")}</span>
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
          })}
        </Category>

        <Category
          title="Каркасы" icon="wireframe" count={wireframeFiles.reduce((n, d) => n + (d.wireframes?.length ?? 0), 0)}
          open={isOpen("cat:wireframes")} onToggle={() => toggle("cat:wireframes")}
          actions={<Action icon="plus" title="Загрузить каркасы: Micromine TRIDB, DXF" onClick={props.onUploadWireframes} />}
        >
          {wireframeFiles.length === 0 && <li className="tree-note muted small">Нет каркасов. «+» — файлы .tridb или .dxf.</li>}
          {wireframeFiles.map((d) => {
            const key = `file:${d.id}`;
            const items = d.wireframes ?? [];
            const allShown = items.every((w) => shown(layerKey.wireframe(w.id)));
            return (
              <li key={d.id}>
                <div className="tree-row set" onClick={() => toggle(key)}>
                  <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                  <Eye shown={allShown} onChange={(v) => items.forEach((w) => props.onShow(layerKey.wireframe(w.id), v))} label={d.name} />
                  <span className="grow name" title={`${d.name}${d.meta.format ? ` · ${d.meta.format}` : ""}`}>{d.name}</span>
                  <span className="count">{items.length}</span>
                  <span className="row-actions">
                    <Action icon="edit" title="Переименовать" onClick={() => props.onRename(d)} />
                    <Action icon="trash" title="Удалить файл со всеми каркасами" onClick={() => props.onDelete(d)} />
                  </span>
                </div>
                {isOpen(key) && (
                  <ul className="tree-children">
                    {items.map((w) => (
                      <li key={w.id}>
                        <div className={w.id === props.selectedWireframe ? "tree-row leaf active" : "tree-row leaf"} onClick={() => props.onSelectWireframe(w.id === props.selectedWireframe ? null : w.id)}>
                          <Eye shown={shown(layerKey.wireframe(w.id))} onChange={(v) => props.onShow(layerKey.wireframe(w.id), v)} label={w.name} />
                          <input
                            type="color" className="swatch" value={w.color} title="Цвет каркаса" aria-label={`Цвет: ${w.name}`}
                            onClick={(e) => e.stopPropagation()} onChange={(e) => props.onWireframeColor(w.id, e.target.value)}
                          />
                          <span className="grow name" title={w.name}>{w.name}</span>
                          <span className="count" title="Треугольников">{w.triangle_count.toLocaleString("ru-RU")}</span>
                          <span className="row-actions">
                            <Action icon="trash" title="Удалить каркас" onClick={() => props.onDeleteWireframe(w.id, w.name)} />
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </Category>

        {STUBS.map((stub) => (
          <Category
            key={stub.kind} title={stub.title} icon={stub.icon} open={isOpen(`cat:${stub.kind}`)} onToggle={() => toggle(`cat:${stub.kind}`)}
            actions={<Action icon="plus" title={`Загрузка появится позже: ${stub.note}`} onClick={() => undefined} disabled />}
          >
            <li className="tree-note muted small">В разработке. {stub.note}.</li>
          </Category>
        ))}
      </ul>
    </div>
  );
}
