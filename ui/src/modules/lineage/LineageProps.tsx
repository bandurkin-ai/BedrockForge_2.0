/**
 * The block of «Свойства» for a computed object (Д-35): the pin («Закрепить» — the object is not marked «устарел»; what is built
 * from it counts as current), «Происхождение» and «Зависимые». `node` — what the object is in the graph (`{kind, id, sub?}`;
 * for the selected wireframe `{kind: "wireframe", id}`, for a block model `{kind: "object:block_model", id}`). The panel of an
 * object puts it where its other groups are: `<LineageProps node={...} />`.
 */
import { useApp } from "../../shell/AppContext";
import { Check, useRun } from "../../shell/PropsParts";
import type { LNode } from "./api";
import { argsOf, DependentsPanel } from "./DependentsPanel";
import { useSelect, useStatus } from "./hooks";
import { OriginPanel } from "./OriginPanel";
import css from "./Lineage.module.css";

export function LineageProps({ node }: { node: LNode }) {
  const app = useApp();
  const readOnly = useSelect(app.store, (s) => s.projectReadOnly);
  const status = useStatus(node);
  const { run, error } = useRun();
  return (
    <div className={css.stack} data-testid="lineage-props">
      <fieldset className={css.group}>
        <legend>Зависимости</legend>
        <Check
          checked={status?.state === "pinned"}
          disabled={readOnly || !status || status.state === "deleted"}
          title="Закреплённый объект не помечается «устарел»: он нужен для сравнения версий и выпущенных отчётов. Объекты, построенные из него, считаются актуальными."
          onCommit={(pinned) => run("lineage.pin", { ...argsOf(node), pinned })}
        >
          Закрепить (не помечать «устарел»)
        </Check>
        {error && <p className={css.error}>{error}</p>}
      </fieldset>
      <OriginPanel node={node} />
      <DependentsPanel node={node} />
    </div>
  );
}
