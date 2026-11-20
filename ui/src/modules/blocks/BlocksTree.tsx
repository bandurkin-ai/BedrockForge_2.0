/**
 * The section «Блочные модели» of the data tree (ADR 0017 §3): a row per model in its folder, a click selects
 * it (`object.select {kind: "blocks"}`), Enter or a double click opens «Свойства»; the menu is `tree.ts`
 * `blockMenu`. The keys are those of every tree row (WAI-ARIA, `TreeRow`). No check box: no 3D before Э17.
 */
import { useEffect, useMemo } from "react";
import { useApp } from "../../shell/AppContext";
import { FolderRows, Glyph, useRowMenu } from "../../shell/ObjectRows";
import { isSelected } from "../../shell/selection";
import { TreeRow } from "../../shell/TreeRow";
import { selectWithProperties } from "../../shell/treeRun";
import { useStore } from "../../shell/useStore";
import tree from "../../shell/DataTree.module.css";
import { projectStore } from "../projects/state";
import { blocksStore, loadBlocks } from "./state";
import { blockRow, blockSummary } from "./tree";

export function BlocksTree() {
  const app = useApp();
  const list = useStore(blocksStore, (s) => s.list);
  const loaded = useStore(blocksStore, (s) => s.loaded);
  const error = useStore(blocksStore, (s) => s.error);
  const project = useStore(projectStore, (s) => s.current?.uid ?? null);
  const selected = useStore(app.store, (s) => s.selected);
  const menu = useRowMenu();
  useEffect(() => {
    if (project && !loaded) void loadBlocks();
  }, [project, loaded]);
  const items = useMemo(() => list.map((b) => ({ ...b, folder: b.folder ?? null })), [list]);
  if (!items.length) return <div className={`${tree.none} ${error ? tree.error : ""}`}>{error ?? "Нет объектов"}</div>;
  return (
    <div role="group" aria-label="Блочные модели">
      <FolderRows
        items={items}
        row={(b, level) => {
          const row = blockRow(b);
          const local = () => void selectWithProperties(app, "blocks", b.id);
          return (
            <TreeRow
              key={b.id}
              label={b.name}
              ariaLabel={`${b.name}, ${blockSummary(b)}`}
              level={level}
              selected={isSelected(selected, "blocks", b.id)}
              onSelect={() => void app.run("object.select", { kind: "blocks", id: b.id })}
              onActivate={local}
              title={row.title}
              testId={row.testId}
              glyph={<Glyph icon="blocks" />}
              onContextMenu={menu(row.menu, local)}
            />
          );
        }}
      />
    </div>
  );
}
