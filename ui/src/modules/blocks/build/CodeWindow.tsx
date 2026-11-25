/**
 * The window «Кодировать каркасами» (ТЗ 8.7.3; ADR 0023 §6, decisions 8, 9): the same coding on a ready model — «по центру»
 * (the field of the code in the same model: `CODE`, and `FRAC` only when asked; the sub-blocks do not change) or «с
 * дроблением» (a new model: the boundary rows are split, the children keep the fields of the parent, `CODE` and `FRAC`).
 * On the left the request — the model, the wireframes with the code and the priority of each, the mode, the way, the names
 * of the fields; on the right the course by the stages and the result. A model that was changed while the window was open
 * answers 409 and the window offers «Перечитать»; a name in use (`field_exists`) or a second field of the share
 * (`frac_exists`) is told under its field. «Кодировать» runs the command `blocks.code` through the registry. Floating,
 * not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { blocksStore } from "../state";
import { MAX_M, MODES, STAGES, type FillResult } from "./api";
import mod from "./Build.module.css";
import { argsOfCode, bodyOfCode, MODE_TITLES } from "./fillForm";
import { Issue, RunPanel, useSel, WireList } from "./Parts";
import { chooseCodeModel } from "./bind";
import { FillResultView } from "./Results";
import { buildStore, setForm, setIssue, withBlocks } from "./state";

export function CodeWindow({ winId }: { winId: string }) {
  const app = useApp();
  const f = useSel(buildStore, (s) => s.forms.code);
  const running = useSel(buildStore, (s) => s.runs.code.phase === "running");
  const models = useSel(blocksStore, (s) => s.list);
  const nameBad = useSel(buildStore, (s) => s.issues.code?.field === "code_field");
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.code");
  const list = useMemo(() => withBlocks(), [models]);
  const chosen = list.find((m) => m.id === f.modelId);
  const set = (patch: Parameters<typeof setForm<"code">>[1]) => setForm("code", patch);

  const build = async () => {
    setNote(null);
    const args = argsOfCode(f);
    const got = bodyOfCode(args, chosen?.version ?? 0);
    if (!got.ok) {
      setIssue("code", got.issue);
      return;
    }
    const r = await app.registry.execute("blocks.code", args, "user");
    if (!r.ok && !buildStore.get().issues.code) setNote(r.result);
  };

  return (
    <div className={mod.window} data-testid="code-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group} disabled={running}>
            <legend>Модель</legend>
            <select className={css.input} value={f.modelId ?? ""} aria-label="Блочная модель" data-testid="code-model" onChange={(e) => chooseCodeModel(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{list.length ? "— выберите модель —" : "нет моделей с блоками"}</option>
              {list.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {chosen && (
              <span className={css.note} data-testid="code-model-info">
                Версия {chosen.version}; полей {chosen.fields.length}.
              </span>
            )}
            <Issue kind="code" field="model" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Каркасы</legend>
            <WireList kind="code" rows={f.wires} onChange={(wires) => set({ wires })} disabled={running} />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Как кодировать</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Способ">
              <label title="Поля той же модели; субблоки не меняются">
                <input type="radio" name="code-way" checked={!f.split} data-testid="code-centre" onChange={() => set({ split: false })} />
                По центру блока (поля той же модели)
              </label>
              <label title="Новая модель: граничные блоки делятся на субблоки">
                <input type="radio" name="code-way" checked={f.split} data-testid="code-split" onChange={() => set({ split: true })} />
                С дроблением (новая модель)
              </label>
            </div>
            <Issue kind="code" field="split" />
            <div className={mod.radios} role="radiogroup" aria-label="Режим">
              {MODES.map((m) => (
                <label key={m}>
                  <input type="radio" name="code-mode" checked={f.mode === m} data-testid={`code-mode-${m}`} onChange={() => set({ mode: m })} />
                  {MODE_TITLES[m]}
                </label>
              ))}
            </div>
            <Issue kind="code" field="mode" />
            <label className={css.field}>
              <span>Код снаружи</span>
              <input className={css.input} value={f.outsideCode} disabled={f.mode === "inside"} aria-label="Код снаружи" data-testid="code-outside" onChange={(e) => set({ outsideCode: e.target.value })} />
            </label>
            <Issue kind="code" field="outside_code" />
            {f.split && (
              <label className={css.field}>
                <span>Точек подсетки m по оси (1–{MAX_M})</span>
                <input className={css.input} value={f.m} inputMode="numeric" aria-label="Точек подсетки" data-testid="code-m" onChange={(e) => set({ m: e.target.value })} />
              </label>
            )}
            <Issue kind="code" field="m" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Поля и имя</legend>
            <label className={css.field}>
              <span>Поле кода</span>
              <input className={css.input} value={f.codeField} aria-label="Поле кода" aria-invalid={nameBad} data-testid="code-code-field" onChange={(e) => set({ codeField: e.target.value })} />
            </label>
            <Issue kind="code" field="code_field" />
            {!f.split && (
              <label className={css.check} title="Доля объёма блока в теле; нужна, если блоки потом считаются по доле">
                <input type="checkbox" checked={f.fracOn} data-testid="code-frac-on" onChange={(e) => set({ fracOn: e.target.checked })} />
                Записать ещё и долю объёма
              </label>
            )}
            {(f.split || f.fracOn) && (
              <label className={css.field}>
                <span>Поле доли объёма</span>
                <input className={css.input} value={f.fracField} aria-label="Поле доли объёма" data-testid="code-frac-field" onChange={(e) => set({ fracField: e.target.value })} />
              </label>
            )}
            <Issue kind="code" field="frac_field" />
            {f.split && (
              <label className={css.field}>
                <span>Имя новой модели</span>
                <input className={css.input} value={f.name} placeholder="‹модель› (кодирование)" aria-label="Имя новой модели" data-testid="code-name" onChange={(e) => set({ name: e.target.value })} />
              </label>
            )}
            <Issue kind="code" field="name" />
            <Issue kind="code" field="general" />
          </fieldset>
        </div>

        <div className={mod.result}>
          <RunPanel<FillResult> kind="code" stages={STAGES.code} idle="Выберите модель и каркасы, нажмите «Кодировать»: ход, коды и объёмы появятся здесь.">
            {(r) => <FillResultView r={r} kind="code" />}
          </RunPanel>
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="code-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || f.modelId === null} title={reason ?? "Закодировать блоки модели каркасами"} data-testid="code-run" onClick={() => void build().catch((e: unknown) => setNote(errorText(e)))}>
          Кодировать
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="code-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
