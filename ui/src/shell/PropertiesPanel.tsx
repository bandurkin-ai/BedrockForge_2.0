/**
 * The «Свойства» tab of the right dock (ТЗ 7.0.3; ADR 0011 §5): the object chosen by `object.select` — in the
 * tree or by a click in the 3D window — shows its panel: a layer of lines, a raster, a wireframe or a DTM (its
 * settings at the top, Д-07), a block model. The card of the selected hole stays below it; without an object it is alone.
 */
import { BlockProps } from "../modules/blocks/BlockProps";
import { ImplicitProps } from "../modules/implicit/ImplicitProps";
import { LayerProps } from "../modules/lines/LayerProps";
import { LineageProps } from "../modules/lineage/LineageProps";
import { ScenarioProps } from "../modules/pit/ScenarioProps";
import { nodeOfSelected } from "../modules/lineage/state";
import { RasterProps } from "../modules/rasters/RasterProps";
import { ReportProps } from "../modules/resources/ReportProps";
import { HoleCard } from "../modules/tables/HoleCard";
import { tablesStore } from "../modules/tables/state";
import { WireframeProps } from "../modules/wireframes/WireframeProps";
import { useApp } from "./AppContext";
import { MultiProps } from "./MultiProps";
import { itemKey, multiStore, selectedItems } from "./selection";
import { useStore } from "./useStore";
import css from "./PropsParts.module.css";

export function PropertiesPanel() {
  const app = useApp();
  const selected = useStore(app.store, (s) => s.selected);
  const multi = useStore(multiStore, (m) => m);
  const hole = useStore(tablesStore, (s) => s.hole !== null);
  if (!selected) return <HoleCard />;
  // several objects chosen in the tree (Ctrl/Shift, Д-44 А5): the fields they share, or the plaque of mixed kinds
  const items = selectedItems(selected, multi);
  if (items.length > 1) return <MultiProps key={items.map(itemKey).join(",")} items={items} />;
  const key = `${selected.kind}:${selected.id}`; // a new object starts with clean fields
  const node = nodeOfSelected(selected); // the origin and the dependents of a computed object (Д-35, ADR 0025)
  return (
    <div className={css.stack}>
      {selected.kind === "lines" && <LayerProps key={key} id={selected.id} />}
      {selected.kind === "raster" && <RasterProps key={key} id={selected.id} />}
      {selected.kind === "wireframe" && <WireframeProps key={key} id={selected.id} />}
      {selected.kind === "blocks" && <BlockProps key={key} id={selected.id} />}
      {selected.kind === "implicit" && <ImplicitProps key={key} id={selected.id} />}
      {selected.kind === "resource_report" && <ReportProps key={key} id={selected.id} />}
      {selected.kind === "pit_scenario" && <ScenarioProps key={key} id={selected.id} />}
      {node && <LineageProps key={`lin:${key}`} node={node} />}
      {hole && <HoleCard />}
    </div>
  );
}
