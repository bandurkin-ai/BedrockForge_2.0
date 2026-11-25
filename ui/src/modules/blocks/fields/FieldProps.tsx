/**
 * The tab «Свойства полей» of the window «Поля блочной модели» (ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §7, §9; № 74): the fields
 * of the model chosen — the type, the unit, the description, the role (a number — содержание, плотность, доля объёма; a
 * category — код каркаса, категория), where the field came from, the mark «устарел» (the fields, the filter or the
 * wireframes it was made from changed), «Где используется». «Сохранить» runs `blocks.field.props`, «Перестроить» —
 * `blocks.field.rebuild`, «Удалить» — `blocks.field.delete` through the registry (the log, the notice, the question of the
 * assistant). A field in use or the field of the volume is not deleted — the button says why. The name of a field is not
 * edited (№ 74): the tab says how to get a new name. A refusal of the core stands under the row it is about.
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { useStatus, useWatch } from "../../lineage/hooks";
import { LineageBadge } from "../../lineage/LineageBadge";
import { blocksStore } from "../state";
import type { FieldRow } from "./api";
import { RunView, useSel } from "./CommonParts";
import mod from "./Fields.module.css";
import { editOf, patchOfEdit, refreshProps, rowPlace } from "./propsBind";
import { deleteReason, RENAME_HINT, rolesFor, roleTitle, sourceText, usageLine } from "./propsText";
import { cancelRun } from "./run";
import { fieldsStore, setProps, setPropsEdit } from "./state";

function Row({ f, modelId, locked, onNote }: { f: FieldRow; modelId: number; locked: string | null; onNote: (t: string | null) => void }) {
  const app = useApp();
  const st = useSel(fieldsStore, (s) => s.props);
  const issue = useSel(fieldsStore, (s) => (s.issue?.tab === "props" && s.issue.place === rowPlace(f.name) ? s.issue : null));
  const base = editOf(f);
  const edit = st.edits[f.name] ?? base;
  const patch = patchOfEdit(f, edit);
  const dirty = Object.keys(patch).length > 0;
  const why = deleteReason(f);
  const open = st.usageOpen === f.name;
  // the state of the field in the graph of dependencies (Д-35; the name of a field is in lower case in a node); until it answers — the check of the list
  const node = { kind: "bm_field", id: modelId, sub: f.name.toLowerCase() };
  const status = useStatus(node);
  const marked = status ? status.state !== "deleted" && status.state !== "current" : f.stale;
  const set = (p: Partial<typeof edit>) => setPropsEdit(f.name, base, p);
  const run = async (command: string, args: Record<string, unknown>) => {
    onNote(null);
    const r = await app.registry.execute(command, { model_id: modelId, field: f.name, ...args }, "user");
    if (!r.ok && !fieldsStore.get().issue) onNote(r.result);
  };
  const save = () => {
    const args: Record<string, unknown> = {};
    if ("unit" in patch) args.unit = edit.unit;
    if ("description" in patch) args.description = edit.description;
    if ("role" in patch) args.role = edit.role;
    return run("blocks.field.props", args);
  };
  return (
    <>
      <tr className={`${dirty ? mod.dirty : ""} ${f.stale ? mod.stale : ""}`} data-testid={`props-row-${f.name}`} data-stale={f.stale || undefined} data-dirty={dirty || undefined}>
        <td className={mod.name} title={RENAME_HINT} data-testid={`props-name-${f.name}`}>
          {f.name}
          {marked && (
            <>
              {" "}
              <LineageBadge
                node={node}
                hideCurrent
                fallback={
                  <span className={`${mod.badge} ${mod.staleBadge}`} title={f.staleReason ?? "Поля, из которых считалось, изменились"} data-testid={`props-stale-${f.name}`}>
                    устарел
                  </span>
                }
              />
            </>
          )}
        </td>
        <td data-testid={`props-dtype-${f.name}`}>{f.kind === "category" ? `категория (${f.labelsCount})` : f.dtype}</td>
        <td>
          <input className={css.input} value={edit.unit} aria-label={`Единица поля ${f.name}`} disabled={!!locked} data-testid={`props-unit-${f.name}`} onChange={(e) => set({ unit: e.target.value })} />
        </td>
        <td>
          <input className={css.input} value={edit.description} aria-label={`Описание поля ${f.name}`} disabled={!!locked} data-testid={`props-description-${f.name}`} onChange={(e) => set({ description: e.target.value })} />
        </td>
        <td>
          <select className={css.input} value={edit.role} aria-label={`Роль поля ${f.name}`} disabled={!!locked} data-testid={`props-role-${f.name}`} onChange={(e) => set({ role: e.target.value })}>
            <option value="">без роли</option>
            {rolesFor(f.kind).map((r) => (
              <option key={r} value={r}>
                {roleTitle(r)}
              </option>
            ))}
          </select>
        </td>
        <td className={css.note} data-testid={`props-source-${f.name}`}>
          {sourceText(f)}
        </td>
        <td>
          <button type="button" className={css.btn} aria-expanded={open} title="Где используется поле" data-testid={`props-usage-${f.name}`} onClick={() => setProps({ usageOpen: open ? null : f.name })}>
            {f.usage.length ? `Где используется (${f.usage.length})` : "Нигде не используется"}
          </button>
          {open && f.usage.length > 0 && (
            <ul className={mod.used} data-testid={`props-usage-list-${f.name}`}>
              {f.usage.map((u) => (
                <li key={`${u.kind}:${u.id}:${u.name}:${u.role}`}>{usageLine(u)}</li>
              ))}
            </ul>
          )}
        </td>
        <td className={mod.act}>
          <button type="button" className={css.primary} disabled={!dirty || !!locked} title={locked ?? (dirty ? "Записать единицу, описание и роль поля" : "Нет изменений")} data-testid={`props-save-${f.name}`} onClick={() => void save()}>
            Сохранить
          </button>{" "}
          <button type="button" className={css.btn} disabled={!f.rebuildable || !!locked} title={locked ?? (f.rebuildable ? (f.stale ? "Поле устарело: посчитать заново по тем же параметрам" : "Посчитать поле заново по тем же параметрам") : "Это поле нельзя перестроить: оно из файла импорта, заполнения или регуляризации")} data-testid={`props-rebuild-${f.name}`} onClick={() => void run("blocks.field.rebuild", {})}>
            Перестроить
          </button>{" "}
          <button type="button" className={css.btn} disabled={!!locked} title={locked ?? why ?? "Удалить поле из модели"} aria-disabled={why ? true : undefined} data-testid={`props-delete-${f.name}`} onClick={() => void run("blocks.field.delete", {})}>
            Удалить
          </button>
        </td>
      </tr>
      {issue && (
        <tr data-testid={`props-issue-${f.name}`}>
          <td colSpan={8}>
            <p className={css.error} role="alert">
              Поле «{f.name}»: {issue.message} {issue.hint}
            </p>
          </td>
        </tr>
      )}
    </>
  );
}

export function FieldProps({ winId }: { winId: string }) {
  const app = useApp();
  const st = useSel(fieldsStore, (s) => s.props);
  const modelId = useSel(fieldsStore, (s) => s.modelId);
  const run = useSel(fieldsStore, (s) => s.run);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const models = useSel(blocksStore, (s) => s.list);
  const [note, setNote] = useState<string | null>(null);
  const running = run.phase === "running";
  const list = st.modelId === modelId ? st.list : null;
  const model = useMemo(() => models.find((m) => m.id === modelId), [models, modelId]);
  const locked = readOnly ? "Проект открыт только для чтения" : running ? "Идёт расчёт поля" : null;

  // the list is read when the tab is first seen for the model
  useEffect(() => {
    if (modelId !== null && !list && !st.loading && !st.error) void refreshProps(modelId);
  }, [modelId, list, st.loading, st.error]);

  useWatch(list ? list.fields.map((f) => ({ kind: "bm_field", id: st.modelId ?? 0, sub: f.name.toLowerCase() })) : []); // the whole tab in one request
  const cancel = () => void cancelRun().catch((e: unknown) => setNote(errorText(e)));

  return (
    <>
      <div className={mod.body} data-testid="props-panel">
        {modelId === null && <p className={css.note}>Выберите блочную модель: здесь её поля.</p>}
        {list?.stale && (
          <p className={css.warn} data-testid="props-model-stale">
            Модель «{model?.name ?? list.name}» устарела: {list.staleReason ?? "изменились данные, из которых она получена"}. Пересчитайте её (заполнение, регуляризация) или перестройте поля.
          </p>
        )}
        {st.error && (
          <p className={css.error} role="alert" data-testid="props-error">
            {st.error}
          </p>
        )}
        {st.loading && !list && <p className={css.note}>Читаю поля модели…</p>}
        {list && (
          <div className={mod.tableWrap}>
            <table className={mod.props} data-testid="props-table">
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>Тип</th>
                  <th>Единица</th>
                  <th>Описание</th>
                  <th>Роль</th>
                  <th>Откуда</th>
                  <th>Используется</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.fields.map((f) => (
                  <Row key={f.name} f={f} modelId={list.id} locked={locked} onNote={setNote} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list && !list.fields.length && <p className={css.note}>В модели нет полей.</p>}
        <p className={css.note} data-testid="props-rename-hint">
          {RENAME_HINT}
        </p>
        <RunView tab="props" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="props-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.btn} disabled={modelId === null || st.loading} title="Прочитать поля модели заново" data-testid="props-reload" onClick={() => void refreshProps(modelId)}>
          Перечитать
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job} title="Остановить расчёт: поле записывается целиком или не записывается" data-testid="props-cancel" onClick={cancel}>
            Отменить
          </button>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="fields-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </>
  );
}
