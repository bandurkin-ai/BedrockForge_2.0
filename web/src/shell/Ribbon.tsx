import { formatShortcut } from "./commands";
import { Icon } from "./icons";
import type { RibbonItem, RibbonTab } from "./ribbonConfig";

function tooltip(item: RibbonItem): string {
  const text = item.hint ?? item.label;
  return item.shortcut ? `${text} (${formatShortcut(item.shortcut)})` : text;
}

function RibbonButton({ item, onRun }: { item: RibbonItem; onRun: (item: RibbonItem) => void }) {
  return (
    <button
      className={`rb ${item.small ? "rb-sm" : "rb-lg"}${item.active ? " active" : ""}`}
      title={tooltip(item)}
      aria-pressed={item.active}
      disabled={item.disabled}
      onClick={() => onRun(item)}
    >
      <Icon name={item.icon} size={item.small ? 16 : 24} />
      <span>{item.label}</span>
    </button>
  );
}

export function Ribbon({
  tabs,
  activeTab,
  collapsed,
  onTab,
  onToggleCollapsed,
  onRun,
}: {
  tabs: RibbonTab[];
  activeTab: string;
  collapsed: boolean;
  onTab: (id: string) => void;
  onToggleCollapsed: () => void;
  onRun: (item: RibbonItem) => void;
}) {
  const tab = tabs.find((t) => t.id === activeTab) ?? tabs[0];
  return (
    <div className={collapsed ? "ribbon collapsed" : "ribbon"}>
      <div className="ribbon-tabs" role="tablist" aria-label="Лента инструментов">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={!collapsed && t.id === tab.id}
            className={!collapsed && t.id === tab.id ? "ribbon-tab active" : "ribbon-tab"}
            onClick={() => onTab(t.id)}
            onDoubleClick={onToggleCollapsed}
          >
            {t.title}
          </button>
        ))}
        <span className="spacer" />
        <button
          className="icon"
          onClick={onToggleCollapsed}
          title={collapsed ? "Развернуть ленту (Ctrl+F1)" : "Свернуть ленту (Ctrl+F1)"}
          aria-label={collapsed ? "Развернуть ленту" : "Свернуть ленту"}
        >
          <Icon name={collapsed ? "chevronDown" : "chevronUp"} size={16} />
        </button>
      </div>
      {!collapsed && (
        <div className="ribbon-body" role="tabpanel">
          {tab.groups.map((group) => {
            const large = group.items.filter((i) => !i.small);
            const small = group.items.filter((i) => i.small);
            return (
              <section className="ribbon-group" key={group.title} aria-label={group.title}>
                <div className="ribbon-items">
                  {large.map((item) => <RibbonButton key={item.label} item={item} onRun={onRun} />)}
                  {small.length > 0 && (
                    <div className="ribbon-stack">
                      {small.map((item) => <RibbonButton key={item.label} item={item} onRun={onRun} />)}
                    </div>
                  )}
                </div>
                <div className="ribbon-caption">{group.title}</div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
