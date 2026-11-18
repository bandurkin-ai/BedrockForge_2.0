/**
 * The sections «Каркасы» and «ЦТМ» of the data tree (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5 — the minimum of Э7):
 * visibility (`layer.show {layer: "wireframe", id}`), the colour, the sign and the menu item «Перенести в
 * ЦТМ». What a row does and offers is `tree.ts` `wireframeRow`: a DTM has no button at all. The list is
 * `sceneObjects.wireframes`, shared with «Свойства» and the 3D window.
 */
import { useMemo } from "react";
import { useApp } from "../../shell/AppContext";
import { FolderRows, Glyph, RowButtons, Swatch, useRowMenu } from "../../shell/ObjectRows";
import { isSelected } from "../../shell/selection";
import { TreeRow } from "../../shell/TreeRow";
import { selectWithProperties } from "../../shell/treeRun";
import { useStore } from "../../shell/useStore";
import tree from "../../shell/DataTree.module.css";
import { sceneObjects } from "../scene/objects";
import { solids, type WireframeInfo } from "./state";
import { wireframeKey, wireframeRow } from "./tree";

/** Rows of wireframes: `pick` chooses the ones of the section (`solids` or `dtms`). */
export function WireframeRows({ pick, label }: { pick: (list: readonly WireframeInfo[]) => WireframeInfo[]; label: string }) {
  const app = useApp();
  const all = useStore(sceneObjects, (o) => o.wireframes);
  const error = useStore(sceneObjects, (o) => o.error);
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const selected = useStore(app.store, (s) => s.selected);
  const menu = useRowMenu();
  // `/wireframes` gives the folder of a wireframe too; the list of the scene does not declare it
  const list = useMemo(() => pick(all).map((w) => ({ ...w, folder: w.folder ?? null })), [pick, all]);
  if (!list.length) return <div className={`${tree.none} ${error ? tree.error : ""}`}>{error ?? "Нет объектов"}</div>;
  return (
    <div role="group" aria-label={label}>
      <FolderRows
        items={list}
        row={(w, level) => {
          const visible = !hidden.includes(wireframeKey(w.id));
          const row = wireframeRow(w, visible);
          const local = () => void selectWithProperties(app, "wireframe", w.id);
          return (
            <TreeRow
              key={w.id}
              label={w.name}
              level={level}
              check={{ checked: visible, label: `Показать «${w.name}» в 3D`, onChange: (v) => void app.run("layer.show", { layer: "wireframe", id: w.id, visible: v }) }}
              selected={isSelected(selected, "wireframe", w.id)}
              onSelect={() => (row.click === "properties" ? local() : void app.run("object.select", { kind: "wireframe", id: w.id }))}
              onActivate={local}
              title={row.title}
              testId={row.testId}
              glyph={w.dtm ? <Glyph icon="dtm" /> : <Swatch color={w.color} />}
              after={<RowButtons actions={row.buttons} local={local} />}
              onContextMenu={menu(row.menu, local)}
            />
          );
        }}
      />
    </div>
  );
}

export function WireframesTree() {
  return <WireframeRows pick={solids} label="Каркасы" />;
}
