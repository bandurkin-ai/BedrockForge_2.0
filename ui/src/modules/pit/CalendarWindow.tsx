/**
 * The window «Календарь карьера» (ТЗ 8.11.4, 8.11.5, вкладка «Карьер» → «Сценарий» → «Календарь»; ADR 0028 решения 19, 29): the ore capacity Q_р and the mass capacity Q_гм (t per year;
 * Q_гм empty — no limit) and the discount rate r in percent per year (the API keeps a share: 8 % is 0,08). The calendar only shows the NPV: changing it does not make the result old
 * and needs no new run. «Применить» runs the command `pit.calendar` through the registry (the log, the notice, the question of the assistant). A wrong form is told under its
 * field in Russian before anything is sent. Floating, not modal.
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import { calendarArgs } from "./formsCalendar";
import mod from "./Pit.module.css";
import { Fieldset, FormNotice, Issue, NumberField, useFormScenario, useSel, useStaleForm } from "./PitParts";
import { pitStore, setCalendarForm, setCalendarIssue } from "./state";

export function CalendarWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(pitStore, (s) => s.calendarForm);
  const scenario = useSel(pitStore, (s) => s.scenario);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reason = app.registry.reason("pit.calendar");
  const target = useFormScenario("calendar");
  const stale = useStaleForm("calendar");

  const apply = async () => {
    setNote(null);
    const got = calendarArgs(form, target);
    if (!got.ok) return setCalendarIssue(got.issue);
    setBusy(true);
    try {
      const r = await app.registry.execute("pit.calendar", got.args, "user");
      if (!r.ok && !pitStore.get().calendarIssue) setNote(r.result);
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={mod.window} data-testid="pit-calendar-window">
      <div className={mod.body}>
        <p className={mod.head} data-testid="pit-calendar-head">
          {scenario ? `Сценарий «${scenario.name}»` : "Сценарий не выбран"}
        </p>
        <FormNotice kind="calendar" />
        <Fieldset title="Производительность" disabled={busy}>
          <div className={mod.pair}>
            <NumberField label="Q_р, т/год (руда)" value={form.qOre} onChange={(v) => setCalendarForm({ qOre: v })} placeholder="не задано" testId="pit-q-ore" hint="Производительность по руде; без неё NPV не считается" />
            <NumberField label="Q_гм, т/год (горная масса)" value={form.qMass} onChange={(v) => setCalendarForm({ qMass: v })} placeholder="без ограничения" testId="pit-q-mass" hint="Производительность по горной массе; пусто — без ограничения" />
          </div>
          <Issue which="calendar" field="q_ore" />
          <Issue which="calendar" field="q_mass" />
          <span className={css.note}>Q_р — сколько руды в год перерабатывается, Q_гм — сколько горной массы (руда и пустая порода) в год вынимается; пустое Q_гм — без ограничения.</span>
        </Fieldset>
        <Fieldset title="Ставка" disabled={busy}>
          <NumberField label="r, % в год" value={form.ratePct} onChange={(v) => setCalendarForm({ ratePct: v })} placeholder="8" testId="pit-rate" hint="Ставка дисконтирования для NPV, 0…50 %" />
          <Issue which="calendar" field="rate" />
          <span className={css.note}>Ставка дисконтирования для NPV, по умолчанию 8 % в год.</span>
        </Fieldset>
        <Issue which="calendar" field="general" />
        <span className={css.note} data-testid="pit-calendar-note">
          Календарь нужен только для показа NPV лучшего и худшего случаев: его можно менять без нового запуска, оболочки и результат не пересчитываются.
        </span>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!reason || !scenario || !!stale} title={reason ?? stale ?? (scenario ? "Сохранить календарь в сценарии" : "Выберите или создайте сценарий")} data-testid="pit-calendar-apply" onClick={() => void apply()}>
          Применить
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
