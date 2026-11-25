/**
 * The tab «Показ» of the window «Блочная модель · …» (ТЗ 8.7.6, 6.8; ADR 0023 §11, decisions 16–19): «Показать в 3D» and
 * «Оболочка / Все блоки», the field of the colouring with its scale (from, to, a logarithm, «Взять из данных»), the breaks
 * of the classes or the categories with their colours and «показать», the palette that fills the colours, the edges, the
 * opacity, the limit of faces, and what the display says now (the faces, «упрощено, LOD», the time of the last request).
 * Every control writes the style of the model into the view of the project and takes effect at once (`edits.ts`): the
 * colours and the look never ask the core, the field, the range, the breaks, the hidden categories, the mode and the limit
 * do. A wrong value is not written; the field tells why, in Russian, with a hint.
 */
import { useApp } from "../../../shell/AppContext";
import { Check, ColorField, Slider } from "../../../shell/PropsParts";
import css from "../../../shell/PropsParts.module.css";
import { showText } from "../display";
import mod from "./Window.module.css";
import { showModel } from "./actions";
import {
  commitBound,
  commitBreaks,
  commitLimit,
  commitWidth,
  edgesChoice,
  resetRange,
  setCategoriesShown,
  setCategory,
  setClassColour,
  setEdgeColour,
  setEdges,
  setField,
  setKind,
  setLog,
  setMode,
  setOpacity,
  setPalette,
  takeRange,
  type EdgesChoice,
} from "./edits";
import { useInfo, useModel, useStyle } from "./hooks";
import { paletteById, paletteCss, palettesFor } from "./palettes";
import { Issue, TextField, useIssues } from "./parts";
import { breaksText, categoryRows, classRows, classText, colourIssueAt, effectiveKind, limitText, lodText, paletteShown, plain, rangeText, takeReason, timeText, type FormField, type FormIssue } from "./style";

export function StylePanel({ id }: { id: number }) {
  const app = useApp();
  const style = useStyle(id);
  const { model } = useModel(id);
  const info = useInfo(id);
  const { issues, apply } = useIssues();
  const c = style.colour;
  const field = model?.fields.find((f) => f.name === c.field);
  const kind = effectiveKind(c, field);
  const stored = colourIssueAt(c, model);
  const say = (f: FormField): string | undefined => issues[f] ?? (stored?.field === f ? stored.text : undefined);
  const labels = info?.header?.field === c.field ? info?.header?.labels : null;
  const take = takeReason(c, field, info);
  const palettes = palettesFor(kind === "none" ? "scale" : kind);
  const shown = paletteShown(c);
  const palette = palettes.some((p) => p.id === shown) ? shown : (palettes[0]?.id ?? shown);
  const showReason = app.registry.reason("blocks.show");
  const statusText = info ? showText(info, model?.name ?? `№${id}`) : style.visible ? "Запрос показа…" : "Модель не показана в 3D.";
  const lod = lodText(info);
  const time = timeText(info);
  const run = (f: FormField, r: FormIssue | null): void => apply(f, r);
  /** For the parts that go back to the stored value when the change was refused (`Check`, `ColorField`, `Slider`). */
  const commit = (f: FormField, r: FormIssue | null): Promise<boolean> => {
    apply(f, r);
    return Promise.resolve(r === null);
  };

  const toggleShow = async () => {
    const bad = await showModel(app, id, !style.visible, style.mode);
    apply("general", bad ? { field: "general", text: bad } : null);
  };

  return (
    <div className={mod.panel} data-testid="style-panel">
      <fieldset className={css.group}>
        <legend>Показ в 3D</legend>
        <div className={css.row}>
          <button type="button" className={style.visible ? css.btn : css.primary} disabled={showReason !== null} title={showReason ? `Недоступно: ${showReason}` : style.visible ? "Убрать модель из 3D" : "Показать модель в 3D"} data-testid="style-show" aria-pressed={style.visible} onClick={() => void toggleShow()}>
            {style.visible ? "Скрыть" : "Показать в 3D"}
          </button>
          <div className={mod.seg} role="radiogroup" aria-label="Режим показа">
            <button type="button" role="radio" aria-checked={style.mode === "shell"} aria-pressed={style.mode === "shell"} title="Только грани блоков, видимые снаружи" data-testid="style-mode-shell" onClick={() => apply("general", setMode(id, "shell"))}>
              Оболочка
            </button>
            <button type="button" role="radio" aria-checked={style.mode === "all"} aria-pressed={style.mode === "all"} title="Каждая ячейка с рёбрами, до лимита граней" data-testid="style-mode-all" onClick={() => apply("general", setMode(id, "all"))}>
              Все блоки
            </button>
          </div>
        </div>
        <p className={info?.status === "error" ? css.error : css.note} data-testid="style-status" data-state={info?.status ?? (style.visible ? "loading" : "off")}>
          {statusText}
        </p>
        {lod && (
          <p className={css.warn} data-testid="style-lod" title="Блоки показаны крупнее, чем они есть: родители 2×2×2, 4×4×4… Увеличьте лимит граней или ограничьте показ фильтром.">
            {lod}
          </p>
        )}
        {info?.warnings.map((w) => (
          <p key={w} className={css.warn}>
            {w}
          </p>
        ))}
        {time && (
          <p className={css.note} data-testid="style-time">
            {time}
          </p>
        )}
        <Issue text={issues.general ?? (stored?.field === "general" ? stored.text : undefined)} testId="style-issue-general" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Раскраска</legend>
        <label className={css.field}>
          <span>Поле</span>
          <select className={css.input} value={c.field ?? ""} aria-label="Поле раскраски" aria-invalid={!!say("field") || undefined} data-testid="style-field" disabled={!model} onChange={(e) => run("field", setField(id, model, e.target.value || null))}>
            <option value="">— без раскраски —</option>
            {c.field && !field && <option value={c.field}>{c.field} (нет в модели)</option>}
            {model?.fields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
                {f.unit ? `, ${f.unit}` : ""}
                {f.kind === "category" ? " — категории" : ""}
              </option>
            ))}
          </select>
        </label>
        <Issue text={say("field")} testId="style-issue-field" />

        {c.field && field?.kind === "number" && (
          <div className={css.row}>
            <div className={mod.seg} role="radiogroup" aria-label="Вид раскраски">
              <button type="button" role="radio" aria-checked={kind === "scale"} aria-pressed={kind === "scale"} data-testid="style-kind-scale" onClick={() => run("field", setKind(id, model, "scale"))}>
                Шкала
              </button>
              <button type="button" role="radio" aria-checked={kind === "classes"} aria-pressed={kind === "classes"} data-testid="style-kind-classes" onClick={() => run("field", setKind(id, model, "classes"))}>
                Градации
              </button>
            </div>
          </div>
        )}

        {kind === "scale" && (
          <div className={mod.block} data-testid="style-scale">
            <div className={css.pair}>
              <label className={css.field}>
                <span>От</span>
                <TextField value={c.from === null ? "" : plain(c.from)} placeholder="минимум поля" label="Граница шкалы от" testId="style-from" invalid={!!say("from")} onCommit={(t) => run("from", commitBound(id, model, "from", t))} />
              </label>
              <label className={css.field}>
                <span>До</span>
                <TextField value={c.to === null ? "" : plain(c.to)} placeholder="максимум поля" label="Граница шкалы до" testId="style-to" invalid={!!say("to")} onCommit={(t) => run("to", commitBound(id, model, "to", t))} />
              </label>
            </div>
            <Issue text={say("from")} testId="style-issue-from" />
            <Issue text={say("to")} testId="style-issue-to" />
            <div className={css.row}>
              <Check checked={c.log} title="Логарифмическая шкала: границы больше нуля" onCommit={(v) => commit("from", setLog(id, model, v))}>
                Логарифмическая
              </Check>
              <button type="button" className={css.btn} disabled={take !== null} title={take ?? "Взять границы, которые ядро выбрало по данным поля"} data-testid="style-take" onClick={() => run("from", takeRange(id, model, info))}>
                Взять из данных
              </button>
              <button type="button" className={css.btn} disabled={c.from === null && c.to === null} title="Пустые границы: ядро возьмёт минимум и максимум поля" data-testid="style-reset" onClick={() => run("from", resetRange(id))}>
                Сбросить
              </button>
            </div>
            {rangeText(info) && info?.header?.field === c.field && (
              <p className={css.note} data-testid="style-range">
                Сейчас шкала: {rangeText(info)}.
              </p>
            )}
          </div>
        )}

        {kind === "classes" && (
          <div className={mod.block} data-testid="style-classes">
            <label className={css.field}>
              <span>Границы градаций, через «;»</span>
              <TextField value={breaksText(c.breaks)} placeholder="0,5; 1; 2" label="Границы градаций" testId="style-breaks" invalid={!!say("breaks")} onCommit={(t) => run("breaks", commitBreaks(id, model, t))} />
            </label>
            <Issue text={say("breaks")} testId="style-issue-breaks" />
            <ul className={mod.rows} aria-label="Градации">
              {classRows(c).map((r) => (
                <li key={r.k} data-testid={`style-class-${r.k}`}>
                  <ColorField value={r.colour} label={`Цвет градации ${r.k + 1}`} onCommit={(v) => commit("general", setClassColour(id, r.k, v))} />
                  <span>{classText(r)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {kind === "categories" && (
          <div className={mod.block} data-testid="style-categories">
            <div className={css.row}>
              <button type="button" className={css.btn} data-testid="style-cats-all" onClick={() => run("general", setCategoriesShown(id, categoryRows(field, labels, c).map((r) => r.code), true))}>
                Показать все
              </button>
              <button type="button" className={css.btn} data-testid="style-cats-none" onClick={() => run("general", setCategoriesShown(id, categoryRows(field, labels, c).map((r) => r.code), false))}>
                Скрыть все
              </button>
            </div>
            <ul className={mod.rows} aria-label="Категории">
              {categoryRows(field, labels, c).map((r) => (
                <li key={r.code} data-testid={`style-cat-${r.code}`} data-on={r.on}>
                  <ColorField value={r.colour} label={`Цвет категории ${r.label}`} onCommit={(v) => commit("general", setCategory(id, r.code, { colour: v }))} />
                  <Check checked={r.on} title="Снимите флажок, чтобы скрыть блоки этой категории" onCommit={(v) => commit("general", setCategory(id, r.code, { on: v }))}>
                    <span>{r.label}</span>
                  </Check>
                  {r.blocks !== null && <span className={css.note}>{r.blocks.toLocaleString("ru-RU")} блоков</span>}
                  {r.custom && (
                    <button type="button" className={css.iconBtn} title="Вернуть цвет по умолчанию" aria-label={`Цвет по умолчанию: ${r.label}`} onClick={() => run("general", setCategory(id, r.code, { colour: null }))}>
                      ↺
                    </button>
                  )}
                </li>
              ))}
              {!categoryRows(field, labels, c).length && <li className={css.note}>У поля нет категорий.</li>}
            </ul>
          </div>
        )}

        {kind !== "none" && (
          <label className={css.field}>
            <span>Палитра</span>
            <div className={css.row}>
              <select className={css.input} style={{ width: "auto", flex: 1 }} value={palette} aria-label="Палитра" data-testid="style-palette" onChange={(e) => run("general", setPalette(id, model, e.target.value))}>
                {palettes.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
              <span className={mod.strip} style={{ background: paletteCss(palette) }} title={paletteById(palette)?.title} data-testid="style-palette-strip" />
            </div>
          </label>
        )}
        {c.kind === "none" && !c.field && <p className={css.note}>Без раскраски блоки показаны одним цветом.</p>}
      </fieldset>

      <fieldset className={css.group}>
        <legend>Вид</legend>
        <div className={css.row}>
          <label className={css.field} style={{ maxWidth: 190 }}>
            <span>Рёбра</span>
            <select className={css.input} value={edgesChoice(style)} aria-label="Рёбра" data-testid="style-edges" onChange={(e) => run("edge_width", setEdges(id, e.target.value as EdgesChoice))}>
              <option value="mode">по режиму показа</option>
              <option value="on">всегда</option>
              <option value="off">нет</option>
            </select>
          </label>
          <label className={css.field} style={{ maxWidth: 70 }}>
            <span>Цвет</span>
            <ColorField value={style.edges.colour ?? "#1b222b"} label="Цвет рёбер" onCommit={(v) => commit("general", setEdgeColour(id, v))} />
          </label>
          <label className={css.field} style={{ maxWidth: 90 }}>
            <span>Толщина, пкс</span>
            <TextField value={style.edges.width === null ? "" : plain(style.edges.width)} placeholder="по умолч." label="Толщина рёбер" testId="style-edge-width" invalid={!!say("edge_width")} onCommit={(t) => run("edge_width", commitWidth(id, t))} />
          </label>
        </div>
        <Issue text={say("edge_width")} testId="style-issue-edge_width" />
        <div className={css.row}>
          <span className={css.note}>Непрозрачность</span>
          <Slider value={style.opacity} min={0.05} max={1} step={0.05} label="Непрозрачность" onCommit={(v) => commit("general", setOpacity(id, v))} />
        </div>
        <label className={css.field} style={{ maxWidth: 260 }}>
          <span>Лимит граней, млн (0,5–10)</span>
          <TextField value={limitText(style.limit)} label="Лимит граней в миллионах" testId="style-limit" invalid={!!say("limit")} title="Если граней больше, модель показана упрощённо: родители 2×2×2, 4×4×4…" onCommit={(t) => run("limit", commitLimit(id, t))} />
        </label>
        <Issue text={say("limit")} testId="style-issue-limit" />
      </fieldset>
    </div>
  );
}
