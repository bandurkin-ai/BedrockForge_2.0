import { useState } from "react";
import { getStyleUsage, type StyleUsage } from "../../api";
import type { StyleRef } from "../../charts/stripLog";
import { LEVEL_TITLES, type StyleLevel, type StyleRec } from "./styles";
import { StyleGallery } from "./StyleGallery";
import { MenuItem, Popover } from "./ui";
import { askConfirm, askText } from "../../shell/dialogs";

/** What the form gives every style bar: the library and the actions on it. */
export interface StyleLib {
  styles: StyleRec[];
  /** removes a style from the library; displays linked to it keep a copy of its settings */
  remove: (style: StyleRec) => Promise<void>;
  rename: (style: StyleRec, name: string) => Promise<void>;
  setDefaultFor: (style: StyleRec, patterns: string[]) => Promise<void>;
  exportFile: (ids?: number[]) => Promise<void>;
  importFile: () => void;
  openDisplay: (chartId: number) => void;
  say: (text: string, error?: boolean) => void;
}

/**
 * The style of a level (a colouring, a layer style or a display template): a gallery to take one from,
 * the link switch, and the menu to save, rename, apply elsewhere, export, import and delete.
 */
export function StyleBar({ level, lib, options, current, fieldName, onApply, onSaveAs, onUpdate, onLink, onApplyTo, onClear }: {
  level: StyleLevel;
  lib: StyleLib;
  /** the styles that fit what is edited */
  options: StyleRec[];
  current: StyleRef | undefined;
  /** the field of the layer: offered as the name of a default style */
  fieldName?: string;
  onApply: (style: StyleRec) => void;
  /** saves the current settings as a style with this name */
  onSaveAs: (name: string) => Promise<void>;
  /** writes the current settings into the source style */
  onUpdate: () => Promise<void>;
  onLink: (linked: boolean) => void;
  onApplyTo?: (style: StyleRec) => void;
  /** forgets where the settings came from */
  onClear: () => void;
}) {
  const [usage, setUsage] = useState<StyleUsage[] | null>(null);
  const source = current ? lib.styles.find((s) => s.id === current.id) : undefined;
  const own = source && !source.builtin ? source : undefined;
  const run = (p: Promise<unknown>) => void p.catch((e: Error) => lib.say(e.message, true));
  const title = LEVEL_TITLES[level];
  const saveAs = async () => {
    const name = await askText({ title: `${title}: сохранить как`, message: "Название в библиотеке проекта", value: current?.name ?? (fieldName ? `${fieldName}` : "") });
    if (!name) return;
    if (lib.styles.some((s) => s.level === level && !s.builtin && s.name === name)
      && !(await askConfirm({ title: "Такое название уже есть", message: `«${name}» уже есть в библиотеке. Заменить?`, ok: "Заменить", danger: true }))) return;
    run(onSaveAs(name));
  };
  return (
    <div className="dh-stylebar">
      <Popover label={`${title}: выбрать`} title={`${title}: выбрать из библиотеки. Настройки копируются; значок связи рядом делает стиль общим`} className="dh-style-pick"
        button={<><span className="grow">{current ? current.name : `${title}: свои настройки`}</span>{current?.modified && <em className="dh-modified">изменён</em>}<span aria-hidden>▾</span></>}>
        {(close) => <StyleGallery styles={options} currentId={current?.id} onPick={(s) => { onApply(s); close(); }} />}
      </Popover>
      <button type="button" className={`icon dh-link${current?.linked ? " active" : ""}`} aria-pressed={!!current?.linked} disabled={!own}
        title={!own ? "Связать можно со стилем из библиотеки проекта: выберите или сохраните стиль" : current?.linked ? "Связан: настройки берутся из стиля. Нажмите, чтобы отвязать (останется копия)" : "Копия. Нажмите, чтобы связать: правка стиля будет менять все отображения, где он связан"}
        aria-label={`${title}: ${current?.linked ? "отвязать" : "связать"}`} onClick={() => onLink(!current?.linked)}>🔗</button>
      <Popover label={`${title}: действия`} title="Сохранить, применить к другим, экспорт и импорт" className="icon" button="⋯" align="right">
        {(close) => (
          <div className="dh-pop-form">
            <MenuItem disabled={!own} title={own ? `Записать текущие настройки в «${own.name}»` : "Нет стиля-источника в библиотеке"} onClick={() => { run(onUpdate()); close(); }}>Обновить стиль{own ? ` «${own.name}»` : ""}</MenuItem>
            <MenuItem onClick={() => { close(); void saveAs(); }}>Сохранить как…</MenuItem>
            <MenuItem disabled={!own} onClick={async () => {
              close();
              const name = own && (await askText({ title: "Переименовать стиль", value: own.name }));
              if (own && name && name !== own.name) run(lib.rename(own, name));
            }}>Переименовать…</MenuItem>
            {level === "layer" && (
              <MenuItem disabled={!own} title="Стиль сам применяется при добавлении слоя с полем с таким именем" onClick={async () => {
                close();
                if (!own) return;
                const text = await askText({
                  title: "Стиль по умолчанию для полей", allowEmpty: true,
                  message: "Имена полей через запятую, для которых стиль применяется сам (можно со звёздочкой: AU*). Пусто — ни для каких",
                  value: own.default_for.length ? own.default_for.join(", ") : fieldName ?? "",
                });
                if (text !== null) run(lib.setDefaultFor(own, text.split(",").map((x) => x.trim()).filter(Boolean)));
              }}>Сделать стилем по умолчанию для поля…</MenuItem>
            )}
            {onApplyTo && <MenuItem disabled={!source} title="Создать отображения с этим стилем для других наборов, таблиц и полей" onClick={() => { close(); if (source) onApplyTo(source); }}>Применить к…</MenuItem>}
            <MenuItem disabled={!own} onClick={() => { close(); if (own) run(getStyleUsage(own.id).then(setUsage)); }}>Где используется</MenuItem>
            {current && <MenuItem title="Настройки остаются, связь с источником забывается" onClick={() => { onClear(); close(); }}>Отвязать от источника</MenuItem>}
            <hr />
            <MenuItem disabled={!own} onClick={() => { close(); if (own) run(lib.exportFile([own.id])); }}>Экспорт стиля в файл</MenuItem>
            <MenuItem onClick={() => { close(); run(lib.exportFile()); }}>Экспорт всей библиотеки</MenuItem>
            <MenuItem onClick={() => { close(); lib.importFile(); }}>Импорт из файла…</MenuItem>
            <hr />
            <MenuItem danger disabled={!own} onClick={() => {
              close();
              if (!own) return;
              run(getStyleUsage(own.id).then(async (u) => {
                const linked = u.filter((x) => x.linked);
                const text = linked.length
                  ? `Стиль «${own.name}» связан с отображениями:\n${linked.map((x) => `• ${x.name}`).join("\n")}\n\nОни получат копию настроек и будут отвязаны. Удалить стиль?`
                  : `Удалить стиль «${own.name}» из библиотеки проекта?`;
                return (await askConfirm({ title: "Удаление стиля", message: text, danger: true })) ? lib.remove(own) : undefined;
              }));
            }}>Удалить из библиотеки</MenuItem>
          </div>
        )}
      </Popover>
      {usage && (
        <div className="dh-usage" role="dialog" aria-label="Где используется стиль">
          <div className="dh-usage-head"><strong className="grow">Где используется «{own?.name ?? current?.name}»</strong><button type="button" className="icon" aria-label="Закрыть" onClick={() => setUsage(null)}>✕</button></div>
          {usage.length === 0 && <span className="muted small">Ни в одном сохранённом отображении.</span>}
          {usage.map((u) => (
            <button key={u.chart_id} type="button" className="dh-menu-item" title="Открыть это отображение" onClick={() => { lib.openDisplay(u.chart_id); setUsage(null); }}>
              {u.name} <span className="muted small">{u.linked ? "· связан" : "· копия"}{u.tracks.length ? ` · ${u.tracks.join(", ")}` : ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
