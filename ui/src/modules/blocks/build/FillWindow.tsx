/**
 * The window «Заполнить каркасами» (ТЗ 8.7.3; ADR 0023 §6, decisions 8, 9): on the left the request — the prototype, the
 * wireframes with the code and the priority of each, the mode (inside / outside / both), the levels L of the splitting
 * (by default those of the prototype), the points m of the sub-grid, the code outside, the names of the fields and of the
 * model; on the right the course by the stages «Каркасы / Столбцы / Граничные родители / Дробление / Слияние / Запись»
 * with «Отменить» and the result — the codes with their volumes and the warnings of the core (a wireframe off the grid in
 * the plan, a mode that took no block). «Заполнить» runs the command `blocks.fill` through the registry; a wrong form is
 * told under its field in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { blocksStore } from "../state";
import { MAX_M, MODES, STAGES, type FillResult } from "./api";
import mod from "./Build.module.css";
import { argsOfFill, bodyOfFill, MODE_TITLES } from "./fillForm";
import { countsText } from "./format";
import { Issue, RunPanel, Triple, useSel, WireList } from "./Parts";
import { choosePrototype } from "./bind";
import { FillResultView } from "./Results";
import { buildStore, prototypes, setForm, setIssue } from "./state";

export function FillWindow({ winId }: { winId: string }) {
  const app = useApp();
  const f = useSel(buildStore, (s) => s.forms.fill);
  const running = useSel(buildStore, (s) => s.runs.fill.phase === "running");
  const models = useSel(blocksStore, (s) => s.list);
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.fill");
  const protos = useMemo(() => prototypes(), [models]);
  const chosen = protos.find((m) => m.id === f.prototypeId);
  const set = (patch: Parameters<typeof setForm<"fill">>[1]) => setForm("fill", patch);

  const build = async () => {
    setNote(null);
    const args = argsOfFill(f);
    const got = bodyOfFill(args);
    if (!got.ok) {
      setIssue("fill", got.issue);
      return;
    }
    const r = await app.registry.execute("blocks.fill", args, "user");
    if (!r.ok && !buildStore.get().issues.fill) setNote(r.result);
  };

  return (
    <div className={mod.window} data-testid="fill-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group} disabled={running}>
            <legend>Прототип</legend>
            <select className={css.input} value={f.prototypeId ?? ""} aria-label="Прототип модели" data-testid="fill-prototype" onChange={(e) => choosePrototype(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{protos.length ? "— выберите прототип —" : "нет прототипов: создайте командой «Прототип»"}</option>
              {protos.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {chosen && (
              <span className={css.note} data-testid="fill-prototype-info">
                Блоков {countsText(chosen.grid.counts)}, субблоки по осям {(chosen.grid as { levels?: number[] }).levels?.join("; ") ?? "—"}.
              </span>
            )}
            <Issue kind="fill" field="prototype" />
            <Issue kind="fill" field="model" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Каркасы</legend>
            <WireList kind="fill" rows={f.wires} onChange={(wires) => set({ wires })} disabled={running} />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Режим</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Режим">
              {MODES.map((m) => (
                <label key={m}>
                  <input type="radio" name="fill-mode" checked={f.mode === m} data-testid={`fill-mode-${m}`} onChange={() => set({ mode: m })} />
                  {MODE_TITLES[m]}
                </label>
              ))}
            </div>
            <Issue kind="fill" field="mode" />
            <label className={css.field}>
              <span>Код снаружи</span>
              <input className={css.input} value={f.outsideCode} disabled={f.mode === "inside"} aria-label="Код снаружи" data-testid="fill-outside" onChange={(e) => set({ outsideCode: e.target.value })} />
            </label>
            <Issue kind="fill" field="outside_code" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Дробление</legend>
            <label className={css.check}>
              <input type="checkbox" checked={f.levelsOn} data-testid="fill-levels-on" onChange={(e) => set({ levelsOn: e.target.checked })} />
              Свои уровни L (иначе — как у прототипа)
            </label>
            {f.levelsOn && <Triple labels={["L по X", "L по Y", "L по Z"]} values={f.levels} onChange={(levels) => set({ levels })} testId="fill-levels" />}
            <Issue kind="fill" field="levels" />
            <label className={css.field}>
              <span>Точек подсетки m по оси (1–{MAX_M})</span>
              <input className={css.input} value={f.m} inputMode="numeric" aria-label="Точек подсетки" data-testid="fill-m" onChange={(e) => set({ m: e.target.value })} />
            </label>
            <span className={css.note}>Доля объёма FRAC — доля из m × m × m точек внутри тела; больше m — точнее и дольше.</span>
            <Issue kind="fill" field="m" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Поля и имя</legend>
            <label className={css.field}>
              <span>Поле кода</span>
              <input className={css.input} value={f.codeField} aria-label="Поле кода" data-testid="fill-code-field" onChange={(e) => set({ codeField: e.target.value })} />
            </label>
            <Issue kind="fill" field="code_field" />
            <label className={css.field}>
              <span>Поле доли объёма</span>
              <input className={css.input} value={f.fracField} aria-label="Поле доли объёма" data-testid="fill-frac-field" onChange={(e) => set({ fracField: e.target.value })} />
            </label>
            <Issue kind="fill" field="frac_field" />
            <label className={css.field}>
              <span>Имя модели</span>
              <input className={css.input} value={f.name} placeholder="‹прототип› (заполнение)" aria-label="Имя модели" data-testid="fill-name" onChange={(e) => set({ name: e.target.value })} />
            </label>
            <Issue kind="fill" field="name" />
            <Issue kind="fill" field="general" />
          </fieldset>
        </div>

        <div className={mod.result}>
          <RunPanel<FillResult> kind="fill" stages={STAGES.fill} idle="Выберите прототип и каркасы, нажмите «Заполнить»: ход по стадиям, коды и объёмы появятся здесь.">
            {(r) => <FillResultView r={r} kind="fill" />}
          </RunPanel>
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="fill-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || f.prototypeId === null} title={reason ?? "Заполнить прототип каркасами: новая модель с полями кода и доли объёма"} data-testid="fill-run" onClick={() => void build().catch((e: unknown) => setNote(errorText(e)))}>
          Заполнить
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="fill-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
