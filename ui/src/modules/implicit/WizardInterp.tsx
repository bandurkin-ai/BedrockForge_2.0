/**
 * Step 3 of the wizard «Новая модель» (ТЗ 8.9.2, 8.9.4; Д-25): «Интерполяция» — the kernel (linear, cubic, of the finite radius of
 * Wendland), the smoothing («Вручную» — s, «Авто» — the core picks η for the condition number), the trend (a global or a structural
 * one of the list) or the anisotropy by hand (the angles and the ratios of the axes, the same form as the trend's), the patches of the
 * partition of unity.
 */
import { useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { KERNELS, KERNEL_TITLES, loadTrendChoices, type KernelKind, type SmoothingMode, type TrendChoice } from "./api";
import type { TrendMode } from "./form";
import { useLatest, useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { IssueAt, NumberRow } from "./WizardParts";
import { setForm, wizardStore } from "./wizardState";

const TREND_KIND: Record<string, string> = { global: "глобальный", structural: "структурный" };
const TREND_MODES: readonly [TrendMode, string][] = [
  ["none", "Без анизотропии"],
  ["list", "Тренд из списка"],
  ["manual", "Анизотропия вручную"],
];

export function WizardInterp() {
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const [error, setError] = useState<string | null>(null);
  const trends = useLatest<TrendChoice[]>(loadTrendChoices, [], (e) => setError(errorText(e)));
  const p = defaults?.params;
  const sDefault = p ? `${String(p.s_contact).replace(".", ",")} (контакты), ${String(p.s_grade).replace(".", ",")} (содержания)` : "";
  const known = f.trendId !== null && !!trends?.some((t) => t.id === f.trendId);

  return (
    <>
      <fieldset className={css.group} data-testid="imp-step-3">
        <legend>Ядро</legend>
        <label className={css.formRow}>
          <span>Ядро φ(r)</span>
          <select className={css.input} value={f.kernel} aria-label="Ядро" data-testid="imp-kernel" onChange={(e) => setForm({ kernel: e.target.value as KernelKind })}>
            {KERNELS.map((k) => (
              <option key={k} value={k}>
                {KERNEL_TITLES[k]}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="kernel" id="kernel" />
        {f.kernel === "wendland" && <NumberRow label="Радиус ядра a, м" field="kernelRadius" rangeKey="kernel_radius" at="kernel_radius" id="kernel-radius" placeholder="2 · R участка" note="Пусто — удвоенный радиус участка." />}
      </fieldset>

      <fieldset className={css.group} data-testid="imp-smoothing">
        <legend>Сглаживание</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Сглаживание">
          {(
            [
              ["manual", "Вручную: s задаёт человек"],
              ["auto", "Авто: ядро подбирает η по числу обусловленности (Д-25)"],
            ] as const
          ).map(([mode, title]) => (
            <label key={mode}>
              <input type="radio" name="imp-smoothing" checked={f.smoothing === mode} data-testid={`imp-smoothing-${mode}`} onChange={() => setForm({ smoothing: mode as SmoothingMode })} />
              {title}
            </label>
          ))}
        </div>
        <IssueAt at="smoothing.mode" id="smoothing-mode" />
        {f.smoothing === "manual" ? (
          <NumberRow label="Сглаживание s" field="s" rangeKey="s" at="smoothing.s" id="s" placeholder="по виду группы" note={sDefault ? `Пусто — ${sDefault}.` : undefined} />
        ) : (
          <>
            <NumberRow label="Цель cond" field="cond" rangeKey="cond_target" at="smoothing.cond_target" id="cond" placeholder={p ? String(p.cond_target) : "100000000"} note="Наибольшее число обусловленности блока Φ + ηI; η растёт, пока не достигнет цели." />
            <NumberRow label="Начальное s" field="s" rangeKey="s" at="smoothing.s" id="s" placeholder="по виду группы" note="С него начинается подбор η; если система уже хорошо обусловлена, η не растёт." />
          </>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="imp-trend">
        <legend>Тренд и анизотропия</legend>
        <label className={css.formRow}>
          <span>Анизотропия</span>
          <select className={css.input} value={f.trend} aria-label="Источник анизотропии" data-testid="imp-trend-mode" onChange={(e) => setForm({ trend: e.target.value as TrendMode })}>
            {TREND_MODES.map(([m, t]) => (
              <option key={m} value={m}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {f.trend === "list" && (
          <>
            <label className={css.formRow}>
              <span>Тренд</span>
              <select className={css.input} value={known ? f.trendId! : ""} aria-label="Тренд" data-testid="imp-trend-id" onChange={(e) => setForm({ trendId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">{trends ? (trends.length ? "— выберите тренд —" : "в проекте нет трендов") : "Чтение…"}</option>
                {(trends ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({TREND_KIND[t.kind] ?? t.kind})
                  </option>
                ))}
              </select>
            </label>
            <span className={css.note}>Глобальный тренд — одна матрица анизотропии на модель; структурный — оси у каждого участка по замерам залегания.</span>
            {error && (
              <p className={css.error} role="alert" data-testid="imp-trend-error">
                {error}
              </p>
            )}
            <IssueAt at="trend_id" id="trend" />
          </>
        )}
        {f.trend === "manual" && (
          <>
            <div className={mod.box} data-testid="imp-ang">
              {(["Угол 1 (простирание), °", "Угол 2 (падение), °", "Угол 3 (склонение), °"] as const).map((l, i) => (
                <label key={l}>
                  <span>{l}</span>
                  <input className={css.input} value={f.ang[i]} inputMode="decimal" spellCheck={false} aria-label={l} data-testid={`imp-ang-${i}`} onChange={(e) => setForm({ ang: f.ang.map((v, k) => (k === i ? e.target.value : v)) as [string, string, string] })} />
                </label>
              ))}
            </div>
            <div className={mod.box} data-testid="imp-ratios">
              {(["Ось 1 (главная)", "Отношение r2 = a2 / a1", "Отношение r3 = a3 / a1"] as const).map((l, i) => (
                <label key={l}>
                  <span>{l}</span>
                  <input className={css.input} value={i === 0 ? "1" : f.ratios[i - 1]} disabled={i === 0} inputMode="decimal" spellCheck={false} aria-label={l} data-testid={`imp-ratio-${i}`} onChange={(e) => setForm({ ratios: f.ratios.map((v, k) => (k === i - 1 ? e.target.value : v)) as [string, string] })} />
                </label>
              ))}
            </div>
            <span className={css.note}>Те же углы и отношения осей, что у тренда: поле строится в растянутых координатах A·x.</span>
            <IssueAt at="anisotropy" id="anisotropy" />
          </>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="imp-pou">
        <legend>Участки</legend>
        <NumberRow label="N_уч (точек в участке)" field="nPatch" rangeKey="n_patch" at="pou.n_patch" id="npatch" placeholder={String(p?.n_patch ?? 1000)} />
        <NumberRow label="N_min" field="nMin" rangeKey="n_min" at="pou.n_min" id="nmin" placeholder={String(p?.n_min ?? 100)} note="Наименьшее число точек участка: радиус растёт, пока их не хватит." />
        <NumberRow label="Перекрытие δ" field="delta" rangeKey="delta" at="pou.delta" id="delta" placeholder={String(p?.delta ?? 0.25).replace(".", ",")} />
      </fieldset>
    </>
  );
}
