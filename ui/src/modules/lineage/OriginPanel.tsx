/**
 * «Происхождение» in «Свойства» of a computed object (Д-35; ADR 0025 §10): the state with its chain of causes, then the inputs
 * the object was built from — each with its own state and the version it had when the object was built and has now («изменён»
 * when they differ). The panel follows the events: after a rebuild or an edit of a source it reads again.
 */
import { chainOf, getOrigin, kindTitle, type LNode, type LStatus, type Origin, type OriginInput } from "./api";
import { usePulled, useStatus, useWatch } from "./hooks";
import { StateMark } from "./LineageBadge";
import css from "./Lineage.module.css";

/** A version of an input for people: the first 14 characters of the fingerprint. */
export const shortFp = (fp: string | null): string => (fp === null ? "—" : fp.length > 14 ? `${fp.slice(0, 14)}…` : fp);

function Input({ input }: { input: OriginInput }) {
  const changed = input.fp !== null && input.fpNow !== null && input.fp !== input.fpNow;
  const gone = input.fpNow === null;
  return (
    <li className={css.item} data-testid={`lineage-input-${input.kind}-${input.id}`}>
      <span className={css.name} title={input.name}>
        {input.name || `№${input.id}`}
        {input.sub ? ` · ${input.sub}` : ""}
      </span>
      <StateMark status={{ state: input.state, reason: input.reason, changed: false }} />
      <span className={css.kind}>{kindTitle(input.kind)}</span>
      <span className={css.fp} title={`Версия входа при построении: ${input.fp ?? "не записана"}; сейчас: ${input.fpNow ?? "входа нет"}`}>
        {gone ? "входа нет" : changed ? `было ${shortFp(input.fp)} → стало ${shortFp(input.fpNow)}` : `версия ${shortFp(input.fpNow)}`}
      </span>
    </li>
  );
}

/** The panel drawn from what is known (the tests and the connected panel). */
export function OriginView({ status, origin, error }: { status?: LStatus; origin: Origin | null; error?: string | null }) {
  const chain = chainOf(status?.reason ?? origin?.node.reason ?? null);
  return (
    <fieldset className={css.group} data-testid="lineage-origin">
      <legend>Происхождение</legend>
      {status && (
        <div className={css.row}>
          <StateMark status={status} />
        </div>
      )}
      {chain.length > 0 && (
        <ul className={css.chain} data-testid="lineage-chain">
          {chain.map((c, i) => (
            <li key={`${i}:${c}`}>{i === 0 ? c : `← ${c}`}</li>
          ))}
        </ul>
      )}
      {error && <p className={css.error}>{error}</p>}
      {origin && origin.inputs.length === 0 && <p className={css.note}>Объект не построен из других объектов проекта.</p>}
      {origin && origin.inputs.length > 0 && (
        <ul className={css.list}>
          {origin.inputs.map((i) => (
            <Input key={`${i.kind}:${i.id}:${i.sub}`} input={i} />
          ))}
        </ul>
      )}
    </fieldset>
  );
}

export function OriginPanel({ node }: { node: LNode }) {
  const status = useStatus(node);
  const key = `${node.kind}:${node.id}:${node.sub ?? ""}`;
  const { data, error } = usePulled(() => getOrigin(node), key);
  useWatch(data?.inputs ?? []);
  return <OriginView status={status} origin={data} error={error} />;
}
