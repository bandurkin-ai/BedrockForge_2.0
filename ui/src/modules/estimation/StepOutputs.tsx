/**
 * Step 5 of the wizard «Оценка содержаний» (ТЗ 8.8.2; Д-27, Д-29): the output fields — the prefix of the names (AU_OK_) and the keys
 * the method writes: EST, KV, NS, NH, NHY, AD, DMIN, PASS, SLOPE, KE, LAGR, SNW, NOCT; for IK P1…PK and ETYPE. STATUS (0 — оценён,
 * 1 — вне домена, 2 — мало проб, 3 — вырожденная система) is written always and cannot be switched off. The fields of the model that
 * have the names already are told (the core asks «Заменить поля?» at the run). The keys not touched are the core's own (`defaults`).
 */
import css from "../../shell/PropsParts.module.css";
import { blocksStore } from "../blocks/state";
import { STATUS_LABELS } from "./api";
import { ALLOWED_FIELDS, FIELD_TITLES, defaultPrefix, existingFields } from "./formSearch";
import { useSel } from "./hooks";
import { IssueAt } from "./Parts";
import { outputsOf, prefixOf, thresholdCount } from "./summary";
import mod from "./Wizard.module.css";
import { setOutputs, toggleField, wizardStore } from "./wizardState";

export function StepOutputs() {
  const f = useSel(wizardStore, (s) => s.form);
  const data = useSel(wizardStore, (s) => s.defaults.data);
  const details = useSel(blocksStore, (s) => s.details);
  const list = useSel(blocksStore, (s) => s.list);
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? list.find((m) => m.id === f.modelId));
  const { keys, names } = outputsOf(f, data);
  const allowed = (data?.params.outputs as { allowed?: Record<string, string[]> } | undefined)?.allowed?.[f.method] ?? ALLOWED_FIELDS[f.method];
  const have = existingFields((model?.fields ?? []).map((x) => x.name), names);
  const k = thresholdCount(f);
  const prefix = prefixOf(f);
  const typed = f.outputs.fields !== null;

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-5">
        <legend>Префикс полей</legend>
        <label className={css.formRow}>
          <span>Префикс</span>
          <input className={css.input} value={f.outputs.prefix} placeholder={defaultPrefix(f.field ?? "", f.method)} spellCheck={false} aria-label="Префикс выходных полей" data-testid="est-prefix" onChange={(e) => setOutputs({ prefix: e.target.value })} />
        </label>
        <span className={css.note}>Имя поля в блочной модели — префикс и ключ: {prefix}EST. Буквы, цифры и «_», до 40 знаков. Строки плана с одним префиксом пишут в одни поля — у них должны различаться домены.</span>
        <IssueAt at="outputs.prefix" id="prefix" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Что записывается</legend>
        <div className={mod.fields} role="group" aria-label="Выходные поля" data-testid="est-fields">
          {allowed.map((key) => {
            const on = keys.includes(key);
            const always = key === "STATUS";
            return (
              <label key={key} title={FIELD_TITLES[key]}>
                <input type="checkbox" checked={on} disabled={always} data-testid={`est-field-${key}`} onChange={(e) => toggleField(key, e.target.checked, keys)} />
                <code>{key === "P" ? (k ? `P1…P${k}` : "P1…PK") : key}</code>
                <small>{FIELD_TITLES[key]}</small>
              </label>
            );
          })}
        </div>
        <IssueAt at="outputs.fields" id="fields" />
        <div className={mod.links}>
          <button type="button" className={css.btn} disabled={!typed} data-testid="est-fields-reset" title="Вернуть набор полей метода по умолчанию" onClick={() => setOutputs({ fields: null })}>
            По умолчанию для метода
          </button>
        </div>
        <span className={css.note} data-testid="est-status-note">
          STATUS пишется всегда: {STATUS_LABELS.map((w, i) => `${i} — ${w}`).join("; ")}. У блоков со статусом 1–3 остальные поля пусты (не нули).
        </span>
        {names.length > 0 && (
          <p className={css.note} data-testid="est-names">
            В модель запишутся поля: {names.join(", ")}.
          </p>
        )}
        {have.length > 0 && (
          <p className={css.warn} role="status" data-testid="est-fields-exist">
            В модели «{model?.name}» уже есть поля: {have.join(", ")}. При запуске ядро спросит «Заменить поля?»; другой префикс сохранит прежние поля.
          </p>
        )}
      </fieldset>
    </>
  );
}
