/**
 * The section «Неявные модели» of the data tree (ТЗ 8.9.4; ADR 0026 «Команды реестра»): the models by name; under a model — «Данные» (the groups, each with
 * the check box «точки в 3D»: `implicit.show`), «Поверхности» and «Тела» (the wireframes of the build — ordinary wireframes of the project: a click chooses the
 * wireframe, `object.select`) — and «Линии» (Э19б). A model carries the badge of the graph («устарел» with the chain of the causes, «источник удалён») and, when
 * the build failed, «ошибка»; a click chooses it (`object.select {kind: "implicit"}`), a double click or Enter opens «Свойства»; the menu — the commands of the
 * registry (`treeModel.ts`), unavailable ones with the reason. «+» of the section — `implicit.new` (by the orchestrator: shell/treeSections.ts). The rows are
 * a flat list made at once (`treeItems`): 100 models are a few ms.
 */
import { useMemo, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import { selectWithProperties } from "../../shell/treeRun";
import tree from "../../shell/DataTree.module.css";
import { useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import type { LNode } from "../lineage/api";
import { useSel } from "./hooks";
import { implicitStore, loadRows } from "./state";
import { BRANCH_TITLES, branchMenu, groupTitle, modelMenu, modelTitleText, statsText, stateText, treeItems, type OpenState, type TreeItem } from "./treeModel";
import { implicitViewStore, pointsShown } from "./viewState";

export const NODE_KIND = "object:implicit_model";

const icon = (name: string): IconName => name as IconName;

export function ImplicitTree() {
  const app = useApp();
  const { rows, loaded, error } = useSel(implicitStore, (s) => s);
  const view = useSel(implicitViewStore, (s) => s);
  const selected = useSel(app.store, (s) => s.selected);
  const [open, setOpen] = useState<OpenState>({});
  const [chosen, setChosen] = useState<string | null>(null);
  const items = useMemo(() => treeItems(rows, open), [rows, open]);
  const nodes = useMemo<LNode[]>(() => rows.map((r) => ({ kind: NODE_KIND, id: r.id })), [rows]);
  useWatch(nodes); // the states of the whole section in one request (Д-35)
  if (!rows.length) return <div className={tree.none}>{error ? `Не прочитано: ${error}` : loaded ? "Нет моделей — «+» открывает мастер «Новая модель»" : "Нет объектов"}</div>;

  const run = (id: string, args: Record<string, unknown>) => app.run(id, args);
  const reason = (id: string) => app.registry.reason(id);
  const toggle = (key: string, now: boolean) => setOpen((o) => ({ ...o, [key]: !now }));

  const draw = (it: TreeItem) => {
    if (it.kind === "model") {
      const r = it.row;
      const on = selected?.kind === "implicit" && selected.id === r.id;
      const menu = (e: MouseEvent) => {
        e.preventDefault();
        void run("object.select", { kind: "implicit", id: r.id });
        openMenu(e.clientX, e.clientY, modelMenu(r, reason, run, () => void loadRows()));
      };
      return (
        <TreeRow
          key={it.key}
          label={r.name}
          ariaLabel={`${r.name}: ${stateText(r) || statsText(r)}`}
          level={it.level}
          expanded={it.expanded}
          onToggle={() => toggle(it.key, it.expanded)}
          selected={on}
          onSelect={() => void run("object.select", { kind: "implicit", id: r.id })}
          onActivate={() => void selectWithProperties(app, "implicit", r.id)}
          title={modelTitleText(r)}
          testId={`tree-implicit-${r.id}`}
          glyph={<Glyph icon={icon("implicit")} />}
          after={
            <>
              {r.state === "error" && (
                <span className={tree.error} data-testid={`tree-implicit-error-${r.id}`}>
                  ошибка
                </span>
              )}
              <LineageBadge node={{ kind: NODE_KIND, id: r.id }} hideCurrent fallback={r.stale || r.source_lost ? <span className={tree.count}>{r.source_lost ? "источник удалён" : "устарела"}</span> : null} />
            </>
          }
          onContextMenu={menu}
        />
      );
    }
    if (it.kind === "branch") {
      return (
        <TreeRow
          key={it.key}
          label={BRANCH_TITLES[it.branch]}
          level={it.level}
          expanded={it.expanded}
          onToggle={() => toggle(it.key, it.expanded)}
          after={<span className={tree.count}>{it.count}</span>}
          testId={`tree-implicit-${it.model}-${it.branch}`}
          onContextMenu={(e) => {
            e.preventDefault();
            openMenu(e.clientX, e.clientY, branchMenu(it.model, it.branch, reason, run));
          }}
        />
      );
    }
    if (it.kind === "group") {
      const gid = it.group.id;
      const shown = gid !== null && pointsShown(view, it.model, gid);
      const entry = gid !== null ? view.entries[`${it.model}:${gid}`] : undefined;
      return (
        <TreeRow
          key={it.key}
          label={it.group.name || `группа ${gid ?? ""}`}
          ariaLabel={`${it.group.name}: ${groupTitle(it.group.type)}`}
          level={it.level}
          selected={chosen === it.key}
          onSelect={() => setChosen(it.key)}
          check={gid === null ? undefined : { checked: shown, label: `${it.group.name}: точки данных в 3D`, onChange: (on) => void run("implicit.show", { model_id: it.model, group_id: gid, what: "points", visible: on }) }}
          title={`Группа «${it.group.name}» — ${groupTitle(it.group.type)}\nГалочка — точки данных группы в 3D${entry?.status === "error" ? `\nНе прочитано: ${entry.error}` : ""}`}
          testId={`tree-implicit-${it.model}-group-${gid}`}
          glyph={<Glyph icon={icon(it.group.type === "vein" ? "vein" : "domains")} />}
          after={<span className={entry?.status === "error" ? tree.error : tree.count}>{entry?.status === "loading" ? "…" : entry?.status === "error" ? "ошибка" : groupTitle(it.group.type)}</span>}
        />
      );
    }
    const o = it.output;
    const wid = o.wireframe_id;
    return (
      <TreeRow
        key={it.key}
        label={o.name || `каркас ${wid ?? ""}`}
        level={it.level}
        selected={chosen === it.key || (selected?.kind === "wireframe" && wid !== null && selected.id === wid)}
        onSelect={() => {
          setChosen(it.key);
          if (wid !== null) void run("object.select", { kind: "wireframe", id: wid });
        }}
        onActivate={() => wid !== null && void selectWithProperties(app, "wireframe", wid)}
        title={`${it.branch === "surface" ? "Поверхность" : it.branch === "body" ? "Тело" : "Линия"} модели: обычный каркас проекта, перезаписывается командой «Обновить модель». «В каркас…» делает копию без связи с моделью.`}
        testId={`tree-implicit-out-${wid ?? o.name}`}
        glyph={<Glyph icon={icon("wireframe")} />}
      />
    );
  };

  return (
    <div role="group" aria-label="Неявные модели" data-testid="tree-implicit" data-models={rows.length}>
      {items.map(draw)}
    </div>
  );
}
