/**
 * The window «Фильтр земли» (ТЗ 7.35 Д-22; ADR 0014 §4, §6): the cloud, the profile «Карьер» / «Равнина» / «Свой»
 * and its numbers — CR, RI, CT, R_б (faces), the noise — typed with a comma or a point. «Запустить» runs the
 * command `ground.classify` with the full parameters: the job is seen in «Задания»; once it is done the classes
 * of the cloud in «Свойства» and in the 3D window are the new ones and the colour mode «Высота над землёй» is
 * there (the core sends `objects.changed`).
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Facts, useRun } from "../../shell/PropsParts";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { cloudLayers, countsText, groundArgs, groundBody, initialGround, lastGround, paramsText, pickCloud, PROFILE_HINTS, PROFILE_TITLES, withProfile, type GroundForm, type ProfileChoice, type ProfileId } from "./ground";

const CHOICES: ProfileChoice[] = ["quarry", "plain", "custom"];

export function GroundWindow({ winId, cloudId }: { winId: string; cloudId?: number }) {
  const app = useApp();
  const files = useStore(sceneObjects, (o) => o.files);
  const [form, setForm] = useState<GroundForm>(() => {
    const selected = app.store.get().selected;
    const all = sceneObjects.get().files;
    return initialGround(all, pickCloud(all, cloudId ?? null, selected?.kind === "lines" ? selected.id : null));
  });
  const { run, error } = useRun();
  const [busy, setBusy] = useState(false);
  const patch = (p: Partial<GroundForm>) => setForm((f) => ({ ...f, ...p }));
  const clouds = cloudLayers(files);
  const cloud = clouds.find((l) => l.id === form.cloudId) ?? null;
  const last = lastGround(cloud);
  const built = groundBody(form);
  const custom = form.profile === "custom";
  const why = !cloud ? "Выберите облако точек." : built.error;

  const start = async () => {
    if (!cloud || !built.body) return;
    setBusy(true);
    if (await run("ground.classify", groundArgs(cloud.id, built.body))) closeWindow(winId);
    else setBusy(false);
  };

  const field = (label: string, key: "cr" | "ri" | "ct" | "rb" | "noiseR" | "noiseK", hint: string, on = true) => (
    <label className={css.formRow} title={hint}>
      <span>{label}</span>
      <input className={css.input} inputMode="decimal" value={form[key]} disabled={!custom || !on} aria-label={label} onChange={(e) => patch({ [key]: e.target.value })} />
    </label>
  );

  return (
    <div className={css.win}>
      <div className={css.winBody}>
        {clouds.length ? (
          <label className={css.formRow}>
            <span>Облако точек</span>
            <select className={css.input} aria-label="Облако точек" value={form.cloudId ?? 0} onChange={(e) => setForm(initialGround(files, Number(e.target.value) || null))}>
              {form.cloudId === null && <option value={0}>— выберите облако —</option>}
              {clouds.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({count(l.point_count)} точек)
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className={css.note}>В проекте нет облаков точек: загрузите LAS или таблицу XYZ («Главная › Загрузка › Облако точек»).</p>
        )}
        {cloud && (
          <Facts
            facts={[
              { label: "Классы сейчас", value: countsText(cloud.cloud?.classes ?? {}) },
              { label: "Последний фильтр", value: last ? paramsText(last) : "не запускался" },
            ]}
          />
        )}
        <fieldset className={css.group} data-testid="ground-profile">
          <legend>Профиль</legend>
          <div className={css.row} role="radiogroup" aria-label="Профиль фильтра">
            {CHOICES.map((c) => (
              <label key={c} className={css.check} title={PROFILE_HINTS[c]}>
                <input type="radio" name={`${winId}-profile`} checked={form.profile === c} onChange={() => setForm((f) => withProfile(f, c))} />
                {PROFILE_TITLES[c]}
              </label>
            ))}
          </div>
          <p className={css.note}>{PROFILE_HINTS[form.profile]}.</p>
          {custom && (
            <label className={css.formRow}>
              <span>На основе профиля</span>
              <select className={css.input} aria-label="На основе профиля" value={form.base} onChange={(e) => patch({ base: e.target.value as ProfileId })}>
                <option value="quarry">Карьер</option>
                <option value="plain">Равнина</option>
              </select>
            </label>
          )}
        </fieldset>
        <fieldset className={css.group} data-testid="ground-params">
          <legend>Параметры</legend>
          {field("Шаг ткани CR, м", "cr", "0,1–50 м: размер ячейки ткани; меньше — подробнее рельеф и дольше расчёт")}
          {field("Жёсткость ткани RI", "ri", "1, 2 или 3: 3 — жёсткая (равнина), 1 — мягкая (крутые склоны)")}
          {field("Порог земли CT, м", "ct", "0,05–5 м: точка ближе к поверхности земли — земля")}
          <label className={css.check} title="Вертикальные грани уступов: земля, если плоскость грани продолжается до земли и выше, и ниже">
            <input type="checkbox" checked={form.faces} disabled={!custom} onChange={(e) => patch({ faces: e.target.checked })} />
            Искать землю у вертикальных граней
          </label>
          {field("Радиус R_б, м", "rb", "1–20 м: земля ищется выше и ниже грани в пределах этого расстояния в плане", form.faces)}
          <label className={css.check} title="Точка, у которой в радиусе меньше соседей, — шум (класс 7)">
            <input type="checkbox" checked={form.noise} disabled={!custom} onChange={(e) => patch({ noise: e.target.checked })} />
            Искать шум (класс 7)
          </label>
          {field("Радиус шума, м", "noiseR", "0,5–20 м", form.noise)}
          {field("Соседей не меньше", "noiseK", "1–20: у точки шума соседей в радиусе меньше этого числа", form.noise)}
          {!custom && <p className={css.note}>Числа профиля. Чтобы изменить их, выберите «Свой».</p>}
        </fieldset>
        <p className={css.note}>Классы точек облака заменяются: 2 — земля, 3–5 — растительность и постройки по высоте над землёй, 1 — прочее, 7 — шум. Появляется режим цвета «Высота над землёй».</p>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{why ?? "Фильтр идёт как задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={!!why || busy} onClick={() => void start()}>
          Запустить
        </button>
      </div>
    </div>
  );
}
