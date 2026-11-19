/**
 * «Свойства» of a wireframe or a DTM (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5). A DTM has its settings at the very
 * top (`DtmProps`); then the colour and visibility, the counts and the extent, «Перенести в ЦТМ» of a
 * wireframe, its checks with «Проверить» and where it was imported from (`ChecksProps`; ТЗ 7.21), the triangle
 * picked in the 3D window — its point, dip and dip direction as the core found them (ADR 0013 §6) — and the
 * attributes of the user: add, change, rename, delete (`wireframe.attr`). A wireframe an operation made has the
 * block «Операция» (`model/BuiltProps.tsx`): the operation, the sources, the parameters, the tolerances,
 * «Перестроить» and «Отчёт» (ТЗ 7.23; ADR 0015 §6).
 */
import { useRef, useState, type KeyboardEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm, askText } from "../../shell/dialogs";
import { Icon } from "../../shell/Icon";
import { NO_COLOR } from "../../shell/props";
import { ColorField, Facts, useRun } from "../../shell/PropsParts";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { DtmProps } from "../dtm/DtmProps";
import { BuiltProps } from "../model/BuiltProps";
import { asBuilt } from "../model/report";
import { pickedObject, sceneObjects } from "../scene/objects";
import { attrNameError, attrValueText, wireframeFacts } from "./attrs";
import { ChecksBlock, SourceBlock } from "./ChecksProps";
import { pickedFacts, pickedNote, pickedTriangle } from "./picked";
import type { WireframeAttr, WireframeInfo } from "./state";
import { wireframeKey } from "./tree";

type Run = (id: string, args?: Record<string, unknown>) => Promise<boolean>;

export function WireframeProps({ id }: { id: number }) {
  const app = useApp();
  const w = useStore(sceneObjects, (o) => o.wireframes.find((x) => x.id === id)) as WireframeInfo | undefined;
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const picked = useStore(pickedObject, (p) => p.picked);
  const { run, error, setError } = useRun();
  if (!w) return null;
  const triangle = pickedTriangle(picked, id);
  const built = asBuilt((w as { built?: unknown }).built); // `objects.meta.built` (`GET /wireframes`)
  return (
    <section className={css.panel} aria-label={`Свойства: ${w.name}`} data-testid={w.dtm ? "props-dtm" : "props-wireframe"}>
      <header className={css.head}>
        <span className={css.kind}>{w.dtm ? "ЦТМ" : "Каркас"}</span>
        <h4 className={css.name} title={w.name}>
          {w.name}
        </h4>
        <button type="button" className={css.btn} onClick={() => void run("object.rename", { kind: "wireframe", id })}>
          Переименовать…
        </button>
      </header>
      {/* Д-07: the settings of a DTM stand at the top of «Свойства» */}
      {w.dtm && <DtmProps id={id} name={w.name} dtm={w.dtm} run={run} />}
      <div className={css.row}>
        <label className={css.check}>
          Цвет
          <ColorField value={w.color ?? NO_COLOR} label="Цвет поверхности" onCommit={(color) => run("object.color", { kind: "wireframe", id, color })} />
        </label>
        <button type="button" className={css.btn} disabled={w.color === null} title="Поверхность рисуется цветом по умолчанию" onClick={() => void run("object.color", { kind: "wireframe", id, color: null })}>
          Без цвета
        </button>
        <label className={css.check}>
          <input type="checkbox" checked={!hidden.includes(wireframeKey(id))} onChange={(e) => void run("layer.show", { layer: "wireframe", id, visible: e.target.checked })} />
          показан в 3D
        </label>
      </div>
      <Facts facts={wireframeFacts(w)} />
      {!w.dtm && (
        <div className={css.row}>
          <button type="button" className={css.btn} title="Топографическая поверхность: растр на рельефе, тени, «Скрыть данные за ЦТМ»" onClick={() => void run("wireframe.dtm", { wireframe_id: id, on: true })}>
            Перенести в ЦТМ
          </button>
        </div>
      )}
      {built && <BuiltProps built={built} target={{ wireframe_id: id }} name={w.name} run={run} />}
      <ChecksBlock id={id} stats={w.stats} run={run} />
      <SourceBlock source={w.source} />
      {triangle ? (
        <fieldset className={css.group} data-testid="props-picked-triangle" data-exact={triangle.exact ? "yes" : triangle.exact === null ? "none" : "pending"}>
          <legend>Треугольник по клику</legend>
          <Facts facts={pickedFacts(triangle)} />
          {pickedNote(triangle) && <p className={css.note}>{pickedNote(triangle)}</p>}
        </fieldset>
      ) : (
        <p className={css.note}>Щёлкните поверхность в 3D-окне — здесь появятся точка и падение треугольника под щелчком.</p>
      )}
      <Attrs key={`${w.id}:${w.version}`} id={id} attrs={w.attrs ?? []} run={run} fail={setError} />
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/** Attributes of the user: a value is saved by Enter or when its field is left. */
function Attrs({ id, attrs, run, fail }: { id: number; attrs: readonly WireframeAttr[]; run: Run; fail: (text: string | null) => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(attrs.map((a) => [a.name, attrValueText(a.value)])));
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const saving = useRef(new Set<string>()); // Enter and the blur that follows it save a value once
  const save = (a: WireframeAttr) => {
    const text = values[a.name] ?? "";
    if (text.trim() === attrValueText(a.value) || saving.current.has(a.name)) return;
    saving.current.add(a.name);
    void run("wireframe.attr", { wireframe_id: id, name: a.name, value: text }).finally(() => saving.current.delete(a.name));
  };
  const rename = async (a: WireframeAttr) => {
    const next = await askText({ title: `Переименовать атрибут «${a.name}»`, label: "Название", initial: a.name, okLabel: "Переименовать" });
    if (next === null || next === a.name) return;
    const bad = attrNameError(next, attrs, a.name);
    if (bad) return fail(bad);
    await run("wireframe.attr", { wireframe_id: id, name: a.name, rename: next });
  };
  const remove = async (a: WireframeAttr) => {
    if (await askConfirm({ title: "Удалить атрибут", message: `Атрибут «${a.name}» удаляется вместе со значением.`, okLabel: "Удалить", danger: true })) await run("wireframe.attr", { wireframe_id: id, name: a.name, remove: true });
  };
  const add = () => {
    const bad = attrNameError(name, attrs);
    if (bad) return fail(bad);
    void run("wireframe.attr", { wireframe_id: id, name, value });
  };
  const onEnter = (action: () => void) => (e: KeyboardEvent) => {
    if (e.key === "Enter") action();
  };
  return (
    <fieldset className={css.group} data-testid="props-user-attrs">
      <legend>Атрибуты</legend>
      {attrs.length === 0 && <p className={css.note}>Атрибутов нет. Значение — текст, число или «да»/«нет».</p>}
      {attrs.length > 0 && (
        <div className={css.attrs}>
          {attrs.map((a) => (
            <div key={a.name} style={{ display: "contents" }}>
              <span className={css.attrName} title={a.name}>
                {a.name}
              </span>
              <input className={css.input} value={values[a.name] ?? ""} placeholder="пусто" aria-label={`Значение атрибута «${a.name}»`} onChange={(e) => setValues((v) => ({ ...v, [a.name]: e.target.value }))} onBlur={() => save(a)} onKeyDown={onEnter(() => save(a))} />
              <button type="button" className={css.iconBtn} aria-label={`Переименовать атрибут «${a.name}»`} title="Переименовать…" onClick={() => void rename(a)}>
                <Icon name="edit" size={13} />
              </button>
              <button type="button" className={css.iconBtn} aria-label={`Удалить атрибут «${a.name}»`} title="Удалить…" onClick={() => void remove(a)}>
                <Icon name="trash" size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className={css.row}>
        <input className={css.input} style={{ flex: 1 }} value={name} placeholder="Название" aria-label="Название нового атрибута" onChange={(e) => setName(e.target.value)} onKeyDown={onEnter(add)} />
        <input className={css.input} style={{ flex: 1.4 }} value={value} placeholder="Значение" aria-label="Значение нового атрибута" onChange={(e) => setValue(e.target.value)} onKeyDown={onEnter(add)} />
        <button type="button" className={css.btn} disabled={!name.trim()} onClick={add}>
          Добавить
        </button>
      </div>
    </fieldset>
  );
}
