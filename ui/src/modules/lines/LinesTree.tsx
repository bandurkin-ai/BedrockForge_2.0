/**
 * The «Линии» section of the data tree (ТЗ 7.18, ADR 0011 §5): files → layers. The check box of a layer shows
 * it in 3D (`layer.show {layer: "lines", id}`), the one of a file — all its layers; a click selects the layer
 * (`object.select`), the arrow of a file expands it. The lists are `sceneObjects`, shared with the 3D window.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { FolderRows, Swatch, useRowMenu } from "../../shell/ObjectRows";
import { isSelected } from "../../shell/selection";
import { toggleExpanded } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import { count } from "../../shell/treeActions";
import { deleteDialog, renameDialog, selectWithProperties } from "../../shell/treeRun";
import { useStore } from "../../shell/useStore";
import tree from "../../shell/DataTree.module.css";
import { sceneObjects, type LineFileInfo, type LineLayerInfo } from "../scene/objects";
import { deleteLineFile, renameLayer } from "./actions";
import { fileCheck, fileCounts, fileMenu, fileOpen, fileTitle, fileToggle, layerCounts, layerKey, layerMenu, layerTitle, shownLayers } from "./tree";

export function LinesTree() {
  const app = useApp();
  const files = useStore(sceneObjects, (o) => o.files);
  const error = useStore(sceneObjects, (o) => o.error);
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const selected = useStore(app.store, (s) => s.selected);
  const [toggled, setToggled] = useState<string[]>([]);
  const menu = useRowMenu();
  const off = useMemo(() => new Set(hidden), [hidden]); // a file may hold thousands of layers
  if (!files.length) return <div className={`${tree.none} ${error ? tree.error : ""}`}>{error ?? "Нет объектов"}</div>;

  const showFile = async (f: LineFileInfo, visible: boolean) => {
    for (const id of fileToggle(f, app.store.get().view.hidden, visible)) await app.run("layer.show", { layer: "lines", id, visible });
  };
  const removeFile = (f: LineFileInfo) =>
    deleteDialog("Удалить файл линий", `Файл «${f.name}» удаляется из проекта со всеми слоями (${fileCounts(f)}). Отменить удаление нельзя.`, () => deleteLineFile(f.id));

  const layerRow = (l: LineLayerInfo, level: number) => {
    const visible = !off.has(layerKey(l.id));
    return (
      <TreeRow
        key={`l:${l.id}`}
        label={l.name}
        ariaLabel={`${l.name}, ${layerCounts(l)}`}
        level={level}
        check={{ checked: visible, label: `Показать «${l.name}» в 3D`, onChange: (v) => void app.run("layer.show", { layer: "lines", id: l.id, visible: v }) }}
        selected={isSelected(selected, "lines", l.id)}
        onSelect={() => void app.run("object.select", { kind: "lines", id: l.id })}
        onActivate={() => void selectWithProperties(app, "lines", l.id)}
        title={layerTitle(l)}
        testId={`tree-layer-${l.id}`}
        glyph={<Swatch color={l.color} />}
        after={<span className={tree.count}>{count(l.points_only ? l.point_count : l.line_count)}</span>}
        onContextMenu={menu(layerMenu(l, visible), (kind) => {
          if (kind === "properties") void selectWithProperties(app, "lines", l.id);
          else if (kind === "rename") void renameDialog("слой линий", l.name, (name) => renameLayer(l, name));
        })}
      />
    );
  };

  return (
    <div role="group" aria-label="Файлы линий">
      <FolderRows
        items={files}
        row={(f, level) => {
          const open = fileOpen(f, toggled);
          const check = fileCheck(f, hidden);
          const { layers, rest } = shownLayers(f);
          return (
            <div key={`f:${f.id}`}>
              <TreeRow
                label={f.name}
                ariaLabel={`${f.name}, ${fileCounts(f)}`}
                level={level}
                expanded={f.layers.length ? open : undefined}
                onToggle={() => setToggled((t) => toggleExpanded(t, String(f.id)))}
                check={check ? { checked: check.checked, label: `Показать все слои «${f.name}» в 3D`, onChange: (v) => void showFile(f, v) } : undefined}
                title={fileTitle(f)}
                testId={`tree-linefile-${f.id}`}
                after={<span className={tree.count}>{count(f.layers.length)}</span>}
                onContextMenu={menu(fileMenu(), (kind) => kind === "delete" && void removeFile(f))}
              />
              {open && layers.map((l) => layerRow(l, level + 1))}
              {open && rest > 0 && (
                <div className={tree.none} style={{ paddingLeft: 28 + level * 14 }}>
                  … и ещё слоёв: {count(rest)}
                </div>
              )}
            </div>
          );
        }}
      />
    </div>
  );
}
