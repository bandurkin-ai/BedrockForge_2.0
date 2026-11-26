/**
 * The sections «Модель» and «Глубина рассуждений» of the window «Claude: подключение» (Э23, 23.17; ТЗ 7.32; ADR 0029 решения 13–15, 35): the models of
 * `status.models` with the prices in $ per 1 million tokens (input / output / cache read; the cache write is 1,25 × the input), the five levels of
 * `status.efforts`. A choice goes to the core (`PUT /prefs`) and the status of the answer replaces the old one; the source of the value (`prefs` / `env` /
 * `default`) is said under the list. The unsupported depth of a model is lowered by the core to the nearest one (№ 127) — the window says it after «Проверить связь».
 */
import type { AssistantStatus, EffortInfo, ModelInfo, ModelPrice } from "./wireTypes";
import css from "./Connection.module.css";

/** `4` → «4», `0.2` → «0,20», `0.125` → «0,125»: the prices keep the digits of the reference. */
export function fmtPrice(n: number): string {
  const s = String(+n.toFixed(4));
  if (!s.includes(".")) return s;
  const [int, frac] = s.split(".");
  return `${int},${frac.padEnd(2, "0")}`;
}

/** «вход $4 · выход $20 · чтение кеша $0,20 · запись кеша $5» (per 1 million tokens). */
export function priceLine(p: ModelPrice): string {
  return `вход $${fmtPrice(p.input)} · выход $${fmtPrice(p.output)} · чтение кеша $${fmtPrice(p.cache_read)} · запись кеша $${fmtPrice(p.cache_write)}`;
}

/** «100 тыс.» for a round threshold of the long request. */
export const fmtThreshold = (n: number): string => (n % 1000 === 0 ? `${n / 1000} тыс.` : new Intl.NumberFormat("ru-RU").format(n));

export const EFFORT_TITLES: Record<string, string> = { low: "Быстро", medium: "Обычно", high: "Тщательно", xhigh: "Очень тщательно", max: "Максимум" };
export const effortTitle = (e: EffortInfo): string => e.title || EFFORT_TITLES[e.id] || e.id;

const SOURCE_TEXT: Record<string, string> = {
  prefs: "выбрано в этом окне",
  env: "задано переменной окружения (ASSISTANT_MODEL / ASSISTANT_EFFORT); выбор в окне её заменит",
  default: "по умолчанию",
};

export interface ChoiceProps {
  status: AssistantStatus;
  busy: boolean;
  /** `id` of the model or of the level the user picks */
  onPick: (id: string) => void;
}

function Option({ m, checked, busy, onPick }: { m: ModelInfo; checked: boolean; busy: boolean; onPick: (id: string) => void }) {
  return (
    <button type="button" role="radio" aria-checked={checked} aria-disabled={busy ? true : undefined} className={css.option} data-testid={`model-${m.id}`} onClick={() => !busy && !checked && onPick(m.id)}>
      <span className={css.optionHead}>
        <span className={css.optionTitle}>{m.title}</span>
        <span className={css.optionId}>{m.id}</span>
        {m.default ? <span className={css.badge}>по умолчанию</span> : null}
      </span>
      {m.note ? <span className={css.hint}>{m.note}</span> : null}
      <span className={css.price} data-testid={`price-${m.id}`}>
        {priceLine(m.price)}
      </span>
      {m.long ? (
        <span className={css.price}>
          Запрос длиннее {fmtThreshold(m.long.threshold)} токенов: {priceLine(m.long)}
        </span>
      ) : null}
    </button>
  );
}

export function ModelSection({ status, busy, onPick }: ChoiceProps) {
  return (
    <>
      <div className={css.options} role="radiogroup" aria-label="Модель">
        {status.models.map((m) => (
          <Option key={m.id} m={m} checked={m.id === status.model} busy={busy} onPick={onPick} />
        ))}
      </div>
      <p className={css.hint}>
        Цены — $ за 1 млн токенов; справочник {status.prices_version}. Модель сейчас: {status.model} ({SOURCE_TEXT[status.model_source] ?? status.model_source}).
      </p>
    </>
  );
}

export interface EffortProps extends ChoiceProps {
  /** the levels the model supports by the last «Проверить связь»; null — not known */
  supported: string[] | null;
}

export function EffortSection({ status, busy, supported, onPick }: EffortProps) {
  const lowered = supported ? status.efforts.filter((e) => !supported.includes(e.id)) : [];
  return (
    <>
      <div className={css.efforts} role="radiogroup" aria-label="Глубина рассуждений">
        {status.efforts.map((e) => (
          <button
            key={e.id}
            type="button"
            role="radio"
            aria-checked={e.id === status.effort}
            aria-disabled={busy ? true : undefined}
            className={css.effort}
            data-testid={`effort-${e.id}`}
            title={supported && !supported.includes(e.id) ? "Модель не поддерживает этот уровень: ассистент возьмёт ближайший" : undefined}
            onClick={() => !busy && e.id !== status.effort && onPick(e.id)}
          >
            {effortTitle(e)}
          </button>
        ))}
      </div>
      <p className={css.hint}>
        Чем глубже, тем медленнее и дороже ответ. Сейчас: {effortTitle(status.efforts.find((e) => e.id === status.effort) ?? { id: status.effort, title: "" })} ({SOURCE_TEXT[status.effort_source] ?? status.effort_source}).
      </p>
      {lowered.length > 0 ? (
        <p className={css.warn} data-testid="effort-lowered">
          Выбранная модель не поддерживает: {lowered.map(effortTitle).join(", ")}. Такой уровень ассистент понизит до ближайшего поддерживаемого и скажет об этом в ходе ответа.
        </p>
      ) : null}
    </>
  );
}
