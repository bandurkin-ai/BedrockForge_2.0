/**
 * «Зависимые» in «Свойства» (Д-35; ADR 0025 §7): what is built from the object — the number by kind and the nearest few; the
 * buttons open «Влияние изменения» and run «Перестроить с зависимыми» (the commands of the registry, so the log, the keys and
 * the assistant see the same). A panel of an object nothing is built from says so.
 */
import { useApp } from "../../shell/AppContext";
import { useRun } from "../../shell/PropsParts";
import { getImpact, kindTitle, type Impact, type LNode } from "./api";
import { usePulled, useSelect, useWatch } from "./hooks";
import { LineageBadge } from "./LineageBadge";
import { ImpactCounts } from "./ImpactWindow";
import css from "./Lineage.module.css";

/** How many dependents the panel lists (the window lists more). */
export const PANEL_LISTED = 8;

export const argsOf = (n: LNode): Record<string, unknown> => ({ kind: n.kind, id: n.id, ...(n.sub ? { sub: n.sub } : {}) });

/** The panel drawn from what is known (the tests and the connected panel). */
export function DependentsView({ impact, error, readOnly, runError, onImpact, onRebuild }: { impact: Impact | null; error?: string | null; readOnly: boolean; runError?: string | null; onImpact: () => void; onRebuild: () => void }) {
  const shown = impact?.nodes.slice(0, PANEL_LISTED) ?? [];
  useWatch(shown);
  return (
    <fieldset className={css.group} data-testid="lineage-dependents-panel">
      <legend>Зависимые</legend>
      {error && <p className={css.error}>{error}</p>}
      {impact && impact.total === 0 && <p className={css.note}>Из этого объекта ничего не построено.</p>}
      {impact && impact.total > 0 && (
        <>
          <p className={css.note}>Построено из объекта: {impact.total}</p>
          <ImpactCounts impact={impact} />
          <ul className={css.list}>
            {shown.map((n) => (
              <li key={`${n.kind}:${n.id}:${n.sub}`} className={css.item}>
                <span className={css.name} title={n.name}>
                  {n.name || `№${n.id}`}
                  {n.sub ? ` · ${n.sub}` : ""}
                </span>
                <LineageBadge node={n} />
                <span className={css.kind}>{kindTitle(n.kind)}</span>
              </li>
            ))}
          </ul>
          {impact.total > shown.length && <p className={css.note}>…и ещё {impact.total - shown.length}</p>}
        </>
      )}
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={!impact || impact.total === 0} onClick={onImpact} data-testid="lineage-open-impact">
          Влияние изменения…
        </button>
        <button type="button" className={css.btn} disabled={readOnly || !impact} onClick={onRebuild} data-testid="lineage-rebuild-panel">
          Перестроить с зависимыми
        </button>
      </div>
      {runError && <p className={css.error}>{runError}</p>}
    </fieldset>
  );
}

export function DependentsPanel({ node }: { node: LNode }) {
  const app = useApp();
  const readOnly = useSelect(app.store, (s) => s.projectReadOnly);
  const key = `${node.kind}:${node.id}:${node.sub ?? ""}`;
  const { data, error } = usePulled(() => getImpact(node), key);
  const { run, error: runError } = useRun();
  return <DependentsView impact={data} error={error} readOnly={readOnly} runError={runError} onImpact={() => void run("lineage.impact", argsOf(node))} onRebuild={() => void run("lineage.rebuild", argsOf(node))} />;
}
