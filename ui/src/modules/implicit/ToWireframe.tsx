/**
 * The window «В каркас» (ТЗ 8.9.4; ADR 0026 решение 24): the outputs of the model — surfaces and bodies — and the name of the copy. The copy is an
 * ordinary wireframe of the project without a parent: it stays when the model is rebuilt or deleted and can be edited and counted. «Копировать»
 * runs `implicit.to_wireframe`; the answer of the core (or its refusal) stands in the window.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { copyStore } from "./copyState";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { implicitStore } from "./state";
import { g } from "./WizardParts";

const KIND_TITLES: Record<string, string> = { surface: "поверхность", body: "тело", line: "линия" };

export function ToWireframe({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(copyStore, (x) => x);
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const outs = (info?.outputs ?? []).filter((o) => o.wireframe_id !== null);
  const chosen = outs.find((o) => o.wireframe_id === s.wireframeId) ?? null;
  const reason = readOnly ? "Проект открыт только для чтения" : null;
  const copy = async () => {
    if (s.modelId === null || s.wireframeId === null) return;
    copyStore.set({ busy: true, error: null });
    const r = await app.registry.execute("implicit.to_wireframe", { model_id: s.modelId, wireframe_id: s.wireframeId, ...(s.name.trim() ? { name: s.name.trim() } : {}) }, "user");
    copyStore.set({ busy: false, error: r.ok ? null : r.result });
    if (r.ok) void requestClose(winId);
  };
  return (
    <div className={mod.window} data-testid="implicit-copy">
      <div className={mod.body} style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <div className={mod.page}>
          <fieldset className={css.group}>
            <legend>Выход модели</legend>
            <p className={mod.grid} data-testid="cp-model">
              {info ? `Модель «${info.name}»` : "Чтение модели…"}
            </p>
            <div className={mod.radios} role="radiogroup" aria-label="Выход модели" data-testid="cp-outputs">
              {outs.map((o) => (
                <label key={o.wireframe_id}>
                  <input type="radio" name="cp-output" checked={s.wireframeId === o.wireframe_id} data-testid={`cp-output-${o.wireframe_id}`} onChange={() => copyStore.set({ wireframeId: o.wireframe_id, error: null })} />
                  {o.name} — {KIND_TITLES[o.kind] ?? o.kind}
                  {o.threshold !== null ? `, порог ${g(o.threshold)}` : ""}
                  {o.volume !== null ? `, ${g(o.volume)} м³` : ""}
                </label>
              ))}
              {info && !outs.length && <span className={css.note}>У модели нет каркасов-выходов: постройте её командой «Обновить».</span>}
            </div>
            <label className={css.formRow}>
              <span>Название копии</span>
              <input className={css.input} value={s.name} placeholder={chosen ? `${chosen.name} (копия)` : "по названию выхода"} aria-label="Название копии" data-testid="cp-name" onChange={(e) => copyStore.set({ name: e.target.value, error: null })} />
            </label>
            <span className={css.note}>Копия — обычный каркас проекта без связи с моделью: «Обновить» её не меняет, удаление модели её не удаляет.</span>
            {s.error && (
              <p className={css.error} role="alert" data-testid="cp-error">
                {s.error}
              </p>
            )}
          </fieldset>
        </div>
      </div>
      <div className={mod.footer}>
        <span className={mod.grow} />
        <button type="button" className={css.primary} disabled={s.busy || s.wireframeId === null || !!reason} title={reason ?? "Записать копию выхода обычным каркасом"} data-testid="cp-copy" onClick={() => void copy()}>
          Копировать
        </button>
        <button type="button" className={css.btn} data-testid="cp-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
