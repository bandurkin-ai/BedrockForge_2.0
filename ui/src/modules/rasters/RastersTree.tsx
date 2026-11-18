/**
 * The «Растры» section of the data tree (ТЗ 7.19, ADR 0011 §5): the check box shows the raster in 3D
 * (`layer.show {layer: "raster", id}`), a click selects it (`object.select`). The list is `sceneObjects`,
 * shared with the 3D window.
 */
import { useMemo } from "react";
import { useApp } from "../../shell/AppContext";
import { FolderRows, Glyph, useRowMenu } from "../../shell/ObjectRows";
import { isSelected } from "../../shell/selection";
import { TreeRow } from "../../shell/TreeRow";
import { selectWithProperties } from "../../shell/treeRun";
import { useStore } from "../../shell/useStore";
import tree from "../../shell/DataTree.module.css";
import { sceneObjects } from "../scene/objects";
import { rasterKey, rasterMenu, rasterPlace, rasterTitle } from "./tree";

export function RastersTree() {
  const app = useApp();
  const rasters = useStore(sceneObjects, (o) => o.rasters);
  const error = useStore(sceneObjects, (o) => o.error);
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const selected = useStore(app.store, (s) => s.selected);
  const menu = useRowMenu();
  // `/rasters` gives the folder of a raster too; the list of the scene does not declare it
  const items = useMemo(() => rasters.map((r) => ({ ...r, folder: (r as { folder?: string | null }).folder ?? null })), [rasters]);
  if (!rasters.length) return <div className={`${tree.none} ${error ? tree.error : ""}`}>{error ?? "Нет объектов"}</div>;
  return (
    <div role="group" aria-label="Растры">
      <FolderRows
        items={items}
        row={(r, level) => {
          const visible = !hidden.includes(rasterKey(r.id));
          return (
            <TreeRow
              key={r.id}
              label={r.name}
              ariaLabel={`${r.name}, ${rasterPlace(r)}`}
              level={level}
              check={{ checked: visible, label: `Показать «${r.name}» в 3D`, onChange: (v) => void app.run("layer.show", { layer: "raster", id: r.id, visible: v }) }}
              selected={isSelected(selected, "raster", r.id)}
              onSelect={() => void app.run("object.select", { kind: "raster", id: r.id })}
              onActivate={() => void selectWithProperties(app, "raster", r.id)}
              title={rasterTitle(r)}
              testId={`tree-raster-${r.id}`}
              glyph={<Glyph icon={r.vertical ? "section" : "raster"} />}
              onContextMenu={menu(rasterMenu(r, visible), () => void selectWithProperties(app, "raster", r.id))}
            />
          );
        }}
      />
    </div>
  );
}
