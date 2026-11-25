/**
 * Step 1 of the wizard «Моделирование» (Д-23), above the model and the domain of the estimation's step: the kind — SGS (a continuous
 * variable by normal scores), SIS (thresholds of a continuous variable or the categories — the codes of a lithology), co-SGS (SGS with the
 * collocated secondary variable of the block model). The kind decides what steps 3 and 5 ask.
 */
import css from "../../shell/PropsParts.module.css";
import { IssueAt } from "../estimation/Parts";
import { useSel } from "../estimation/hooks";
import mod from "../estimation/Wizard.module.css";
import { KIND_TITLES, SIM_KINDS, type SimKind } from "./api";
import { setKind, simWizardStore } from "./wizardState";

const NOTES: Record<SimKind, string> = {
  sgs: "Нормальные оценки проб → случайный путь по родителям блочной модели → простой кригинг по пробам и ранее смоделированным узлам → обратное преобразование. Нужна модель вариограммы в нормальных оценках.",
  sis: "Индикаторы по порогам непрерывной величины или по категориям (коды литологии): вероятности в узле → значение или категория по случайному числу. Модели — в единицах поля, своя на порог или одна медианная.",
  cosgs: "SGS, где в узле добавляется значение вторичной переменной блочной модели (плотность): корреляция нормальных оценок ρ задаётся или считается по пробам; ρ = 0 даёт SGS.",
};

export function StepKind() {
  const kind = useSel(simWizardStore, (s) => s.form.kind);
  return (
    <fieldset className={css.group} data-testid="sim-step-1">
      <legend>Вид моделирования</legend>
      <div className={mod.radios} role="radiogroup" aria-label="Вид моделирования">
        {SIM_KINDS.map((k) => (
          <label key={k}>
            <input type="radio" name="sim-kind" checked={kind === k} data-testid={`sim-kind-${k}`} onChange={() => setKind(k)} />
            {KIND_TITLES[k]}
          </label>
        ))}
      </div>
      <span className={css.note} data-testid="sim-kind-note">
        {NOTES[kind]}
      </span>
      <IssueAt at="kind" id="kind" />
    </fieldset>
  );
}
