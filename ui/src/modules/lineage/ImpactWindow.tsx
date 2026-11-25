/**
 * The window «Влияние изменения» (Д-35; ADR 0025 §7, §9): what is built from an object, directly or through others — the numbers
 * by kind and the list, nearest first, each with its state. It opens only when there are dependents (№ 90). Two uses: as a
 * question before a replacement or a deletion (`ask`: «Продолжить» / «Отмена»; the change is not blocked, the person decides)
 * and as a view with «Перестроить с зависимыми»: the plan (dry run) — what is rebuilt in what order, what is left out and why —
 * then «Запустить»; a rebuild that would lose results or columns says so and goes on only after «Перестроить и удалить». The
 * window is its own — never `window.confirm`.
 */
import { useEffect, useRef, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { closeWindow, windowStore } from "../../shell/windows";
import { getImpact, kindTitle, type Impact, type LNode, type LStatus, type Plan } from "./api";
import { usePulled, useSelect, useStatus, useWatch } from "./hooks";
import { abandonAsk, settleAsk } from "./impactAsk";
import { planPhase, runPhase, type Phase } from "./impactFlow";
import { LineageBadge, StateMark } from "./LineageBadge";
import { headLost, jobsOf, planLines, skippedLines } from "./rebuild";
import css from "./Lineage.module.css";

/** How many dependents are listed (the counts are of all; the core gives up to 2000). */
export const LISTED = 200;

const depthText =(d: number): string => (d <= 1 ? "прямой" : `через ${d - 1}`);

export function ImpactCounts({ impact }: { impact: Impact }) {
  const kinds = Object.entries(impact.byKind).sort((a, b) => b[1] - a[1]);
  return (
    <dl className={css.counts} data-testid="lineage-counts">
      {kinds.map(([k, n]) => (
        <div key={k}>
          <dt>{kindTitle(k)}</dt>
          <dd>{n}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DependentList({ impact, limit = LISTED }: { impact: Impact; limit?: number }) {
  const shown = impact.nodes.slice(0, limit);
  useWatch(shown);
  return (
    <ul className={css.list} data-testid="lineage-dependents">
      {shown.map((n) => (
        <li key={`${n.kind}:${n.id}:${n.sub}`} className={css.item}>
          <span className={css.name} title={n.name}>
            {n.name || `№${n.id}`}
            {n.sub ? ` · ${n.sub}` : ""}
          </span>
          <LineageBadge node={n} />
          <span className={css.kind}>
            {kindTitle(n.kind)}, {depthText(n.depth)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function PlanView({ plan }: { plan: Plan }) {
  const lines = planLines(plan);
  const left = skippedLines(plan);
  return (
    <fieldset className={css.group} data-testid="lineage-plan">
      <legend>План перестроения</legend>
      <p className={css.note}>
        Порядок построения: каждый объект — после тех, из которых он построен. Заданий: {jobsOf(plan)}; ход — вкладка «Задания», отмена останавливает остальные.
      </p>
      <ol className={css.chain}>
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ol>
      {left.length > 0 && (
        <>
          <p className={css.warn}>Не будут перестроены (останутся как есть):</p>
          <ul className={css.chain}>
            {left.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
      {headLost(plan) && <p className={css.error}>Источник объекта удалён: перестроить его нельзя. Создайте объект заново из существующих данных.</p>}
    </fieldset>
  );
}

export interface ImpactViewProps {
  node: LNode;
  status: LStatus | undefined;
  impact: Impact | null;
  error: string | null;
  ask: string | null;
  phase: Phase;
  readOnly: boolean;
  onPlan: () => void;
  onRun: (dropLost: boolean) => void;
  onGoOn: () => void;
  onCancel: () => void;
  onClose: () => void;
}

/** The window drawn from what is known (the tests and the connected window). */
export function ImpactView({ node, status, impact, error, ask, phase, readOnly, onPlan, onRun, onGoOn, onCancel, onClose }: ImpactViewProps) {
  const busy = phase.t === "planning" || phase.t === "running";
  const name = status?.name || `№${node.id}`;
  return (
    <div className={css.win} data-testid="lineage-impact-window" role="dialog" aria-label="Влияние изменения">
      <div className={css.body}>
        <h2 className={css.title}>
          {kindTitle(node.kind)} «{name}»{node.sub ? ` · ${node.sub}` : ""} {status && <StateMark status={status} />}
        </h2>
        {error && <p className={css.error}>{error}</p>}
        {!impact && !error && <p className={css.note}>Читаю зависимости…</p>}
        {impact && impact.total === 0 && <p className={css.note}>Из этого объекта ничего не построено: изменение никого не затронет.</p>}
        {impact && impact.total > 0 && (
          <>
            <p data-testid="lineage-lead">
              {ask ? `Вы собираетесь ${ask}. ` : ""}Из этого объекта построено объектов: {impact.total}. После изменения они станут «устарел», пока их не перестроить; изменение не блокируется.
            </p>
            <ImpactCounts impact={impact} />
            <div className={css.scroll}>
              <DependentList impact={impact} />
            </div>
            {impact.nodes.length > LISTED && <p className={css.note}>Показаны первые {LISTED} из {impact.total}.</p>}
            {impact.truncated && <p className={css.note}>Список обрезан ядром: число по видам — по всем зависимым.</p>}
          </>
        )}
        {phase.t === "plan" && <PlanView plan={phase.plan} />}
        {phase.t === "lost" && (
          <fieldset className={css.group} data-testid="lineage-lost">
            <legend>При перестроении пропадут</legend>
            <ul className={css.chain}>
              {phase.lost.map((l) => (
                <li key={`${l.title}:${l.name}`}>
                  {l.title} «{l.name}»
                </li>
              ))}
            </ul>
            <p className={css.warn}>Сохраните их заранее или согласитесь на потерю.</p>
          </fieldset>
        )}
        {phase.t === "planning" && <p className={css.note}>Составляю план…</p>}
        {phase.t === "running" && <p className={css.note} data-testid="lineage-running">{phase.text}</p>}
        {phase.t === "end" && (
          <p className={phase.ok ? css.note : css.error} data-testid="lineage-end">
            {phase.text}
          </p>
        )}
        {phase.t === "error" && (
          <p className={css.error} data-testid="lineage-error">
            {phase.text}
          </p>
        )}
      </div>
      <div className={css.foot}>
        {ask ? (
          <>
            <button type="button" className={css.btn} onClick={onCancel} data-testid="lineage-cancel">
              Отмена
            </button>
            <button type="button" className={css.primary} onClick={onGoOn} autoFocus data-testid="lineage-continue">
              Продолжить
            </button>
          </>
        ) : (
          <>
            {readOnly && <span className={css.grow}>Проект открыт только для чтения.</span>}
            {phase.t === "plan" ? (
              <button type="button" className={css.primary} disabled={busy || readOnly || headLost(phase.plan)} onClick={() => onRun(false)} data-testid="lineage-run">
                Запустить перестроение
              </button>
            ) : phase.t === "lost" ? (
              <button type="button" className={css.primary} disabled={busy || readOnly} onClick={() => onRun(true)} data-testid="lineage-run-lost">
                Перестроить и потерять перечисленное
              </button>
            ) : (
              <button type="button" className={css.primary} disabled={busy || readOnly || !impact || phase.t === "end"} onClick={onPlan} data-testid="lineage-rebuild">
                Перестроить с зависимыми
              </button>
            )}
            <button type="button" className={css.btn} onClick={onClose} data-testid="lineage-close">
              Закрыть
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function ImpactWindow({ winId }: { winId: string }) {
  const app = useApp();
  const win = useSelect(windowStore, (s) => s.windows.find((w) => w.id === winId));
  const readOnly = useSelect(app.store, (s) => s.projectReadOnly);
  const p = win?.props ?? {};
  const node: LNode = { kind: String(p.kind ?? ""), id: Number(p.id ?? 0), sub: typeof p.sub === "string" ? p.sub : "" };
  const ask = typeof p.ask === "string" ? p.ask : null;
  const token = typeof p.token === "number" ? p.token : null;
  const key = `${node.kind}:${node.id}:${node.sub}`;
  const status = useStatus(node);
  const { data: impact, error } = usePulled(() => getImpact(node), key);
  const [phase, setPhase] = useState<Phase>({ t: "idle" });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (token !== null) abandonAsk(token, winId); // the cross is «Отмена»
    };
  }, [token, winId]);
  const put = (next: Phase): void => {
    if (alive.current) setPhase(next);
  };
  const close = () => closeWindow(winId);
  const plan = phase.t === "plan" || phase.t === "lost" ? phase.plan : null;

  const makePlan = async (): Promise<void> => put(await planPhase(node));
  const run = async (dropLost: boolean): Promise<void> => {
    if (plan) put(await runPhase(app, node, plan, dropLost, put));
  };

  return (
    <ImpactView
      node={node}
      status={status}
      impact={impact}
      error={error}
      ask={ask}
      phase={phase}
      readOnly={readOnly}
      onPlan={() => void makePlan()}
      onRun={(d) => void run(d)}
      onGoOn={() => {
        if (token !== null) settleAsk(token, true);
        close();
      }}
      onCancel={() => {
        if (token !== null) settleAsk(token, false);
        close();
      }}
      onClose={close}
    />
  );
}
