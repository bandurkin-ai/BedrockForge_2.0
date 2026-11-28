/**
 * The section «Неявные модели» of the data tree (ТЗ 8.9.4; ADR 0026 «Команды реестра»): the models by name; under a model — «Данные» (the groups, each with
 * the check box «точки в 3D»: `implicit.show`), «Поверхности» and «Тела» (the wireframes of the build — ordinary wireframes of the project: a click chooses the
 * wireframe, `object.select`) — and «Линии» (Э19б). A model carries the badge of the graph («устарел» with the chain of the causes, «источник удалён») and, when
 * the build failed, «ошибка»; a click chooses it (`object.select {kind: "implicit"}`), a double click or Enter opens «Свойства»; the menu — the commands of the
 * registry (`treeModel.ts`), unavailable ones with the reason. «+» of the section — `implicit.new` (by the orchestrator: shell/treeSections.ts). The rows are
 * a flat list made at once (`treeItems`): 100 models are a few ms.
 */
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { isSelected } from "../../shell/selection";
import { TreeRow } from "../../shell/TreeRow";
import { selectWithProperties } from "../../shell/treeRun";
import tree from "../../shell/DataTree.module.css";
import { useWatch } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import type { LNode } from "../lineage/api";
import { useSel } from "../estimation/hooks";
import { projectStore } from "../projects/state";
import { implicitStore, loadDetails, loadRows } from "./state";
import { BRANCH_TITLES, branchMenu, groupMenu, groupTitle, modelMenu, modelTitleText, needsDetails, statsText, stateText, treeItems, type OpenState, type TreeItem } from "./treeModel";
import { implicitViewStore, pointsShown } from "./viewState";

export const NODE_KIND = "object:implicit_model";

const groupNameOfOutput = (rows: readonly { id: number; group_list: { id: number | null; name: string }[] }[], model: number, group: number | null): string => rows.find((r) => r.id === model)?.group_list.find((g) => g.id === group)?.name ?? "Система жил";

/** Reads the details of the expanded models that need them and have not been asked yet; a failed read is asked again next time. */
export function askDetails(items: readonly TreeItem[], details: Record<number, { version: number } | undefined>, asked: Set<string>, uid: string | null, load: (id: number) => Promise<unknown>): void {
  for (const it of items) {
    if (it.kind !== "model" || !it.expanded || !needsDetails(it.row) || details[it.row.id]?.version === it.row.version) continue;
    const key = `${uid ?? ""}:${it.row.id}:${it.row.version}`;
    if (asked.has(key)) continue;
    asked.add(key);
    void load(it.row.id).catch(() => asked.delete(key));
  }
}

const icon = (name: string): IconName => name as IconName;

export function ImplicitTree() {
  const app = useApp();
  const { rows, loaded, error } = useSel(implicitStore, (s) => s);
  const view = useSel(implicitViewStore, (s) => s);
  const selected = useSel(app.store, (s) => s.selected);
  const [open, setOpen] = useState<OpenState>({});
  const [chosen, setChosen] = useState<string | null>(null);
  const details = useSel(implicitStore, (s) => s.details);
  const hidden = useSel(app.store, (s) => s.view.hidden);
  const items = useMemo(() => treeItems(rows, open, details), [rows, open, details]);
  // the list has no relations, radii and lines: the models that show them are read once per version (of one project: the key has its uid)
  const asked = useRef(new Set<string>());
  const uid = useSel(projectStore, (s) => s.current?.uid ?? null);
  useEffect(() => {
    askDetails(items, details, asked.current, uid, loadDetails);
  }, [items, details, uid]);
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
          onContextMenu={(e) => {
            const menu = groupMenu(it.model, it.group, reason, run);
            if (!menu.length) return;
            e.preventDefault();
            openMenu(e.clientX, e.clientY, menu);
          }}
        />
      );
    }
    if (it.kind === "fault") {
      return (
        <TreeRow
          key={it.key}
          label={it.group.name}
          ariaLabel={`${it.group.name}: разлом${it.text ? `, ${it.text}` : ""}`}
          level={it.level}
          selected={chosen === it.key}
          onSelect={() => setChosen(it.key)}
          onActivate={() => void run("implicit.fault", { model_id: it.model, group_id: it.group.id })}
          title={`Разлом «${it.group.name}»${it.text ? `: ${it.text}` : ""}\nДвойной щелчок — окно «Разлом»; смещение — результат построения, только чтение`}
          testId={`tree-implicit-${it.model}-fault-${it.group.id}`}
          glyph={<Glyph icon={icon("contact")} />}
          after={it.text ? <span className={tree.count}>{it.text}</span> : undefined}
          onContextMenu={(e) => {
            e.preventDefault();
            openMenu(e.clientX, e.clientY, groupMenu(it.model, it.group, reason, run));
          }}
        />
      );
    }
    if (it.kind === "stackgroup") {
      return (
        <TreeRow
          key={it.key}
          label={`${it.position}. ${it.group.name}`}
          ariaLabel={`${it.position}. ${it.group.name}${it.relation ? `: ${it.relation}` : ""}`}
          level={it.level}
          selected={chosen === it.key}
          onSelect={() => setChosen(it.key)}
          onActivate={() => void run("implicit.relations", { model_id: it.model })}
          title={`Домен «${it.group.name}» — ${it.position}-й по возрасту (сверху самый молодой)${it.relation ? `; отношение к более старым: ${it.relation}` : ""}\nДвойной щелчок — окно «Стек отношений»`}
          testId={`tree-implicit-${it.model}-stack-${it.group.id}`}
          glyph={<Glyph icon={icon("domains")} />}
          after={it.relation ? <span className={tree.count}>{it.relation}</span> : undefined}
          onContextMenu={(e) => {
            e.preventDefault();
            openMenu(e.clientX, e.clientY, groupMenu(it.model, it.group, reason, run));
          }}
        />
      );
    }
    if (it.kind === "wings") {
      return (
        <TreeRow
          key={it.key}
          label={`${it.name}: ${it.region ? "области крыльев" : "крылья"}`}
          level={it.level}
          expanded={it.expanded}
          onToggle={() => toggle(it.key, it.expanded)}
          after={<span className={tree.count}>{it.count}</span>}
          title={it.region ? "Области двух сторон разлома — пространство, не порода: объёмы областей не складываются" : "Крылья рассечённой группы: каждое крыло — свой кусок поверхности и тела"}
          testId={`tree-implicit-${it.model}-wings-${it.group ?? "rest"}`}
        />
      );
    }
    const o = it.output;
    const lineId = o.line_layer_id;
    const kindWord = o.region ? "Область крыла разлома" : o.common ? "Общее тело системы жил" : o.kind === "line" ? "Линия пересечения жил" : it.branch === "surface" ? "Поверхность" : "Тело";
    const label = it.level === 5 ? `крыло ${o.wing}${o.region ? " (область)" : ""}` : o.common ? `${groupNameOfOutput(rows, it.model, o.group)}: общее тело` : o.name || `каркас ${o.wireframe_id ?? ""}`;
    if (o.kind === "line") {
      const on = lineId !== null && !hidden.includes(`ln:${lineId}`);
      return (
        <TreeRow
          key={it.key}
          label={label}
          level={it.level}
          selected={chosen === it.key || (lineId !== null && isSelected(selected, "lines", lineId))}
          onSelect={() => {
            setChosen(it.key);
            if (lineId !== null) void run("object.select", { kind: "lines", id: lineId });
          }}
          check={lineId === null ? undefined : { checked: on, label: `${o.name}: показать в 3D`, onChange: (v) => void run("layer.show", { layer: "lines", id: lineId, visible: v }) }}
          title={`${kindWord}: слой линий проекта (редактор стрингов), перезаписывается командой «Обновить модель».${o.length !== null ? ` Длина ${o.length.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м.` : ""}`}
          testId={`tree-implicit-line-${lineId ?? o.name}`}
          glyph={<Glyph icon={icon("lines")} />}
          after={o.length !== null ? <span className={tree.count}>{o.length.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м</span> : undefined}
        />
      );
    }
    const wid = o.wireframe_id;
    return (
      <TreeRow
        key={it.key}
        label={label}
        level={it.level}
        selected={chosen === it.key || (wid !== null && isSelected(selected, "wireframe", wid))}
        onSelect={() => {
          setChosen(it.key);
          if (wid !== null) void run("object.select", { kind: "wireframe", id: wid });
        }}
        onActivate={() => wid !== null && void selectWithProperties(app, "wireframe", wid)}
        title={`${kindWord} модели${o.name ? `: «${o.name}»` : ""}. Обычный каркас проекта, перезаписывается командой «Обновить модель». «В каркас…» делает копию без связи с моделью.${o.region ? "\nОбласть — часть пространства по одну сторону разлома, не порода: объём области в сумму объёмов пород не входит." : ""}${o.common ? "\nОбщее тело — объединение жил: повторяет их объёмы, в сумму не входит." : ""}`}
        testId={`tree-implicit-out-${wid ?? o.name}`}
        glyph={<Glyph icon={icon("wireframe")} />}
        after={o.region ? <span className={tree.count}>область</span> : o.common ? <span className={tree.count}>общее</span> : undefined}
      />
    );
  };

  return (
    <div role="group" aria-label="Неявные модели" data-testid="tree-implicit" data-models={rows.length}>
      {items.map(draw)}
    </div>
  );
}
