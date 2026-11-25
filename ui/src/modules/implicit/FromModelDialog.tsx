/**
 * The dialog «Модель на скважины» (ТЗ 7.35 Д-34 ч. 1; docs/progress/e19-api.md §8.4): the bodies of an implicit model or closed wireframes cut
 * the holes of a set into a new interval table (`FROM`, `TO`, `CODE`; a gap between the bodies has an empty code, not 0), or the number
 * field of a block model is read at the middle of the intervals of a table. The priority of the bodies is the order of the groups of the
 * model (the first is the youngest) or the order of the wireframes of the list; a code is the name of the group (of the wireframe) unless
 * the person writes another. The table is new: its name is tried before the job is made (409 `table_exists` stands under the field with the
 * free name). «Записать» runs the command `drillholes.from_model`; the window is floating, not modal.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText, num } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { listBlockModels, type BlockModelInfo } from "../blocks/api";
import { loadSets, loadTables, type SetRow, type TableRow } from "../composites/api";
import { sceneObjects } from "../scene/objects";
import { solids } from "../wireframes/state";
import { whenGone } from "./CheckState";
import mod from "./Check.module.css";
import { argsOf, codesLine, fromStore, resetFrom, setFrom, type FromSource } from "./FromModelState";
import { formOfMeta, TYPE_TITLES, type GroupForm } from "./form";
import { useLatest, useSel } from "./hooks";
import { implicitStore, loadDetails } from "./state";

const SOURCES: readonly { id: FromSource; title: string; hint: string }[] = [
  { id: "model", title: "Тела неявной модели", hint: "Тела групп модели по порядку групп: первая — самая молодая" },
  { id: "meshes", title: "Замкнутые каркасы", hint: "Тела каркасов по порядку списка" },
  { id: "field", title: "Поле блочной модели", hint: "Значение числового поля блочной модели в серединах интервалов таблицы" },
];

/** The default name of the new table for a source (the core has the same defaults). */
function defaultName(f: { source: FromSource; modelName: string | null; table: string | null; field: string | null }): string {
  if (f.source === "model") return f.modelName ? `${f.modelName} — на скважинах` : "Модель на скважинах";
  if (f.source === "meshes") return "Каркасы на скважинах";
  return f.table && f.field ? `${f.table} — ${f.field}` : "Поле модели на скважинах";
}

export function FromModelDialog({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(fromStore, (x) => x);
  const models = useSel(implicitStore, (x) => x.rows).filter((r) => r.stats !== null || r.outputs > 0);
  const wires = useSel(sceneObjects, (x) => x.wireframes);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const [problem, setProblem] = useState<string | null>(null);
  const [groups, setGroups] = useState<GroupForm[]>([]);
  const [blocks, setBlocks] = useState<BlockModelInfo[]>([]);
  useEffect(() => whenGone(winId, resetFrom), [winId]);
  const fail = (e: unknown) => setProblem(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(s.setId === null ? null : () => loadTables(s.setId!), [s.setId], fail);
  const closed = solids(wires).filter((w) => w.stats?.closed !== false);
  const model = models.find((m) => m.id === s.modelId) ?? null;
  // the groups of the model: the priority and the names of the codes
  useEffect(() => {
    setGroups([]);
    if (s.source !== "model" || s.modelId === null) return;
    let live = true;
    loadDetails(s.modelId).then(
      (info) => live && info && setGroups(formOfMeta(info.name, info.meta).groups),
      (e: unknown) => live && fail(e),
    );
    return () => {
      live = false;
    };
  }, [s.source, s.modelId, model?.version]);
  useEffect(() => {
    if (s.source !== "field") return;
    let live = true;
    listBlockModels().then(
      (b) => live && setBlocks(b),
      (e: unknown) => live && fail(e),
    );
    return () => {
      live = false;
    };
  }, [s.source]);
  const block = blocks.find((b) => b.id === s.blockId) ?? null;
  const numbers = (block?.fields ?? []).filter((f) => f.kind === "number");
  // the name follows the source until the person writes one
  const name = s.nameTouched ? s.name : defaultName({ source: s.source, modelName: model?.name ?? null, table: s.table, field: s.field });
  const taken = !!name.trim() && !!tables?.some((t) => t.name.trim().toLowerCase() === name.trim().toLowerCase());
  const source: FromSource = s.source;
  const have = source === "model" ? s.modelId !== null : source === "meshes" ? s.meshIds.length > 0 : s.blockId !== null && !!s.field && !!s.table;
  const reason = readOnly ? "Проект открыт только для чтения" : s.setId === null ? "Выберите набор скважин" : !name.trim() ? "Задайте название новой таблицы" : !have ? (source === "model" ? "Выберите модель" : source === "meshes" ? "Выберите замкнутые каркасы" : "Выберите блочную модель, поле и таблицу интервалов") : s.busy ? "Идёт запись" : null;
  const run = () => void app.run("drillholes.from_model", argsOf({ ...s, name })).catch(() => undefined); // a refusal stands in the dialog
  const setCode = (key: number, v: string) => setFrom({ codes: { ...s.codes, [String(key)]: v } });
  const moveMesh = (k: number, d: number) => {
    const ids = [...s.meshIds];
    [ids[k], ids[k + d]] = [ids[k + d], ids[k]];
    setFrom({ meshIds: ids });
  };
  const err = s.error;
  const atName = !!err && (err.param === "name" || err.code === "table_exists");
  return (
    <div className={mod.window} data-testid="implicit-from-model">
      <div className={mod.dialog}>
        {problem && (
          <p className={css.error} role="alert" data-testid="fm-problem">
            {problem}
          </p>
        )}
        <label className={mod.row}>
          <span>Набор скважин</span>
          <select className={mod.select} value={s.setId ?? ""} aria-label="Набор скважин" data-testid="fm-set" disabled={s.busy} onChange={(e) => setFrom({ setId: e.target.value ? Number(e.target.value) : null, table: null })}>
            <option value="">{sets ? "— выберите —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <div className={mod.row}>
          <span>Что записать</span>
          <div className={mod.radios} role="radiogroup" aria-label="Что записать">
            {SOURCES.map((x) => (
              <label key={x.id} title={x.hint}>
                <input type="radio" name="fm-source" checked={s.source === x.id} disabled={s.busy} data-testid={`fm-source-${x.id}`} onChange={() => setFrom({ source: x.id })} />
                {x.title}
              </label>
            ))}
          </div>
        </div>
        {source === "model" && (
          <>
            <label className={mod.row}>
              <span>Неявная модель</span>
              <select className={mod.select} value={s.modelId ?? ""} aria-label="Неявная модель" data-testid="fm-model" disabled={s.busy} onChange={(e) => setFrom({ modelId: e.target.value ? Number(e.target.value) : null, codes: {} })}>
                <option value="">— выберите —</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            {groups.length > 0 && (
              <div className={mod.bodies} data-testid="fm-groups">
                <span className={css.note}>Порядок — приоритет: первая группа самая молодая, её тело вырезается первым. Код по умолчанию — название группы.</span>
                {groups.map((g, k) => (
                  <div key={g.id} className={mod.body}>
                    <span className={mod.no}>{k + 1}</span>
                    <span title={TYPE_TITLES[g.type] ?? g.type}>{g.name}</span>
                    <input className={css.input} value={s.codes[String(g.id)] ?? ""} placeholder={g.name} aria-label={`Код группы «${g.name}»`} data-testid={`fm-code-${g.id}`} disabled={s.busy} onChange={(e) => setCode(g.id, e.target.value)} />
                    <span />
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        {source === "meshes" && (
          <div className={mod.bodies} data-testid="fm-meshes">
            <span className={css.note}>Отметьте замкнутые каркасы; порядок — приоритет (первый вырезается первым). Код по умолчанию — название каркаса.</span>
            {closed.map((w) => (
              <label key={w.id} className={css.check}>
                <input type="checkbox" checked={s.meshIds.includes(w.id)} disabled={s.busy} data-testid={`fm-mesh-${w.id}`} onChange={(e) => setFrom({ meshIds: e.target.checked ? [...s.meshIds, w.id] : s.meshIds.filter((x) => x !== w.id) })} />
                {w.name}
              </label>
            ))}
            {!closed.length && <span className={css.note}>В проекте нет замкнутых каркасов.</span>}
            {s.meshIds.map((id, k) => (
              <div key={id} className={mod.body}>
                <span className={mod.no}>{k + 1}</span>
                <span>{wires.find((w) => w.id === id)?.name ?? `№${id}`}</span>
                <input className={css.input} value={s.codes[String(id)] ?? ""} placeholder={wires.find((w) => w.id === id)?.name ?? ""} aria-label={`Код каркаса №${id}`} data-testid={`fm-meshcode-${id}`} disabled={s.busy} onChange={(e) => setCode(id, e.target.value)} />
                <span className={css.row}>
                  <button type="button" className={css.iconBtn} aria-label="Выше" title="Выше: вырезается раньше" disabled={k === 0 || s.busy} data-testid={`fm-up-${id}`} onClick={() => moveMesh(k, -1)}>
                    ↑
                  </button>
                  <button type="button" className={css.iconBtn} aria-label="Ниже" title="Ниже: вырезается позже" disabled={k === s.meshIds.length - 1 || s.busy} data-testid={`fm-down-${id}`} onClick={() => moveMesh(k, 1)}>
                    ↓
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
        {source === "field" && (
          <>
            <label className={mod.row}>
              <span>Блочная модель</span>
              <select className={mod.select} value={s.blockId ?? ""} aria-label="Блочная модель" data-testid="fm-block" disabled={s.busy} onChange={(e) => setFrom({ blockId: e.target.value ? Number(e.target.value) : null, field: null })}>
                <option value="">— выберите —</option>
                {blocks.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={mod.row}>
              <span>Числовое поле</span>
              <select className={mod.select} value={s.field ?? ""} disabled={!block || s.busy} aria-label="Числовое поле" data-testid="fm-field" onChange={(e) => setFrom({ field: e.target.value || null })}>
                <option value="">{block ? "— выберите —" : "—"}</option>
                {numbers.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name}
                    {f.unit ? `, ${f.unit}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className={mod.row}>
              <span>Таблица интервалов</span>
              <select className={mod.select} value={s.table ?? ""} disabled={s.setId === null || s.busy} aria-label="Таблица интервалов" data-testid="fm-table" onChange={(e) => setFrom({ table: e.target.value || null })}>
                <option value="">{s.setId === null ? "—" : tables ? "— выберите —" : "Чтение…"}</option>
                {(tables ?? []).map((t) => (
                  <option key={t.table} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <label className={mod.row}>
          <span>Название новой таблицы</span>
          <input className={css.input} value={name} aria-label="Название новой таблицы" aria-invalid={taken || atName} data-testid="fm-name" disabled={s.busy} onChange={(e) => setFrom({ name: e.target.value, nameTouched: true })} />
        </label>
        {(taken || atName) && (
          <div className={mod.suggest} data-testid="fm-name-taken">
            <span className={css.error} role="alert">
              {err && atName ? `${err.message} ${err.hint}` : `В наборе уже есть таблица «${name.trim()}»: запись не заменяет таблицы.`}
            </span>
            {s.suggested && (
              <button type="button" className={css.btn} data-testid="fm-suggested" onClick={() => setFrom({ name: s.suggested as string, nameTouched: true })}>
                Взять «{s.suggested}»
              </button>
            )}
          </div>
        )}
        {err && !atName && (
          <p className={css.error} role="alert" data-testid="fm-error" data-code={err.code}>
            {err.message} {err.hint}
          </p>
        )}
        {s.busy && (
          <p className={css.note} role="status" data-testid="fm-progress">
            {s.stage ?? "Запись"}… ход — во вкладке «Задания».
          </p>
        )}
        {s.result && (
          <div className={mod.bodies} role="status" data-testid="fm-result">
            <b>Таблица «{s.result.name}» записана в набор.</b>
            <span>
              Строк {num(s.result.rows, 0)}, скважин {num(s.result.holes, 0)}
              {s.result.no_trajectory ? `, без траектории ${num(s.result.no_trajectory, 0)} (в таблицу не вошли)` : ""}
              {s.result.length_inside !== null && s.result.length !== null ? `; внутри тел ${num(s.result.length_inside, 0)} м из ${num(s.result.length, 0)} м` : ""}
            </span>
            {s.result.codes.length > 0 && <span data-testid="fm-codes">{codesLine(s.result)}</span>}
            {s.result.journal.map((j) => (
              <span key={j} className={css.note}>
                {j}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className={mod.footer}>
        <div className={css.row}>
          <span className={css.note} style={{ flex: 1 }}>
            {reason ?? "Интервалы между телами получают пустой код, а не 0."}
          </span>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Записать новую интервальную таблицу в набор (существующие таблицы не меняются)"} data-testid="fm-run" onClick={run}>
            Записать
          </button>
          <button type="button" className={css.btn} data-testid="fm-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
