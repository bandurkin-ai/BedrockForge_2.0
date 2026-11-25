/**
 * Step 4 of the wizard «Моделирование» (ТЗ 8.8.1; Д-23): the ellipsoid of the search — from the model of variogram (the structure with the
 * longest range; SIS «модель на порог» — the middle model of the list) or by hand — and the one pass: the multiplier of the radii and the
 * number of samples n_max of a node (the samples and the nodes simulated before, 1–128, 24 by default). A simulation has one pass: the
 * trend is not offered and the node takes what its neighbourhood has (the least number of samples is kept by the core, not applied).
 */
import css from "../../shell/PropsParts.module.css";
import { IssueAt, Triple } from "../estimation/Parts";
import { effectiveFrom, type SearchFrom } from "../estimation/formSearch";
import { useSel } from "../estimation/hooks";
import mod from "../estimation/Wizard.module.css";
import { hasModel } from "./form";
import { setForm, simWizardStore } from "./wizardState";

const SOURCES: [Exclude<SearchFrom, "trend">, string, string][] = [
  ["model", "По модели вариограммы", "Углы и радиусы — у структуры модели с наибольшей дальностью."],
  ["manual", "Вручную", "Углы и три радиуса задаёт человек; r2 и r3 без значения равны r1."],
];

export function StepSearch() {
  const f = useSel(simWizardStore, (s) => s.form);
  const modelChosen = hasModel(f);
  const from = effectiveFrom({ ...f.search, trendId: null }, modelChosen);
  const setSearch = (patch: Partial<typeof f.search>) => setForm({ search: { ...f.search, trendId: null, ...patch } });
  return (
    <>
      <fieldset className={css.group} data-testid="sim-step-4">
        <legend>Эллипсоид поиска</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Источник эллипсоида">
          {SOURCES.map(([k, title, hint]) => (
            <label key={k} title={hint}>
              <input type="radio" name="sim-search-from" checked={from === k} data-testid={`sim-search-from-${k}`} onChange={() => setSearch({ from: k })} />
              {title}
            </label>
          ))}
        </div>
        <span className={css.note}>{SOURCES.find(([k]) => k === from)?.[2]}</span>
        {from === "model" && (
          <p className={css.note} data-testid="sim-search-model">
            {modelChosen ? "Углы и радиусы ядро возьмёт из выбранной модели вариограммы." : "Модель вариограммы не выбрана (шаг «Вариограмма и метод»): выберите её или задайте эллипсоид вручную."}
          </p>
        )}
        {from === "manual" && (
          <>
            <Triple labels={["Угол 1 (азимут), °", "Угол 2 (падение), °", "Угол 3 (поворот), °"]} values={f.search.ang} onChange={(ang) => setSearch({ ang })} testId="sim-ang" />
            <Triple labels={["Радиус r1, м", "Радиус r2, м", "Радиус r3, м"]} values={f.search.r} onChange={(r) => setSearch({ r })} testId="sim-r" />
            <span className={css.note}>Углы — как в вариографии: азимут 0–360°, падение −90…90°, поворот −180…180°; пусто — 0. Радиус r1 обязателен, r2 и r3 без значения равны r1.</span>
          </>
        )}
        <IssueAt at={["search.from", "search.ang1", "search.ang2", "search.ang3", "search.r1", "search.r2", "search.r3"]} id="search" />
      </fieldset>

      <fieldset className={css.group} data-testid="sim-pass">
        <legend>Окрестность узла</legend>
        <div className={mod.passGrid}>
          <label title="Множитель радиусов эллипсоида (0–100)">
            <span>Радиусы ×</span>
            <input className={css.input} value={f.factor} inputMode="decimal" spellCheck={false} aria-label="Множитель радиусов" data-testid="sim-factor" onChange={(e) => setForm({ factor: e.target.value })} />
          </label>
          <label title="Наибольшее число проб и ранее смоделированных узлов в окрестности узла, 1–128">
            <span>n_max</span>
            <input className={css.input} value={f.nMax} inputMode="numeric" spellCheck={false} aria-label="Число проб n_max" data-testid="sim-n-max" onChange={(e) => setForm({ nMax: e.target.value })} />
          </label>
        </div>
        <IssueAt at="search.passes" id="pass" />
        <span className={css.note}>Один проход: узел кригуется по ближайшим пробам и ранее смоделированным узлам в эллипсоиде. Ранее смоделированных узлов — число на шаге «Реализации».</span>
      </fieldset>
    </>
  );
}
