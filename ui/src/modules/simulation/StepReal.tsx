/**
 * Step 5 of the wizard «Моделирование» (ТЗ 8.8.1; Д-23): the realizations — the number (1–100, 20 by default), the seed (the same seed gives
 * the same realizations at any number of threads), the nodes simulated before that a node sees (0–64, 12), and the tails of the back
 * transform of the normal scores (as `backtr` of the statistics: linear, power, hyperbolic for the upper one, or none). The categories of
 * SIS have no back transform.
 */
import css from "../../shell/PropsParts.module.css";
import { IssueAt } from "../estimation/Parts";
import { useSel } from "../estimation/hooks";
import { isCategorical, type LowerTail, type UpperTail } from "./form";
import { setForm, simWizardStore } from "./wizardState";

const LOWER: [LowerTail, string][] = [
  ["linear", "линейный к минимуму"],
  ["power", "степенной"],
  ["none", "без хвоста"],
];
const UPPER: [UpperTail, string][] = [
  ["linear", "линейный к максимуму"],
  ["power", "степенной"],
  ["hyperbolic", "гиперболический"],
  ["none", "без хвоста"],
];

export function StepReal() {
  const f = useSel(simWizardStore, (s) => s.form);
  const cat = isCategorical(f);
  const n = f.nscore;
  const setTail = (patch: Partial<typeof n>) => setForm({ nscore: { ...n, ...patch } });
  return (
    <>
      <fieldset className={css.group} data-testid="sim-step-5">
        <legend>Реализации</legend>
        <label className={css.formRow}>
          <span>Число реализаций (1–100)</span>
          <input className={css.input} value={f.nReal} inputMode="numeric" aria-label="Число реализаций" data-testid="sim-n-real" onChange={(e) => setForm({ nReal: e.target.value })} />
        </label>
        <IssueAt at="n_real" id="n-real" />
        <label className={css.formRow}>
          <span>Зерно</span>
          <input className={css.input} value={f.seed} inputMode="numeric" spellCheck={false} aria-label="Зерно" data-testid="sim-seed" onChange={(e) => setForm({ seed: e.target.value })} />
        </label>
        <IssueAt at="seed" id="seed" />
        <label className={css.formRow}>
          <span>Ранее смоделированных узлов (0–64)</span>
          <input className={css.input} value={f.nodes} inputMode="numeric" aria-label="Ранее смоделированных узлов" data-testid="sim-nodes" onChange={(e) => setForm({ nodes: e.target.value })} />
        </label>
        <IssueAt at="nodes" id="nodes" />
        <span className={css.note}>Реализации хранятся отдельным объектом «Моделирование» (в блочную модель не пишутся): сводка и постобработка — кнопки на шаге «Задание». То же зерно даёт те же реализации.</span>
      </fieldset>

      <fieldset className={css.group} data-testid="sim-tails" disabled={cat}>
        <legend>Хвосты обратного преобразования</legend>
        {cat ? (
          <p className={css.note}>У категорий SIS обратного преобразования нет.</p>
        ) : (
          <>
            <label className={css.formRow}>
              <span>Нижний хвост</span>
              <select className={css.input} aria-label="Нижний хвост" data-testid="sim-ltail" value={n.ltail} onChange={(e) => setTail({ ltail: e.target.value as LowerTail })}>
                {LOWER.map(([k, t]) => (
                  <option key={k} value={k}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            {n.ltail === "power" && (
              <label className={css.formRow}>
                <span>Показатель (больше 0)</span>
                <input className={css.input} value={n.ltpar} inputMode="decimal" aria-label="Показатель нижнего хвоста" data-testid="sim-ltpar" onChange={(e) => setTail({ ltpar: e.target.value })} />
              </label>
            )}
            <IssueAt at="nscore.ltpar" id="ltpar" />
            <label className={css.formRow}>
              <span>Верхний хвост</span>
              <select className={css.input} aria-label="Верхний хвост" data-testid="sim-utail" value={n.utail} onChange={(e) => setTail({ utail: e.target.value as UpperTail })}>
                {UPPER.map(([k, t]) => (
                  <option key={k} value={k}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            {(n.utail === "power" || n.utail === "hyperbolic") && (
              <label className={css.formRow}>
                <span>{n.utail === "hyperbolic" ? "Показатель (не меньше 1)" : "Показатель (больше 0)"}</span>
                <input className={css.input} value={n.utpar} inputMode="decimal" aria-label="Показатель верхнего хвоста" data-testid="sim-utpar" onChange={(e) => setTail({ utpar: e.target.value })} />
              </label>
            )}
            <IssueAt at="nscore.utpar" id="utpar" />
            <span className={css.note}>Хвосты за наименьшей и наибольшей пробами: как в обратном преобразовании окна «Вариография». Линейные — по умолчанию.</span>
          </>
        )}
      </fieldset>
    </>
  );
}
