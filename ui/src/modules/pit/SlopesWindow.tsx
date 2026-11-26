/**
 * The window «Углы бортов карьера» (ТЗ 8.11.2, 8.11.5, вкладка «Карьер» → «Сценарий» → «Углы бортов»; ADR 0028 решения 4, 9–12, 29): the table of sectors (up to 16), the domains with
 * tables of their own (up to 16), K (auto or 1…20) and the tolerance of the angle error. «Применить» runs the command `pit.slopes` through the registry (the log, the notice, the
 * question of the assistant); «Применить и проверить» then runs `pit.check` (the angle error by azimuth). A wrong form is told under its place in Russian before anything is
 * sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import { blocksStore } from "../blocks/state";
import { DEFAULT_LIMITS } from "./answers";
import { slopesArgs } from "./formsSlopes";
import mod from "./Pit.module.css";
import { FormNotice, Issue, useFormScenario, useSel, useStaleForm } from "./PitParts";
import { DomainsPart, TablePart, TemplatePart } from "./SlopesParts";
import { modelFields, pitStore, setSlopesIssue } from "./state";

export function SlopesWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(pitStore, (s) => s.slopesForm);
  const defaults = useSel(pitStore, (s) => s.defaults);
  const scenario = useSel(pitStore, (s) => s.scenario);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const modelId = scenario?.params.model_id ?? null;
  const modelDefaults = useSel(pitStore, (s) => (modelId === null ? null : (s.models[modelId] ?? null)));
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reason = app.registry.reason("pit.slopes");
  const target = useFormScenario("slopes");
  const stale = useStaleForm("slopes");
  const checkReason = app.registry.reason("pit.check");

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const categories = useMemo(() => {
    const byName = new Map<string, readonly string[]>();
    for (const c of modelDefaults?.categories ?? []) byName.set(c.field, c.labels);
    for (const f of fields) if (f.kind === "category") byName.set(f.name, f.labels.length ? f.labels : (byName.get(f.name) ?? []));
    return [...byName].map(([name, labels]) => ({ name, labels }));
  }, [modelDefaults, fields]);
  const limits = defaults?.limits ?? DEFAULT_LIMITS;

  const apply = async (check: boolean) => {
    setNote(null);
    const got = slopesArgs(form, target, limits);
    if (!got.ok) return setSlopesIssue(got.issue);
    setBusy(true);
    try {
      const r = await app.registry.execute("pit.slopes", got.args, "user");
      if (!r.ok) {
        if (!pitStore.get().slopesIssue) setNote(r.result);
      } else if (check) {
        const c = await app.registry.execute("pit.check", target !== null ? { scenario_id: target } : {}, "user");
        if (!c.ok) setNote(c.result);
      }
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const model = models.find((m) => m.id === modelId)?.name;
  return (
    <div className={mod.window} data-testid="pit-slopes-window">
      <div className={mod.body}>
        <p className={mod.head} data-testid="pit-slopes-head">
          {scenario ? `Сценарий «${scenario.name}»` : "Сценарий не выбран"}
          {model ? ` · модель «${model}»` : modelId === null && scenario ? " · модель не выбрана" : ""}
        </p>
        <FormNotice kind="slopes" />
        <TablePart form={form} disabled={busy} max={limits.sectors} />
        <DomainsPart form={form} disabled={busy} categories={categories} max={limits.domains} maxSectors={limits.sectors} />
        <TemplatePart form={form} disabled={busy} kAuto={modelDefaults?.k_auto ?? null} />
        <Issue which="slopes" field="general" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!reason || !scenario || !!stale} title={reason ?? stale ?? (scenario ? "Сохранить углы бортов в сценарии" : "Выберите или создайте сценарий")} data-testid="pit-slopes-apply" onClick={() => void apply(false)}>
          Применить
        </button>
        <button type="button" className={css.btn} disabled={busy || !!reason || !!checkReason || !scenario || !!stale} title={reason ?? stale ?? checkReason ?? "Сохранить и проверить шаблон: ошибка угла по азимутам"} data-testid="pit-slopes-check" onClick={() => void apply(true)}>
          Применить и проверить
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
