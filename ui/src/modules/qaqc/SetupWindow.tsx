/**
 * The floating window «Настройка QAQC» (ADR 0020 §11): the tabs «Данные», «Типы», «CRM», «DL», «Пороги». The tab
 * lives in the window's props, so `qaqc.setup {tab}` turns an open window to it. Tabs with unsaved edits keep them
 * while another tab is shown; the cross asks before they are lost (our own window).
 */
import { useRef, type ReactNode } from "react";
import { askConfirm } from "../../shell/dialogs";
import { setCloseGuard, updateWindow, windowStore } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import own from "./setup/Setup.module.css";
import { CrmTab } from "./setup/CrmTab";
import { DataTab } from "./setup/DataTab";
import { DlTab } from "./setup/DlTab";
import { LimitsTab } from "./setup/LimitsTab";
import { TypesTab } from "./setup/TypesTab";

export type SetupTab = "data" | "types" | "crm" | "dl" | "limits";

export const SETUP_TABS: { id: SetupTab; title: string }[] = [
  { id: "data", title: "Данные" },
  { id: "types", title: "Типы" },
  { id: "crm", title: "CRM" },
  { id: "dl", title: "DL" },
  { id: "limits", title: "Пороги" },
];

/** The edits not saved yet, by tab: the window's cross asks before closing. */
export type DirtyMap = Partial<Record<SetupTab, boolean>>;

export function setTab(winId: string, tab: SetupTab): void {
  const w = windowStore.get().windows.find((x) => x.id === winId);
  updateWindow(winId, { props: { ...w?.props, tab } });
}

function useDirty(winId: string) {
  const dirty = useRef<DirtyMap>({});
  const mark = (tab: SetupTab) => (on: boolean) => {
    if (dirty.current[tab] === on) return;
    dirty.current = { ...dirty.current, [tab]: on };
    const any = Object.values(dirty.current).some(Boolean);
    setCloseGuard(winId, any ? () => askConfirm({ title: "Закрыть «Настройка QAQC»", message: "Есть несохранённые правки настройки. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }) : null);
  };
  return mark;
}

export function SetupWindow({ winId, tab }: { winId: string; tab: SetupTab }) {
  const mark = useDirty(winId);
  const body = (id: SetupTab): ReactNode => {
    if (id === "data") return <DataTab />;
    const onDirty = mark(id);
    if (id === "types") return <TypesTab onDirty={onDirty} />;
    if (id === "crm") return <CrmTab onDirty={onDirty} />;
    if (id === "dl") return <DlTab onDirty={onDirty} />;
    return <LimitsTab onDirty={onDirty} />;
  };
  return (
    <div className={css.win} data-testid="qaqc-setup">
      <div className={own.tabs} role="tablist" aria-label="Разделы настройки QAQC">
        {SETUP_TABS.map((t) => (
          <button key={t.id} type="button" role="tab" className={own.tab} aria-selected={t.id === tab} data-testid={`qaqc-setup-tab-${t.id}`} onClick={() => setTab(winId, t.id)}>
            {t.title}
          </button>
        ))}
      </div>
      {SETUP_TABS.map((t) => (
        // every tab stays mounted: unsaved edits survive a switch of tabs
        <div key={t.id} role="tabpanel" aria-label={t.title} hidden={t.id !== tab} style={{ display: t.id === tab ? "contents" : "none" }}>
          {body(t.id)}
        </div>
      ))}
    </div>
  );
}
