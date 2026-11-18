/**
 * What a row of an object tree offers (ТЗ 7.1, 7.0.13): its context menu and its buttons as data, so the
 * sections «Линии», «Растры», «Каркасы» and «ЦТМ» are built — and tested — without React. An action is a
 * registry command with its arguments, or a local one of the tree (rename, delete: own dialogs, then the API).
 */

export interface Confirm {
  title: string;
  message: string;
  okLabel: string;
  danger?: boolean;
}

export interface TreeAction {
  label: string;
  /** registry command (the menu item is disabled with the command's reason while it is unavailable) */
  command?: string;
  args?: Record<string, unknown>;
  /** an action of the tree itself */
  local?: "properties" | "rename" | "delete";
  /** asked in an own dialog before the command runs */
  confirm?: Confirm;
  separator?: boolean;
}

export const SEPARATOR: TreeAction = { label: "", separator: true };

/** «1 234» — thousands by a thin space, as everywhere in the interface. */
export const count = (n: number): string => n.toLocaleString("ru-RU");

/** Russian plural: plural(21, "слой", "слоя", "слоёв") → «слой». */
export function plural(n: number, one: string, few: string, many: string): string {
  const d = Math.abs(n) % 100;
  const u = d % 10;
  if (d > 10 && d < 20) return many;
  if (u === 1) return one;
  return u >= 2 && u <= 4 ? few : many;
}

/** «12 линий» */
export const counted = (n: number, one: string, few: string, many: string): string => `${count(n)} ${plural(n, one, few, many)}`;
