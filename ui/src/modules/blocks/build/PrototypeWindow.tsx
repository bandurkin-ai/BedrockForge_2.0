/**
 * The window «Прототип модели» (ТЗ 8.7.2, 8.7.3; ADR 0023 §6, № 66): on the left the request — the box (the objects or the
 * typed bounds) with the margin, the size of the block, the rotation about Z, the levels of the sub-blocks, «Все родители
 * заняты», the name; on the right the preview the core answers (`POST /blocks/prototype/preview`: nx · ny · nz, the number
 * of parents against the limit 2·10⁹, the size on the disk) and the frame of the grid in the main 3D (`frame.ts`), then
 * the course and the end of the job. «Создать» runs the command `blocks.prototype` through the registry (the log, the
 * notice, the question of the assistant); a wrong form is told under its field in Russian before anything is sent.
 * Floating, not modal.
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { lineLayers, sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { STAGES, MAX_PARENTS, type PrototypeResult } from "./api";
import mod from "./Build.module.css";
import { diskText, int, limitShare, previewLines, prototypeSummary } from "./format";
import { argsOfPrototype, bodyOfPrototype, issueOfPrototype } from "./forms";
import { Issue, RunPanel, Triple, useSel } from "./Parts";
import { requestPreview } from "./bind";
import { buildStore, setForm, setIssue } from "./state";

interface Option {
  id: number;
  name: string;
  kind: string;
}

export function PrototypeWindow({ winId }: { winId: string }) {
  const app = useApp();
  const f = useSel(buildStore, (s) => s.forms.prototype);
  const preview = useSel(buildStore, (s) => s.preview);
  const running = useSel(buildStore, (s) => s.runs.prototype.phase === "running");
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const files = useSel(sceneObjects, (s) => s.files);
  const models = useSel(blocksStore, (s) => s.list);
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.prototype");

  const options = useMemo<Option[]>(
    () => [
      ...wireframes.filter((w) => w.dtm === null).map((w) => ({ id: w.id, name: w.name, kind: "каркас" })),
      ...lineLayers({ files }).map((l) => ({ id: l.id, name: l.name, kind: "линии" })),
      ...models.map((m) => ({ id: m.id, name: m.name, kind: "блочная модель" })),
    ],
    [wireframes, files, models],
  );

  // the preview follows the form: the core is asked, the frame is drawn from its answer
  useEffect(() => {
    requestPreview();
  }, [f]);

  const set = (patch: Parameters<typeof setForm<"prototype">>[1]) => setForm("prototype", patch);
  const toggle = (id: number, on: boolean) => set({ objectIds: on ? [...f.objectIds, id] : f.objectIds.filter((x) => x !== id) });
  const placed = preview.error ? issueOfPrototype(preview.error, f) : null;
  const Placed = ({ field }: { field: string }) =>
    placed?.field === field ? (
      <p className={css.error} role="alert" data-testid={`build-preview-issue-${field}`}>
        {placed.message} {placed.hint}
      </p>
    ) : null;

  const build = async () => {
    setNote(null);
    const args = argsOfPrototype(f);
    const got = bodyOfPrototype(args);
    if (!got.ok) {
      setIssue("prototype", got.issue);
      return;
    }
    const r = await app.registry.execute("blocks.prototype", args, "user");
    if (!r.ok && !buildStore.get().issues.prototype) setNote(r.result);
  };

  const data = preview.phase === "ok" ? preview.data : null;
  const over = data !== null && data.parents > MAX_PARENTS;

  return (
    <div className={mod.window} data-testid="prototype-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group} disabled={running}>
            <legend>Охват</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Охват">
              <label>
                <input type="radio" name="proto-source" checked={f.source === "objects"} data-testid="prototype-source-objects" onChange={() => set({ source: "objects" })} />
                По объектам проекта
              </label>
              <label>
                <input type="radio" name="proto-source" checked={f.source === "extent"} data-testid="prototype-source-extent" onChange={() => set({ source: "extent" })} />
                Границы вручную
              </label>
            </div>
            {f.source === "objects" ? (
              <div className={`${mod.list} ${mod.objects}`} role="group" aria-label="Объекты охвата" data-testid="prototype-objects">
                {options.map((o) => (
                  <label key={o.id} title={`${o.kind} №${o.id}`}>
                    <input type="checkbox" checked={f.objectIds.includes(o.id)} data-testid={`prototype-object-${o.id}`} onChange={(e) => toggle(o.id, e.target.checked)} />
                    <span>
                      {o.name}
                      <span className={mod.unit}>, {o.kind}</span>
                    </span>
                  </label>
                ))}
                {!options.length && <span className={css.note}>В проекте нет объектов: задайте границы вручную</span>}
              </div>
            ) : (
              <>
                <Triple labels={["X мин.", "Y мин.", "Z мин."]} values={f.extent.slice(0, 3)} onChange={(v) => set({ extent: [...v, ...f.extent.slice(3)] })} testId="prototype-extent-min" />
                <Triple labels={["X макс.", "Y макс.", "Z макс."]} values={f.extent.slice(3)} onChange={(v) => set({ extent: [...f.extent.slice(0, 3), ...v] })} testId="prototype-extent-max" />
              </>
            )}
            <Issue kind="prototype" field="objects" />
            <Issue kind="prototype" field="extent" />
            <Placed field="objects" />
            <Placed field="extent" />
            <label className={css.field}>
              <span>Отступ от охвата, м</span>
              <input className={css.input} value={f.pad} spellCheck={false} aria-label="Отступ" data-testid="prototype-pad" onChange={(e) => set({ pad: e.target.value })} />
            </label>
            <span className={css.note}>Одно число или X; Y; Z, например 20; 20; 10.</span>
            <Issue kind="prototype" field="pad" />
            <Placed field="pad" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Блок</legend>
            <Triple labels={["Размер X, м", "Размер Y, м", "Размер Z, м"]} values={f.size} onChange={(size) => set({ size })} testId="prototype-size" invalid={placed?.field === "size"} />
            <Issue kind="prototype" field="size" />
            <Placed field="size" />
            <label className={css.field}>
              <span>Поворот вокруг Z, °</span>
              <input className={css.input} value={f.rotation} inputMode="decimal" spellCheck={false} aria-label="Поворот" data-testid="prototype-rotation" onChange={(e) => set({ rotation: e.target.value })} />
            </label>
            <span className={css.note}>Против часовой стрелки при виде сверху; от −180 (не включая) до 180.</span>
            <Issue kind="prototype" field="rotation" />
            <Placed field="rotation" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Субблоки</legend>
            <Triple labels={["Уровни L по X", "Уровни L по Y", "Уровни L по Z"]} values={f.levels} onChange={(levels) => set({ levels })} testId="prototype-levels" />
            <span className={css.note}>Каждый уровень делит блок пополам по оси: 0 — без дробления, 3 — 8 частей; не больше 7.</span>
            <Issue kind="prototype" field="levels" />
            <Placed field="levels" />
            <label className={css.check} title="В модели будет строка на каждого родителя, даже пока в ней нет данных">
              <input type="checkbox" checked={f.full} data-testid="prototype-full" onChange={(e) => set({ full: e.target.checked })} />
              Все родители заняты
            </label>
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Имя</legend>
            <input className={css.input} value={f.name} aria-label="Имя модели" data-testid="prototype-name" onChange={(e) => set({ name: e.target.value })} />
            <Issue kind="prototype" field="name" />
            <Issue kind="prototype" field="general" />
          </fieldset>
        </div>

        <div className={mod.result}>
          <h4 className={mod.head}>Предпросмотр</h4>
          {preview.phase === "idle" && (
            <p className={css.note} data-testid="prototype-preview-idle">
              Задайте охват и размер блока: здесь появятся число блоков, объём на диске и рамка сетки в 3D.
            </p>
          )}
          {preview.phase === "loading" && (
            <p className={css.note} data-testid="prototype-preview-loading">
              Считаю сетку…
            </p>
          )}
          {preview.phase === "error" && preview.error && (
            <p className={css.error} role="alert" data-testid="prototype-preview-error">
              {preview.error.message} {preview.error.hint}
            </p>
          )}
          {data && (
            <>
              <dl className={mod.facts} data-testid="prototype-preview">
                {previewLines(data).map((l) => (
                  <div key={l.label} style={{ display: "contents" }}>
                    <dt>{l.label}</dt>
                    <dd className={l.label === "Родителей" && over ? mod.over : undefined} data-testid={`prototype-fact-${l.label}`}>
                      {l.value}
                    </dd>
                  </div>
                ))}
              </dl>
              <p className={css.note} data-testid="prototype-limit">
                {over ? `Больше предела ${int(MAX_PARENTS)} родителей: увеличьте блок или сократите охват.` : `Занято ${(limitShare(data.parents) * 100).toFixed(limitShare(data.parents) < 0.01 ? 4 : 1).replace(".", ",")} % предела ${int(MAX_PARENTS)} родителей; на диске ${diskText(data.disk)}.`}
              </p>
              <p className={css.note}>Рамка сетки с линиями родителей показана в 3D-окне.</p>
            </>
          )}
          <RunPanel<PrototypeResult> kind="prototype" stages={STAGES.prototype} idle="Нажмите «Создать»: прототип появится в разделе «Блочные модели» дерева.">
            {(r) => (
              <p className={css.note} data-testid="prototype-done">
                {prototypeSummary(r)}
              </p>
            )}
          </RunPanel>
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="prototype-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || over} title={reason ?? "Создать прототип: модель без строк, её затем заполняют каркасами"} data-testid="prototype-create" onClick={() => void build().catch((e: unknown) => setNote(errorText(e)))}>
          Создать
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="prototype-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
