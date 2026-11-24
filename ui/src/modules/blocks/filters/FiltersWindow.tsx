/**
 * The window «Фильтры · <проект>» (ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8): the library of the project on the left (a click —
 * the filter on edit; «Новый», «Копировать», «Удалить…»), the filter on the right: the name, the note, the rules in the
 * constructor («поле — оператор — значение», И / ИЛИ / НЕ, expressions, wireframes, surfaces) or as the text of 8.7.4
 * (the same filter without loss; a wireframe or a surface has no text), «Проверить на модели» (how many blocks pass),
 * «Где используется». «Сохранить», «Удалить…», «Где используется» and the check run the commands `filter.*` through the
 * registry (the log, the notice, the question of the assistant); a version taken by another window (409) is asked in
 * our own window (`bind.ts`). Floating, not modal; a window with changes asks before it closes.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useApp } from "../../../shell/AppContext";
import { askConfirm } from "../../../shell/dialogs";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import type { Store } from "../../../shell/store";
import { closeWindow, setCloseGuard } from "../../../shell/windows";
import { sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { copyWindow, saveWindow, testWindow } from "./bind";
import { fieldsOfRule } from "./constructor";
import { parseExpr } from "./expr";
import { RuleEditor, type ObjectOption } from "./RuleEditor";
import mod from "./Filters.module.css";
import { currentEdit, filtersByName, filtersStore, forgetEdit, rulesOfEdit, setEdit, switchMode, type FilterEdit } from "./state";

function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

export const CLOSE_QUESTION = { title: "Фильтры", message: "Закрыть окно без сохранения изменений фильтра?", okLabel: "Закрыть", danger: true };

/** «Заменить правки?» — a filter put on edit over a changed form. */
export const REPLACE_QUESTION = { title: "Фильтры", message: "В окне есть несохранённые изменения фильтра. Открыть другой фильтр без сохранения?", okLabel: "Открыть без сохранения", danger: true };

export function FiltersWindow({ winId }: { winId: string }) {
  const app = useApp();
  const edit = useSel(filtersStore, (s) => s.edit);
  const rows = useSel(filtersStore, (s) => s.rows);
  const loaded = useSel(filtersStore, (s) => s.loaded);
  const models = useSel(blocksStore, (s) => s.list);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const saveReason = app.registry.reason("filter.save");

  useEffect(() => {
    setCloseGuard(winId, async () => !currentEdit()?.dirty || (await askConfirm(CLOSE_QUESTION)));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const options = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  const fields = useMemo(() => {
    const model = edit?.modelId != null ? models.find((m) => m.id === edit.modelId) : undefined;
    const list = model ? model.fields : models.flatMap((m) => m.fields);
    return [...new Set(list.map((f) => f.name))];
  }, [models, edit?.modelId]);
  const sorted = useMemo(() => filtersByName(rows), [rows]);

  if (!edit) return <div className={css.panel}>Окно фильтров не готово.</div>;

  const act = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setNote(null);
    try {
      const out = await fn();
      if (out) setNote({ text: out, error: true });
    } catch (e) {
      setNote({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };
  const patch = (p: Partial<FilterEdit>) => setEdit({ dirty: true, ...p });
  const checked = rulesOfEdit(edit);
  const reading = checked.ok ? fieldsOfRule(checked.rule) : [];
  const stored = edit.filterId !== null;

  const open = (id: number | null) =>
    act(async () => {
      if (edit.dirty && !(await askConfirm(REPLACE_QUESTION))) return null;
      await (id === null ? app.run("filter.save", {}) : app.run("filters.manage", { filter_id: id }));
      return null;
    });
  const mode = (m: "builder" | "text") => {
    const r = switchMode(edit, m);
    if ("refusal" in r) setNote({ text: r.refusal, error: true });
    else {
      setNote(null);
      setEdit(r);
    }
  };
  const typeText = (text: string) => {
    const p = parseExpr(text);
    patch({ text, textIssue: p.ok ? null : p.issue });
  };
  const test = () =>
    act(async () => {
      const t = await testWindow(app);
      setEdit({ test: t });
      return null;
    });
  const run = (id: string) =>
    act(async () => {
      if (edit.filterId === null) return null;
      const r = await app.registry.execute(id, { filter_id: edit.filterId }, "user");
      return r.ok ? null : r.result;
    });

  return (
    <div className={mod.window} data-testid="filters-window">
      <div className={mod.layout}>
        <div className={mod.left}>
          <div className={css.row}>
            <button type="button" className={css.btn} disabled={busy || !!saveReason} title={saveReason ?? "Новый фильтр: пустое дерево правил"} data-testid="filters-new" onClick={() => void open(null)}>
              Новый
            </button>
            <button type="button" className={css.btn} disabled={busy || !!saveReason || !checked.ok} title={saveReason ?? "Сохранить правила этого окна как новый фильтр с именем «… (2)»"} data-testid="filters-copy" onClick={() => void act(() => copyWindow(app))}>
              Копировать
            </button>
          </div>
          <ul className={mod.list} aria-label="Фильтры проекта" data-testid="filters-list">
            {sorted.map((f) => (
              <li key={f.id}>
                <button type="button" className={`${mod.item} ${edit.filterId === f.id ? mod.on : ""}`} title={f.title} data-testid={`filters-item-${f.id}`} onClick={() => void open(f.id)}>
                  <span className={mod.itemName}>{f.name}</span>
                  <span className={mod.itemSub}>{f.title}</span>
                </button>
              </li>
            ))}
            {!sorted.length && <li className={mod.empty}>{loaded ? "Фильтров нет — «Новый» создаёт первый" : "Читаю список…"}</li>}
          </ul>
        </div>
        <div className={mod.right}>
          <div className={css.row}>
            <label className={css.field} style={{ flex: 2 }}>
              <span>Имя</span>
              <input className={css.input} value={edit.name} data-testid="filters-name" onChange={(e) => patch({ name: e.target.value })} />
            </label>
            <label className={css.field} style={{ flex: 3 }}>
              <span>Примечание</span>
              <input className={css.input} value={edit.note} data-testid="filters-note" onChange={(e) => patch({ note: e.target.value })} />
            </label>
          </div>
          <p className={css.note} data-testid="filters-state">
            {stored ? `Фильтр №${edit.filterId}, версия ${edit.version}` : "Новый фильтр — ещё не сохранён"}
            {edit.dirty ? " · есть изменения" : ""}
          </p>
          {edit.lost.length > 0 && (
            <p className={css.warn} data-testid="filters-lost">
              Объекты фильтра больше не в проекте: {edit.lost.join(", ")}. Они недоступны, их доля 0 — выберите другие или удалите правила.
            </p>
          )}
          <div className={mod.tabs} role="tablist">
            <button type="button" role="tab" aria-selected={edit.mode === "builder"} className={`${mod.tab} ${edit.mode === "builder" ? mod.tabOn : ""}`} data-testid="filters-mode-builder" onClick={() => mode("builder")}>
              Конструктор
            </button>
            <button type="button" role="tab" aria-selected={edit.mode === "text"} className={`${mod.tab} ${edit.mode === "text" ? mod.tabOn : ""}`} data-testid="filters-mode-text" title="Выражение языка калькулятора 8.7.4; у фильтра с каркасом или поверхностью текста нет" onClick={() => mode("text")}>
              Текст
            </button>
          </div>
          {edit.mode === "builder" ? (
            <RuleEditor draft={edit.draft} onChange={(draft) => patch({ draft, issue: null })} issue={edit.issue} fields={fields} wireframes={options} />
          ) : (
            <div>
              <textarea className={`${css.input} ${mod.text}`} value={edit.text} spellCheck={false} aria-label="Текст выражения 8.7.4" aria-invalid={edit.textIssue !== null} placeholder="AU > 1 AND DOMAIN = 2" data-testid="filters-text" onChange={(e) => typeText(e.target.value)} />
              {edit.textIssue ? (
                <p className={css.error} role="alert" data-testid="filters-text-error">
                  {edit.textIssue.message} {edit.textIssue.hint}
                </p>
              ) : (
                <p className={css.note}>Правила в тексте разбираются на «И / ИЛИ / НЕ» и условия; в конструкторе тот же фильтр без потерь.</p>
              )}
            </div>
          )}
          {edit.issue && edit.mode === "builder" && edit.issue.expr === undefined && (
            <p className={css.error} role="alert" data-testid="filters-issue">
              {edit.issue.message} {edit.issue.hint}
            </p>
          )}
          {reading.length > 0 && <p className={css.note}>Читает поля: {reading.join(", ")}</p>}
          <fieldset className={css.group}>
            <legend>Проверить на модели</legend>
            <div className={css.row}>
              <select className={css.input} style={{ width: "auto", minWidth: 180 }} value={edit.modelId ?? ""} aria-label="Блочная модель" data-testid="filters-model" onChange={(e) => setEdit({ modelId: e.target.value ? Number(e.target.value) : null, test: null })}>
                <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              <button type="button" className={css.btn} disabled={busy || edit.modelId === null || !checked.ok} title={edit.modelId === null ? "Сначала выберите блочную модель" : "Сколько блоков модели проходит фильтр"} data-testid="filters-test" onClick={() => void test()}>
                Проверить на модели
              </button>
            </div>
            {edit.test && (
              <p className={edit.test.kind === "error" ? css.error : edit.test.kind === "notice" ? css.warn : css.note} role={edit.test.kind === "error" ? "alert" : "status"} data-testid="filters-test-result" data-kind={edit.test.kind}>
                {edit.test.text}
              </p>
            )}
          </fieldset>
        </div>
      </div>
      {note && (
        <p className={note.error ? css.error : css.note} style={{ margin: "0 12px" }} role={note.error ? "alert" : "status"} data-testid="filters-note-text">
          {note.text}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!saveReason || (edit.mode === "text" && edit.textIssue !== null)} title={saveReason ?? "Сохранить фильтр в библиотеке проекта"} data-testid="filters-save" onClick={() => void act(() => saveWindow(app))}>
          Сохранить
        </button>
        {stored && (
          <>
            <button type="button" className={css.btn} disabled={busy} title="Объекты и виды, которые используют фильтр" data-testid="filters-usage" onClick={() => void run("filter.usage")}>
              Где используется
            </button>
            <button type="button" className={css.btn} disabled={busy || !!app.registry.reason("filter.delete")} data-testid="filters-delete" onClick={() => void run("filter.delete")}>
              Удалить…
            </button>
          </>
        )}
        <span className={mod.grow} />
        <button
          type="button"
          className={css.btn}
          data-testid="filters-close"
          onClick={() =>
            void (async () => {
              if (!currentEdit()?.dirty || (await askConfirm(CLOSE_QUESTION))) {
                forgetEdit();
                closeWindow(winId);
              }
            })()
          }
        >
          Закрыть
        </button>
      </div>
    </div>
  );
}
