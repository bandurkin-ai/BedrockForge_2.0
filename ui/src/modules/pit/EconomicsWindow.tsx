/**
 * The window «Экономика карьера» (ТЗ 8.11.2, 8.11.5, вкладка «Карьер» → «Сценарий» → «Экономика»; ADR 0028 решения 3, 6, 29): the elements (the grade field, the
 * unit, the price P, the sales S, the royalty), the density, c_m0, Z_ref, H_гл, c_гл, RF (a list or a range) and the prefix of the fields. «Применить» runs
 * the command `pit.economics` through the registry (the log, the notice, the question of the assistant) and tells what the scenario still needs to run.
 * A wrong form is told under its field in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import { autoDensity } from "../blocks/report/state";
import { blocksStore } from "../blocks/state";
import { DEFAULT_LIMITS } from "./answers";
import { economicsArgs, proposedElements } from "./forms";
import { DensityPart, ElementsPart, MiningPart, PrefixPart, RfPart } from "./EconomicsParts";
import mod from "./Pit.module.css";
import { FormNotice, useFormScenario, useSel, useStaleForm } from "./PitParts";
import { modelFields, pitStore, setEconIssue } from "./state";

export function EconomicsWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(pitStore, (s) => s.econForm);
  const defaults = useSel(pitStore, (s) => s.defaults);
  const scenario = useSel(pitStore, (s) => s.scenario);
  const validate = useSel(pitStore, (s) => s.validate);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const modelId = scenario?.params.model_id ?? null;
  const modelDefaults = useSel(pitStore, (s) => (modelId === null ? null : (s.models[modelId] ?? null)));
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reason = app.registry.reason("pit.economics");
  const target = useFormScenario("econ");
  const stale = useStaleForm("econ");

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const numbers = fields.filter((f) => f.kind === "number");
  const limits = defaults?.limits ?? DEFAULT_LIMITS;
  const props = { form, numbers, disabled: busy, limits, gradeLabels: defaults?.gradeLabels, priceLabels: defaults?.priceLabels };

  const apply = async () => {
    setNote(null);
    const got = economicsArgs(form, target, defaults?.gradeLabels, limits);
    if (!got.ok) return setEconIssue(got.issue);
    setBusy(true);
    try {
      const r = await app.registry.execute("pit.economics", got.args, "user");
      if (!r.ok && !pitStore.get().econIssue) setNote(r.result);
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const model = models.find((m) => m.id === modelId)?.name;
  return (
    <div className={mod.window} data-testid="pit-econ-window">
      <div className={mod.body}>
        <p className={mod.head} data-testid="pit-econ-head">
          {scenario ? `Сценарий «${scenario.name}»` : "Сценарий не выбран"}
          {model ? ` · модель «${model}»` : modelId === null && scenario ? " · модель не выбрана" : ""}
        </p>
        <FormNotice kind="econ" />
        <ElementsPart {...props} proposed={proposedElements(modelDefaults)} />
        <DensityPart {...props} auto={modelDefaults?.density ?? autoDensity(fields)} />
        <MiningPart {...props} zRef={modelDefaults?.z_ref ?? null} />
        <RfPart {...props} />
        <PrefixPart {...props} />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!reason || !scenario || !!stale} title={reason ?? stale ?? (scenario ? "Сохранить экономику в сценарии" : "Выберите или создайте сценарий")} data-testid="pit-econ-apply" onClick={() => void apply()}>
          Применить
        </button>
        {validate && (
          <span className={mod.ready} data-ready={validate.ready} data-testid="pit-ready">
            {validate.ready ? "Сценарий готов к запуску." : `Для запуска не хватает: ${validate.missing[0]?.message ?? "параметров"}`}
          </span>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
