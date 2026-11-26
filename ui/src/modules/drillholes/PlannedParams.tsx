/**
 * The tab «Параметры» of the window «Проектная скважина» (ТЗ 7.35 Д-34 ч. 2): the name, the set of holes (the nearest holes and «План / факт» read it),
 * the method of the path (7.11), the collar X, Y, Z (typed, or «Указать на сцене»: a click of the 3D window), the direction — a straight hole, a hole that
 * turns (the change of the azimuth and of the dip per 100 m) or a hole through a target (X, Y, Z of the target; the azimuth and the dip are solved by the
 * core and written into the hole) — and the length. The dip down is negative. The words of a refusal stand under their field (`param` of the core).
 */
import { useState } from "react";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { useApp } from "../../shell/AppContext";
import { loadSets, type SetRow } from "../composites/api";
import { issueAt } from "../estimation/formBase";
import { useLatest, useSel } from "../estimation/hooks";
import mod from "./Planned.module.css";
import { METHODS, METHOD_TITLES, type Method } from "./plannedApi";
import { angleText, lengthText, MODE_HINTS, MODE_TITLES, MODES, RANGES, type Mode } from "./plannedModel";
import { PICK_TITLES, pickStore, startPick, stopPick, type PickWhat } from "./plannedPick";
import { plannedStore, plannedWin, setForm, setXyz } from "./plannedState";

function Under({ at, id }: { at: string | string[]; id: string }) {
  const issue = useSel(plannedWin, (s) => s.issue);
  const hit = issueAt(issue, ...(Array.isArray(at) ? at : [at]));
  if (!hit || hit.reread) return null;
  return (
    <p className={css.error} role="alert" data-testid={`pl-issue-${id}`} data-code={hit.code}>
      {hit.message} {hit.hint}
    </p>
  );
}

function Num({ label, value, onChange, id, at, placeholder, note, disabled }: { label: string; value: string; onChange: (v: string) => void; id: string; at: string; placeholder?: string; note?: string; disabled?: boolean }) {
  const invalid = !!issueAt(plannedWin.get().issue, at);
  return (
    <>
      <label className={css.formRow}>
        <span>{label}</span>
        <input className={css.input} value={value} placeholder={placeholder} inputMode="decimal" spellCheck={false} disabled={disabled} aria-label={label} aria-invalid={invalid || undefined} data-testid={`pl-${id}`} onChange={(e) => onChange(e.target.value)} />
      </label>
      {note && <span className={css.note}>{note}</span>}
      <Under at={at} id={id} />
    </>
  );
}

function Xyz({ which, values, label }: { which: "collar" | "target"; values: readonly string[]; label: string }) {
  const picking = useSel(pickStore, (s) => s.what);
  const note = useSel(pickStore, (s) => s.note);
  const [problem, setProblem] = useState<string | null>(null);
  const on = picking === which;
  const toggle = () => {
    setProblem(null);
    try {
      if (on) stopPick("Выбор точки отменён.");
      else startPick(which as PickWhat, () => plannedWin.get().form[which][2]);
    } catch (e) {
      setProblem(errorText(e));
    }
  };
  return (
    <>
      <div className={mod.xyz} data-testid={`pl-${which}`}>
        {(["X", "Y", "Z"] as const).map((axis, i) => (
          <label key={axis}>
            <span>
              {label} {axis}, м
            </span>
            <input className={css.input} value={values[i]} inputMode="decimal" spellCheck={false} aria-label={`${label} ${axis}`} aria-invalid={!!issueAt(plannedWin.get().issue, which) || undefined} data-testid={`pl-${which}-${axis.toLowerCase()}`} onChange={(e) => setXyz(which, i, e.target.value)} />
          </label>
        ))}
      </div>
      <Under at={which} id={which} />
      <div className={css.row}>
        <button type="button" className={on ? css.primary : css.btn} aria-pressed={on} title={`Щёлкните по 3D-окну: ${PICK_TITLES[which]} встанет на плоскость высоты Z поля (пустое — высота центра вида)`} data-testid={`pl-pick-${which}`} onClick={toggle}>
          {on ? "Отменить выбор" : "Указать на сцене"}
        </button>
        {(on || (note && picking === null)) && (
          <span className={css.note} role="status" data-testid="pl-pick-note">
            {note}
          </span>
        )}
        {problem && (
          <span className={css.error} role="alert" data-testid="pl-pick-error">
            {problem}
          </span>
        )}
      </div>
    </>
  );
}

export function PlannedParams() {
  const app = useApp();
  const s = useSel(plannedWin, (x) => x);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const [problem, setProblem] = useState<string | null>(null);
  const sets = useLatest<SetRow[]>(loadSets, [], (e) => setProblem(errorText(e)));
  const f = s.form;
  const target = f.mode === "target";
  const row = useSel(plannedStore, (x) => (s.id === null ? undefined : x.rows.find((r) => r.id === s.id)));
  const solved = target && row && row.meta.target && !s.dirty ? row : null;
  return (
    <div data-testid="pl-params" data-mode={f.mode}>
      <fieldset className={css.group} disabled={readOnly}>
        <legend>Скважина</legend>
        <Num label="Название" value={f.name} onChange={(v) => setForm({ name: v })} id="name" at="name" placeholder="ПС-1" />
        <label className={css.formRow}>
          <span>Набор скважин</span>
          <select className={css.input} value={f.setId ?? ""} aria-label="Набор скважин" data-testid="pl-set" onChange={(e) => setForm({ setId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{sets ? "— не задан —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
            {f.setId !== null && sets && !sets.some((x) => x.id === f.setId) && <option value={f.setId}>набор №{f.setId} (не найден)</option>}
          </select>
        </label>
        <span className={css.note}>Набор нужен для ближайших скважин у цели и для «План / факт»; в набор проектная скважина не входит и на статистику не влияет.</span>
        <Under at="set_id" id="set" />
        {problem && (
          <p className={css.error} role="alert" data-testid="pl-sets-error">
            {problem}
          </p>
        )}
        <label className={css.formRow}>
          <span>Метод траектории</span>
          <select className={css.input} value={f.method} aria-label="Метод траектории" data-testid="pl-method" onChange={(e) => setForm({ method: e.target.value as Method })}>
            {METHODS.map((m) => (
              <option key={m} value={m}>
                {METHOD_TITLES[m]}
              </option>
            ))}
          </select>
        </label>
        <Under at="method" id="method" />
      </fieldset>
      <fieldset className={css.group} disabled={readOnly} style={{ marginTop: 10 }}>
        <legend>Устье</legend>
        <Xyz which="collar" values={f.collar} label="Устье" />
      </fieldset>
      <fieldset className={css.group} disabled={readOnly} style={{ marginTop: 10 }}>
        <legend>Направление</legend>
        <div className={mod.modes} role="radiogroup" aria-label="Как задано направление">
          {MODES.map((m: Mode) => (
            <label key={m} title={MODE_HINTS[m]}>
              <input type="radio" name="pl-mode" checked={f.mode === m} data-testid={`pl-mode-${m}`} onChange={() => setForm({ mode: m })} />
              {MODE_TITLES[m]}
            </label>
          ))}
        </div>
        <span className={css.note}>{MODE_HINTS[f.mode]}.</span>
        {!target && (
          <>
            <Num label="Азимут, °" value={f.azimuth} onChange={(v) => setForm({ azimuth: v })} id="azimuth" at="azimuth" note={`От севера по часовой стрелке: от ${RANGES.azimuth.lo} до ${RANGES.azimuth.hi} (360 не входит).`} />
            <Num label="Угол, °" value={f.dip} onChange={(v) => setForm({ dip: v })} id="dip" at="dip" note="Вниз — отрицательный: −90 — вертикально вниз, 0 — горизонтально." />
          </>
        )}
        {f.mode === "change" && (
          <div className={mod.pair}>
            <div>
              <Num label="Азимут на 100 м, °" value={f.changeAzimuth} onChange={(v) => setForm({ changeAzimuth: v })} id="change-azimuth" at="change.azimuth" />
            </div>
            <div>
              <Num label="Угол на 100 м, °" value={f.changeDip} onChange={(v) => setForm({ changeDip: v })} id="change-dip" at="change.dip" />
            </div>
            <span className={css.note}>Изменение за каждые 100 м глубины, от −30 до 30°: траектория считается выбранным методом по замерам через 10 м.</span>
          </div>
        )}
        {target && (
          <>
            <Xyz which="target" values={f.target} label="Цель" />
            {solved && (
              <p className={mod.solved} data-testid="pl-solved">
                По устью и цели ядро нашло азимут {angleText(solved.meta.azimuth)} и угол {angleText(solved.meta.dip)}; цель — на глубине {lengthText(solved.target_depth)}.
              </p>
            )}
          </>
        )}
        <Num label="Длина, м" value={f.length} onChange={(v) => setForm({ length: v })} id="length" at="length" placeholder={target ? "до цели" : "250"} note={target ? "Пусто — скважина кончается в цели; длиннее — идёт дальше цели по той же прямой. Короче расстояния до цели нельзя." : `От 0 (не включая) до ${lengthText(RANGES.length.hi)}.`} />
      </fieldset>
    </div>
  );
}
