/**
 * The block «Операция» of «Свойства» of an object an operation made (ТЗ 7.23 «в свойствах результата — операция,
 * исходные объекты, параметры, допуски»; ADR 0015 §6 «Запись операции»): the operation and when, the sources
 * (by the fields of its window; a deleted one keeps its name), the parameters as the window names them, the
 * tolerances; «Перестроить» (`model.rebuild` — the same operation on the current sources, a new object) and
 * «Отчёт» (`ReportWindow`). A DTM built in the program shows its record of «Построить ЦТМ».
 */
import { useEffect } from "react";
import { Facts } from "../../shell/PropsParts";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { builtFacts, dtmParamFacts, paramFacts, roleTitle, sourceText, toleranceFacts, type Built, type Names } from "./report";
import { loadOperations, openReport, operationsStore, type RebuildTarget } from "./run";

type Run = (id: string, args?: Record<string, unknown>) => Promise<boolean>;

export function BuiltProps({ built, target, name, run }: { built: Built; target: RebuildTarget; name: string; run: Run }) {
  const list = useStore(operationsStore, (s) => s.list);
  const o = useStore(sceneObjects, (s) => s);
  useEffect(() => void loadOperations().catch(() => undefined), []);
  const spec = list?.find((s) => s.op === built.op);
  const layers = o.files.flatMap((f) => f.layers);
  const names: Names = {
    wireframe: (id) => o.wireframes.find((w) => w.id === id)?.name ?? null,
    layer: (id) => layers.find((l) => l.id === id)?.name ?? null,
  };
  const from = built.rebuild_of === null || built.rebuild_of === undefined ? null : ("wireframe_id" in target ? names.wireframe(built.rebuild_of) : names.layer(built.rebuild_of));
  const params = built.kind === "dtm" ? dtmParamFacts(built.params) : paramFacts(spec, built.params, names);
  const tolerances = toleranceFacts(built.tolerances);
  const sources = built.sources ?? [];
  return (
    <fieldset className={css.group} data-testid="props-built" data-op={built.kind === "dtm" ? "dtm" : (built.op ?? "")}>
      <legend>Операция</legend>
      <Facts facts={builtFacts(built, from)} />
      {sources.length > 0 && (
        <>
          <span className={css.note}>Исходные объекты</span>
          <ul className={css.list} data-testid="props-built-sources">
            {sources.map((s, k) => (
              <li key={`${s.role ?? ""}:${s.id ?? "x"}:${k}`}>
                {built.kind === "operation" ? `${roleTitle(s.role, spec)}: ` : ""}
                {sourceText(s)}
              </li>
            ))}
          </ul>
        </>
      )}
      {params.length > 0 && (
        <div data-testid="props-built-params">
          <span className={css.note}>Параметры</span>
          <Facts facts={params} />
        </div>
      )}
      {!list && built.kind === "operation" && <p className={css.note}>Параметры появятся, когда ядро даст список операций.</p>}
      {tolerances.length > 0 && (
        <div data-testid="props-built-tolerances">
          <span className={css.note}>Допуски</span>
          <Facts facts={tolerances} />
        </div>
      )}
      <div className={css.row}>
        <button type="button" className={css.btn} data-testid="props-rebuild" title="Та же операция с теми же параметрами на текущих исходных; результат — новый объект, этот не меняется" onClick={() => void run("model.rebuild", target as unknown as Record<string, unknown>)}>
          Перестроить
        </button>
        {built.kind === "operation" && built.report && Object.keys(built.report).length > 0 && (
          <button
            type="button"
            className={css.btn}
            data-testid="props-built-report"
            onClick={() =>
              openReport({
                op: built.op ?? "",
                title: built.title ?? "Операция",
                name,
                report: built.report ?? {},
                warnings: [],
                wireframeIds: "wireframe_id" in target ? [target.wireframe_id] : [],
                layerIds: "layer_id" in target ? [target.layer_id] : [],
                volumes: "wireframe_id" in target ? [o.wireframes.find((w) => w.id === target.wireframe_id)?.stats?.volume ?? null] : [],
                rebuildOf: built.rebuild_of ?? null,
              })
            }
          >
            Отчёт
          </button>
        )}
      </div>
    </fieldset>
  );
}
